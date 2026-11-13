import io
import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.device_modes import DevicePolicy
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.network import Connectivity
from raspi_cloud_sync_pro.staged_worker import (
    StagedWorker, WorkerSettings, observe_uplink, run_staged_worker, scheduler_lock,
)
from raspi_cloud_sync_pro.web_ingest import stage_upload


class Destination:
    def __init__(self, name, fail=False):
        self.destination_id = name
        self.fail = fail
        self.uploads = 0

    def upload(self, batch, item):
        self.uploads += 1
        if self.fail:
            raise BackupError("test_network_timeout", retryable=True)

    def verify(self, batch, item):
        batch.check_file(item)


class StagedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = WorkerSettings(self.root / "state", self.root / "staging", self.root / "rclone.conf",
                                       (("koofr_webdav", "koofr:Backup"),), DevicePolicy())
        self.history = History(self.root / "history.sqlite3")
        self.addCleanup(self.history.close)

    def test_offline_staging_reconciles_after_restart_without_spending_attempts(self):
        batch = stage_upload(io.BytesIO(b"QA"), "test.txt", self.settings.staging)
        providers = [Destination("first"), Destination("second")]
        for _ in range(2):
            worker = StagedWorker(self.settings, self.history, providers)
            worker.tick(now=10, observation=("unknown", Connectivity.OFFLINE, True))
        jobs = self.history.jobs()
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job["state"] == "queued" and job["attempts"] == 0 for job in jobs))
        self.assertTrue((batch.root / "files/test.txt").exists())
        # Removed destinations retain their jobs without keeping this scheduler busy.
        result = StagedWorker(self.settings, self.history, providers[:1]).tick(
            now=11, observation=("unknown", Connectivity.OFFLINE, True))
        self.assertEqual(result["pending_jobs"], 1)
        self.assertEqual(len(self.history.jobs()), 2)

    def test_destinations_retry_independently_and_verified_source_is_retained(self):
        batch = stage_upload(io.BytesIO(b"version"), "QA.txt", self.settings.staging)
        good, failing = Destination("first"), Destination("second", fail=True)
        worker = StagedWorker(self.settings, self.history, [good, failing])
        result = worker.tick(now=100, observation=("wifi", Connectivity.ONLINE, False))
        self.assertEqual(result["cloud_results"], ["verified", "retry"])
        self.assertEqual([job["attempts"] for job in self.history.jobs()], [1, 1])
        worker.tick(now=101, observation=("wifi", Connectivity.ONLINE, False))
        self.assertEqual((good.uploads, failing.uploads), (1, 1))
        failing.fail = False
        worker.tick(now=130, observation=("wifi", Connectivity.ONLINE, False))
        self.assertTrue(all(job["state"] == "verified" for job in self.history.jobs()))
        self.assertEqual((batch.root / "files/QA.txt").read_bytes(), b"version")

    def test_receipts_skip_rehash_after_restart_but_cannot_authorize_changed_payload(self):
        batch = stage_upload(io.BytesIO(b"good"), "QA.txt", self.settings.staging)
        provider = Destination("first")
        StagedWorker(self.settings, self.history, [provider]).tick(
            observation=("unknown", Connectivity.OFFLINE, True))
        (batch.root / "files/QA.txt").write_bytes(b"bad!")
        restarted = StagedWorker(self.settings, self.history, [provider])
        with mock.patch("raspi_cloud_sync_pro.staged_worker.ReadyBatch.load", side_effect=AssertionError("unneeded full rehash")):
            restarted.tick(observation=("unknown", Connectivity.OFFLINE, True))
        restarted.tick(observation=("wifi", Connectivity.ONLINE, False))
        self.assertEqual(provider.uploads, 0)
        self.assertEqual(self.history.jobs()[0]["state"], "failed")
        self.assertTrue((batch.root / "files/QA.txt").exists())

    def test_usb_preference_portal_unknown_and_metered_states_pause(self):
        stage_upload(io.BytesIO(b"QA"), "test.txt", self.settings.staging)
        provider = Destination("first")
        worker = StagedWorker(self.settings, self.history, [provider])
        for observed in (("usb_host_ics", Connectivity.ONLINE, False),
                         ("wifi", Connectivity.PORTAL, False), ("wifi", Connectivity.UNKNOWN, False),
                         ("wifi", Connectivity.ONLINE, True)):
            result = worker.tick(now=100, observation=observed)
            self.assertEqual(result["cloud_results"], ["paused"])
        self.assertEqual(self.history.jobs()[0]["attempts"], 0)
        self.assertEqual(provider.uploads, 0)

    def test_corrupt_incomplete_and_symlink_batches_cannot_enter_queue(self):
        staging = self.settings.staging
        staging.mkdir()
        (staging / "partial.incomplete").mkdir()
        (staging / "broken.ready").mkdir()
        (staging / "linked.ready").symlink_to(self.root, target_is_directory=True)
        worker = StagedWorker(self.settings, self.history, [Destination("first")])
        result = worker.tick(observation=("unknown", Connectivity.UNKNOWN, True))
        self.assertEqual(result["rejected_batches"], 2)
        self.assertEqual(self.history.jobs(), [])
        self.assertTrue((staging / "broken.ready").exists())

    def test_short_lived_workers_finish_reconciliation_beyond_the_tick_budget(self):
        # Empty, valid manifests produce no jobs, so pending_jobs cannot signal
        # the remaining registration work to the Rust parent.
        self.settings.staging.mkdir()
        for number in range(101):
            batch = self.settings.staging / f"{number:032x}.ready"
            batch.mkdir()
            (batch / "manifest.json").write_text('{"schema_version":1,"files":[]}')
        provider = Destination("first")
        first = StagedWorker(self.settings, self.history, [provider]).tick(
            observation=("unknown", Connectivity.OFFLINE, True))
        self.assertEqual(first["pending_jobs"], 0)
        self.assertTrue(first["reconciliation_pending"])
        second = StagedWorker(self.settings, self.history, [provider]).tick(
            observation=("unknown", Connectivity.OFFLINE, True))
        self.assertFalse(second["reconciliation_pending"])
        self.assertEqual(self.history.connection.execute("SELECT COUNT(*) FROM staged_receipts").fetchone()[0], 101)

    def test_retained_malformed_batches_cannot_starve_fresh_worker_reconciliation(self):
        self.settings.staging.mkdir()
        for number in range(101):
            (self.settings.staging / f"{number:032x}.ready").mkdir()
        good = stage_upload(io.BytesIO(b"QA"), "good.txt", self.settings.staging)
        provider = Destination("first")
        results = [StagedWorker(self.settings, self.history, [provider]).tick(
            observation=("unknown", Connectivity.OFFLINE, True)) for _ in range(3)]
        self.assertTrue(results[0]["reconciliation_pending"])
        self.assertFalse(results[-1]["reconciliation_pending"])
        self.assertEqual(sum(result["rejected_batches"] for result in results), 101)
        self.assertEqual(len(self.history.jobs()), 1)
        self.assertEqual(self.history.jobs()[0]["batch_root"], str(good.root))

    def test_disabled_worker_does_not_create_state_and_bad_prerequisites_spend_no_attempts(self):
        with self.assertRaisesRegex(BackupError, "staged_worker_disabled"):
            run_staged_worker(self.settings, once=True)
        with self.assertRaisesRegex(BackupError, "worker_rclone_prerequisites_missing"):
            run_staged_worker(replace(self.settings, enabled=True), once=True)
        self.assertFalse(self.settings.state.exists())

    def test_scheduler_lock_excludes_a_second_scheduler(self):
        with scheduler_lock(self.root):
            with self.assertRaisesRegex(BackupError, "staged_worker_busy"), scheduler_lock(self.root):
                pass

    def test_configuration_rejects_duplicates_unsafe_paths_and_unknown_fields(self):
        example = Path(__file__).resolve().parents[1] / "config/worker.example.json"
        settings = WorkerSettings.load(example)
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.plan()["automatic_deletion"])
        data = json.loads(example.read_text())
        path = self.root / "worker.json"
        for changed in ({**data, "enabled": "true"}, {**data, "state_directory": "relative"},
                        {**data, "password": "must never be echoed"},
                        {**data, "destinations": [data["destinations"][0]] * 2}):
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(BackupError, "invalid_staged_worker_config"):
                WorkerSettings.load(path)
        path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(BackupError):
            WorkerSettings.load(path)

    def test_cached_global_full_does_not_override_usb_route_or_unknown_metering(self):
        report = {"preferred_ipv4_route": {"interface": "usb0"}, "cached_global_connectivity": "full",
                  "interfaces": {"usb0": {"state_code": 100, "type": "ethernet"},
                                 "wlan0": {"state_code": 100, "type": "wifi"}}}
        for metered, expected in (("guess-no", ("usb_host_ics", Connectivity.ONLINE, False)),
                                  ("no (guessed)", ("usb_host_ics", Connectivity.ONLINE, False)),
                                  ("yes (guessed)", ("usb_host_ics", Connectivity.ONLINE, True)),
                                  ("guess-yes", ("usb_host_ics", Connectivity.ONLINE, True)),
                                  ("no", ("usb_host_ics", Connectivity.ONLINE, False)),
                                  ("yes", ("usb_host_ics", Connectivity.ONLINE, True)),
                                  ("no (unknown)", ("unknown", Connectivity.UNKNOWN, True)),
                                  ("unknown", ("unknown", Connectivity.UNKNOWN, True))):
            with mock.patch("raspi_cloud_sync_pro.staged_worker.network_status", return_value=report), \
                    mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, metered, "")) as run:
                self.assertEqual(observe_uplink(), expected)
            self.assertEqual(run.call_args.args[0], ["nmcli", "-g", "GENERAL.METERED", "device", "show", "usb0"])
