import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.engine import BackupEngine
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History, read_history
from raspi_cloud_sync_pro.network import Connectivity
from raspi_cloud_sync_pro.staging import stage_snapshot
from test_storage import staging_owner


class MemoryProvider:
    """Remote byte store for real hash comparisons, never a cloud account."""
    destination_id = "test/remote:backup"

    def __init__(self):
        self.remote = {}
        self.uploads = 0
        self.corrupt = False

    def upload(self, batch, item):
        self.uploads += 1
        self.remote[item.remote_key] = (batch.root / "files" / item.relative).read_bytes()

    def verify(self, batch, item):
        content = b"corrupt" if self.corrupt else self.remote[item.remote_key]
        if hashlib.sha256(content).hexdigest() != item.sha256:
            raise BackupError("verification_mismatch", retryable=True)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "exclusive-fixture"
        self.source.mkdir()
        (self.source / "recording.txt").write_bytes(b"important recording")
        self.batch = stage_snapshot(self.source, self.base / "staging", staging_owner())
        self.path = self.base / "history.sqlite3"
        self.history = History(self.path)
        self.addCleanup(lambda: self.history.close())
        self.provider = MemoryProvider()
        self.history.enqueue(self.batch, self.provider.destination_id, 100)
        self.engine = BackupEngine(self.history, base_delay=10, max_delay=15)

    def run_job(self, now=100, **kwargs):
        return self.engine.run_once(self.provider, now=now, connectivity=Connectivity.ONLINE, **kwargs)

    def test_uploaded_and_verified_are_distinct_persistent_events(self):
        self.assertEqual(self.run_job(), "verified")
        job = self.history.jobs()[0]
        self.assertEqual(job["state"], "verified")
        self.assertEqual([e["state"] for e in self.history.events(job["id"])],
                         ["queued", "uploading", "uploaded", "verifying", "verified"])
        self.assertEqual(read_history(self.path)[0]["sha256"], self.batch.files[0].sha256)
        self.assertEqual((self.source / "recording.txt").read_bytes(), b"important recording")
        self.assertTrue((self.batch.root / "files/recording.txt").exists())
        self.assertIsNone(self.run_job())

    def test_hash_mismatch_retries_with_capped_backoff_then_stops(self):
        self.provider.corrupt = True
        self.assertEqual(self.run_job(100), "retry")
        self.assertEqual(self.history.jobs()[0]["next_attempt"], 110)
        self.assertIsNone(self.run_job(109))
        self.assertEqual(self.run_job(110), "retry")
        self.assertEqual(self.history.jobs()[0]["next_attempt"], 125)
        self.assertEqual(self.run_job(125), "failed")
        self.assertIsNone(self.run_job(1000))
        job = self.history.jobs()[0]
        self.assertEqual(job["attempts"], 3)
        self.assertNotIn("verified", [e["state"] for e in self.history.events(job["id"])])
        self.assertTrue((self.batch.root / "files/recording.txt").exists())

    def test_transient_upload_failure_can_recover_without_duplicate_job(self):
        with mock.patch.object(self.provider, "upload", side_effect=BackupError("temporary", True)):
            self.assertEqual(self.run_job(), "retry")
        self.assertEqual(self.run_job(110), "verified")
        self.assertEqual(len(self.history.jobs()), 1)
        self.assertEqual(self.history.jobs()[0]["attempts"], 2)

    def test_permanent_and_unexpected_errors_do_not_retry_or_leak_text(self):
        with mock.patch.object(self.provider, "upload", side_effect=RuntimeError("password=secret")):
            self.assertEqual(self.run_job(), "failed")
        self.assertEqual(self.history.jobs()[0]["error_code"], "unexpected_provider_error")
        self.assertNotIn("secret", json.dumps(self.history.events(1)))

    def test_duplicate_snapshot_is_not_requeued_but_changed_content_is(self):
        self.assertEqual(self.history.enqueue(self.batch, self.provider.destination_id, 100), 0)
        self.run_job()
        duplicate = stage_snapshot(self.source, self.base / "staging", staging_owner())
        self.assertEqual(self.history.enqueue(duplicate, self.provider.destination_id, 100), 0)
        (self.source / "recording.txt").write_bytes(b"new version")
        changed = stage_snapshot(self.source, self.base / "staging", staging_owner())
        self.assertEqual(self.history.enqueue(changed, self.provider.destination_id, 100), 1)
        self.assertEqual(self.run_job(), "verified")
        self.assertEqual(len(self.provider.remote), 2)

    def test_each_destination_has_independent_jobs(self):
        self.assertEqual(self.history.enqueue(self.batch, "other/destination", 100), 1)
        self.run_job()
        self.assertEqual([j["state"] for j in self.history.jobs()], ["verified", "queued"])

    def test_selected_batch_never_claims_or_reads_another_pending_root(self):
        (self.source / "recording.txt").write_bytes(b"selected later content")
        selected = stage_snapshot(self.source, self.base / "staging", staging_owner())
        self.history.enqueue(selected, self.provider.destination_id, 100)
        original = self.batch.root / "files/recording.txt"
        original.write_bytes(b"must never read this unadmitted older batch")
        self.assertEqual(self.run_job(batch_root=selected.root), "verified")
        jobs = self.history.jobs()
        self.assertEqual([(job["state"], job["attempts"]) for job in jobs], [("queued", 0), ("verified", 1)])
        self.assertEqual(self.provider.uploads, 1)
        self.assertIsNone(self.run_job(batch_root=selected.root))
        self.assertEqual(jobs[0]["batch_root"], str(self.batch.root))

    def test_selected_duplicate_does_not_rebind_old_pending_or_verified_content(self):
        duplicate = stage_snapshot(self.source, self.base / "staging", staging_owner())
        self.assertEqual(self.history.enqueue(duplicate, self.provider.destination_id, 100), 0)
        self.assertIsNone(self.run_job(batch_root=duplicate.root))
        self.assertEqual(self.history.jobs()[0]["state"], "queued")
        self.assertEqual(self.history.jobs()[0]["batch_root"], str(self.batch.root))
        self.assertEqual(self.run_job(), "verified")
        self.assertIsNone(self.run_job(batch_root=duplicate.root))
        self.assertEqual(self.provider.uploads, 1)

    def test_reopen_recovers_crash_during_verification(self):
        with mock.patch.object(self.provider, "verify", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_job()
        self.assertEqual(self.history.jobs()[0]["state"], "verifying")
        self.history.close()
        self.history = History(self.path)
        self.engine = BackupEngine(self.history, base_delay=10, max_delay=15)
        self.assertEqual(self.run_job(200), "verified")
        self.assertEqual(self.history.jobs()[0]["attempts"], 2)
        self.assertIn("interrupted", [e["error_code"] for e in self.history.events(1)])

    def test_interrupted_last_attempt_is_failed_without_more_uploads(self):
        engine = BackupEngine(self.history, max_attempts=1)
        with mock.patch.object(self.provider, "verify", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                engine.run_once(self.provider, now=100, connectivity=Connectivity.ONLINE)
        self.assertIsNone(engine.run_once(self.provider, now=200, connectivity=Connectivity.ONLINE))
        self.assertEqual(self.history.jobs()[0]["state"], "failed")
        self.assertEqual(self.provider.uploads, 1)

    def test_workers_cannot_overlap(self):
        with self.history.worker():
            with self.assertRaisesRegex(BackupError, "backup_worker_busy"):
                self.run_job()
        self.assertEqual(self.history.jobs()[0]["attempts"], 0)

    def test_changed_staging_fails_before_provider_access(self):
        (self.batch.root / "files/recording.txt").write_bytes(b"changed staged bytes")
        self.assertEqual(self.run_job(), "failed")
        self.assertEqual(self.provider.uploads, 0)
        self.assertEqual(self.history.jobs()[0]["error_code"], "staged_file_changed")

    def test_unknown_portal_limited_and_metered_connections_pause_without_attempts(self):
        self.assertEqual(self.engine.run_once(self.provider, now=100), "paused")
        for state in (Connectivity.PORTAL, Connectivity.LIMITED, Connectivity.OFFLINE):
            self.assertEqual(self.engine.run_once(self.provider, now=100, connectivity=state), "paused")
        self.assertEqual(self.run_job(metered=True), "paused")
        self.assertEqual(self.history.jobs()[0]["attempts"], 0)
        self.assertEqual(self.run_job(metered=True, allow_metered=True), "verified")

    def test_history_cli_is_read_only_and_bounds_output(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["history", str(self.path), "--limit", "1"]), 0)
        self.assertEqual(len(json.loads(output.getvalue())), 1)
        self.assertNotIn("batch_root", output.getvalue())
        missing = self.base / "missing.sqlite3"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["history", str(missing)]), 2)
            self.assertEqual(main(["history", str(self.path), "--limit", "-1"]), 2)
        self.assertFalse(missing.exists())

    def test_invalid_retry_policy_rejected(self):
        for kwargs in ({"max_attempts": 0}, {"max_attempts": True}, {"base_delay": 0},
                       {"base_delay": 100, "max_delay": 10}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BackupEngine(self.history, **kwargs)

    def test_database_failure_cannot_record_or_return_false_success(self):
        update = self.history.update

        def fail_last_write(job_id, state, now, **kwargs):
            if state == "verified":
                raise sqlite3.OperationalError("disk full: private details")
            return update(job_id, state, now, **kwargs)

        with mock.patch.object(self.history, "update", side_effect=fail_last_write):
            with self.assertRaisesRegex(BackupError, "history_write_failed") as result:
                self.run_job()
        self.assertNotIn("private", str(result.exception))
        self.assertEqual(self.history.jobs()[0]["state"], "verifying")
        self.assertEqual(self.run_job(200), "verified")

    def test_verification_timestamp_records_completion_in_real_clock_mode(self):
        with mock.patch("raspi_cloud_sync_pro.engine.time.time", side_effect=[100, 110, 111, 120]):
            self.assertEqual(self.engine.run_once(self.provider, connectivity=Connectivity.ONLINE), "verified")
        self.assertEqual(self.history.events(1)[-1]["recorded_at"], 120)

    def test_reduced_retry_limit_marks_exhausted_jobs_failed(self):
        self.provider.corrupt = True
        self.assertEqual(self.run_job(), "retry")
        engine = BackupEngine(self.history, max_attempts=1)
        self.assertIsNone(engine.run_once(self.provider, now=200, connectivity=Connectivity.ONLINE))
        self.assertEqual(self.history.jobs()[0]["state"], "failed")
        self.assertEqual(self.history.jobs()[0]["error_code"], "retry_exhausted")
