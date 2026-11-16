"""Hardware-free MTP completion -> existing worker integration.

The responder's protocol/cancel fixtures live under tools/experimental/mtp.
These tests do not emulate Windows or contact Google: the provider deliberately
uses a disposable local directory and independently hashes downloaded bytes.
"""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest

from raspi_cloud_sync_pro.device_modes import DevicePolicy
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.mtp_ingest import CompletionRequest, CompletionSettings, publish_completed
from raspi_cloud_sync_pro.network import Connectivity
from raspi_cloud_sync_pro.staged_worker import StagedWorker, WorkerSettings


class LocalVerificationProvider:
    """A synthetic provider, not another upload engine or a Google adapter."""
    destination_id = "synthetic-mtp-destination"

    def __init__(self, root):
        self.root = root
        self.uploads = 0
        self.verified_downloads = 0
        self.fail_next = False
        self.fail_verification_once = False
        self.first_upload_started = None

    def upload(self, batch, item):
        self.uploads += 1
        if self.first_upload_started is None:
            self.first_upload_started = time.monotonic()
        if self.fail_next:
            self.fail_next = False
            raise BackupError("synthetic_network_failure", retryable=True)
        target = self.root / item.remote_key
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes((batch.root / "files" / item.relative).read_bytes())

    def verify(self, batch, item):
        if self.fail_verification_once:
            self.fail_verification_once = False
            raise BackupError("synthetic_verification_interrupted", retryable=True)
        downloaded = (self.root / item.remote_key).read_bytes()
        if len(downloaded) != item.size or hashlib.sha256(downloaded).hexdigest() != item.sha256:
            raise BackupError("synthetic_download_mismatch")
        self.verified_downloads += 1


class MTPExistingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.work, self.staging = [self.root / name for name in ("export", "private", "staging")]
        for path in (self.source, self.work, self.staging):
            path.mkdir(mode=0o700)
        self.settings = CompletionSettings(
            self.source, self.work, self.staging, os.getuid(), os.getuid(), os.getgid(),
            reserve_bytes=0,
        )
        self.worker_settings = WorkerSettings(
            self.root / "worker", self.staging, self.root / "unused-synthetic-rclone.conf",
            (("google_drive_rclone", "synthetic:"),), DevicePolicy(),
        )
        self.history = History(self.root / "history.sqlite3")
        self.addCleanup(self.history.close)
        self.provider = LocalVerificationProvider(self.root / "synthetic-remote")
        self.transaction = 0

    def completed(self, relative, data):
        """Invoke the trusted completion boundary; C tests exercise the protocol."""
        self.transaction += 1
        path = self.source / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            return publish_completed(fd, CompletionRequest(relative, len(data), 1,
                                     self.transaction, self.transaction), self.settings)
        finally:
            os.close(fd)

    def tick(self, *, now=100, online=True):
        worker = StagedWorker(self.worker_settings, self.history, [self.provider])
        return worker.tick(now=now, observation=("wifi", Connectivity.ONLINE if online
                                               else Connectivity.OFFLINE, False))

    def test_consecutive_transfers_publish_and_upload_on_first_worker_tick(self):
        batches = []
        for number in range(3):
            body = ("synthetic report %d\n" % number).encode() * 4096
            batch = self.completed("QA/report-%d.txt" % number, body)
            published = time.monotonic()
            result = self.tick(now=100 + number)
            batches.append(batch)
            self.assertEqual(result["jobs_added"], 1)
            self.assertEqual(result["cloud_results"], ["verified"])
            self.assertEqual(result["pending_jobs"], 0)
            self.assertTrue(batch.root.is_dir())
            self.assertLessEqual(published, time.monotonic())
            item = batch.files[0]
            self.assertEqual(hashlib.sha256(body).hexdigest(), item.sha256)
            self.assertEqual((self.provider.root / item.remote_key).read_bytes(), body)
        self.assertEqual(self.provider.uploads, 3)
        self.assertEqual(self.provider.verified_downloads, 3)
        self.assertEqual(len({batch.root.name for batch in batches}), 3)
        self.assertTrue(all(job["state"] == "verified" for job in self.history.jobs()))

    def test_same_filename_versions_and_identical_retransmission_reuse_deduplication(self):
        first = self.completed("QA/result.txt", b"first result")
        self.tick()
        duplicate = self.completed("QA/result.txt", b"first result")
        result = self.tick(now=101)
        self.assertEqual(result["jobs_added"], 0)
        self.assertEqual(self.provider.uploads, 1)
        replacement = self.completed("QA/result.txt", b"updated result")
        self.tick(now=102)
        self.assertEqual(len(self.history.jobs()), 2)
        self.assertEqual(self.provider.uploads, 2)
        self.assertNotEqual(first.root, duplicate.root)
        self.assertNotEqual(first.root, replacement.root)
        self.assertEqual((first.root / "files/QA/result.txt").read_bytes(), b"first result")
        self.assertEqual((replacement.root / "files/QA/result.txt").read_bytes(), b"updated result")
        self.assertTrue(all(batch.root.exists() for batch in (first, duplicate, replacement)))

    def test_offline_queue_survives_worker_recreation_then_verifies(self):
        batch = self.completed("offline.dat", b"locally retained while offline")
        self.tick(online=False)
        self.tick(now=101, online=False)
        job = self.history.jobs()[0]
        self.assertEqual((job["state"], job["attempts"]), ("queued", 0))
        self.assertEqual(self.provider.uploads, 0)
        self.assertEqual(self.tick(now=102)["cloud_results"], ["verified"])
        self.assertTrue(batch.root.exists())

    def test_network_and_verification_failures_retry_through_existing_engine(self):
        self.completed("retry.dat", b"retained retry contents")
        self.provider.fail_next = True
        self.assertEqual(self.tick()["cloud_results"], ["retry"])
        self.assertEqual(self.tick(now=101)["cloud_results"], [None])
        self.provider.fail_verification_once = True
        self.assertEqual(self.tick(now=130)["cloud_results"], ["retry"])
        self.assertEqual(self.tick(now=190)["cloud_results"], ["verified"])
        self.assertEqual(len(self.history.jobs()), 1)
        self.assertEqual(self.history.jobs()[0]["attempts"], 3)

    def test_receipt_loss_after_enqueue_does_not_create_another_job(self):
        self.completed("receipt.dat", b"durably deduplicated")
        self.tick(online=False)
        with self.history.connection:
            self.history.connection.execute("DELETE FROM staged_receipts")
        self.tick(now=101)
        self.assertEqual(len(self.history.jobs()), 1)
        self.assertEqual(self.provider.uploads, 1)

    def test_interrupted_upload_recovers_without_replacing_history(self):
        batch = self.completed("interrupted.dat", b"interrupted upload survives")
        self.tick(online=False)
        job = self.history.jobs()[0]
        with self.history.connection:
            self.history.connection.execute("UPDATE jobs SET state='uploading',attempts=1 WHERE id=?", (job["id"],))
        result = self.tick(now=200)
        self.assertEqual(result["cloud_results"], ["verified"])
        self.assertEqual(self.history.jobs()[0]["id"], job["id"])
        self.assertTrue(batch.root.exists())

    def test_export_view_deletion_cannot_delete_accepted_backup_source(self):
        batch = self.completed("retained.dat", b"snapshot is independent")
        (self.source / "retained.dat").unlink()
        self.assertEqual(self.tick()["cloud_results"], ["verified"])
        self.assertEqual((batch.root / "files/retained.dat").read_bytes(), b"snapshot is independent")

    def test_larger_object_streams_across_many_snapshot_chunks(self):
        # Deliberately exceed the publisher's 64 KiB read chunks. This is local
        # synthetic storage evidence, not a multi-gigabyte Windows acceptance.
        body = bytes(range(256)) * (16 * 1024 * 1024 // 256)
        batch = self.completed("larger-report.bin", body)
        self.assertEqual(batch.files[0].size, 16 * 1024 * 1024)
        self.assertEqual(self.tick()["cloud_results"], ["verified"])
        self.assertEqual(self.provider.verified_downloads, 1)
        self.assertEqual(hashlib.sha256(body).hexdigest(), batch.files[0].sha256)


if __name__ == "__main__":
    unittest.main()
