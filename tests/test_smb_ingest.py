import io
import os
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.device_modes import DevicePolicy
from raspi_cloud_sync_pro.engine import BackupEngine
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.network import Connectivity
from raspi_cloud_sync_pro.smb_ingest import (
    LocalSambaReader, NetworkBackupCoordinator, SambaIngestor, candidate_files, stage_smb_version,
)
from test_engine import MemoryProvider


class SimulatedSamba:
    def __init__(self, root):
        self.root = root
        self.writers = set()
        self.read_locks = set()
        self.fail_close = False
        self.on_read = None

    def open_writer(self, relative):
        if relative in self.read_locks:
            raise BackupError("sharing_violation", True)
        self.writers.add(relative)

    @contextmanager
    def open_version(self, relative):
        if relative in self.writers:
            raise BackupError("sharing_violation", True)
        self.read_locks.add(relative)
        owner = self
        class Stream(io.BytesIO):
            def read(self, count=-1):
                if owner.on_read:
                    owner.on_read(relative)
                return super().read(count)
        try:
            with Stream((self.root / relative).read_bytes()) as stream:
                yield stream
            if self.fail_close:
                raise BackupError("smb_close_failed", True)
        finally:
            self.read_locks.remove(relative)


class SambaIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.share = self.root / "share"
        self.share.mkdir()
        (self.share / "video.dat").write_bytes(b"closed file version")
        self.reader = SimulatedSamba(self.share)
        self.staging = self.root / "staging"
        self.history = History(self.root / "state/history.sqlite3")
        self.addCleanup(self.history.close)
        self.provider = MemoryProvider()
        self.ingestor = SambaIngestor(self.reader, self.staging, self.history, [self.provider.destination_id])

    def test_active_writer_defers_without_reading_or_advancing_candidate(self):
        self.reader.open_writer("video.dat")
        self.assertEqual(self.ingestor.scan_once(self.share, 100), {"staged": 0, "deferred": 1})
        self.assertEqual(self.history.jobs(), [])
        self.assertFalse(self.staging.exists())
        self.reader.writers.clear()
        self.assertEqual(self.ingestor.scan_once(self.share, 101)["staged"], 1)

    def test_snapshot_prevents_new_write_opens_until_all_bytes_staged(self):
        def try_write(relative):
            with self.assertRaisesRegex(BackupError, "sharing_violation"):
                self.reader.open_writer(relative)
        self.reader.on_read = try_write
        batch, added = self.ingestor.capture("video.dat", 100)
        self.assertEqual(added, 1)
        self.assertEqual((batch.root / "files/video.dat").read_bytes(), b"closed file version")
        self.reader.open_writer("video.dat")  # Lock is released after successful snapshot.
        self.assertEqual((self.share / "video.dat").read_bytes(), b"closed file version")

    def test_close_failure_and_power_interruption_never_publish_ready(self):
        self.reader.fail_close = True
        with self.assertRaises(BackupError): self.ingestor.capture("video.dat", 100)
        self.assertFalse(list(self.staging.glob("*.ready")))
        self.assertTrue(list(self.staging.glob("*.incomplete")))
        self.reader.fail_close = False
        self.reader.on_read = mock.Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt): self.ingestor.capture("video.dat", 100)
        self.assertFalse(list(self.staging.glob("*.ready")))
        self.assertEqual(self.history.jobs(), [])

    def test_publication_before_queue_crash_is_reconciled_idempotently(self):
        with mock.patch.object(self.history, "enqueue", side_effect=OSError("simulated power loss")):
            with self.assertRaises(OSError): self.ingestor.capture("video.dat", 100)
        self.assertEqual(len(list(self.staging.glob("*.ready"))), 1)
        self.assertEqual(self.ingestor.reconcile(101), 1)
        self.assertEqual(self.ingestor.reconcile(102), 0)

    def test_new_version_requeues_and_unchanged_candidates_do_not_repeat(self):
        self.ingestor.scan_once(self.share, 100)
        self.assertEqual(self.ingestor.scan_once(self.share, 101)["staged"], 0)
        (self.share / "video.dat").write_bytes(b"a later version")
        self.assertEqual(self.ingestor.scan_once(self.share, 102)["staged"], 1)
        self.assertEqual(len(self.history.jobs()), 2)

    def test_wifi_outage_does_not_prevent_local_capture_or_spend_cloud_attempts(self):
        coordinator = NetworkBackupCoordinator(self.ingestor, BackupEngine(self.history),
                                               [self.provider], DevicePolicy())
        result = coordinator.tick(self.share, 100)
        self.assertEqual(result["staged"], 1)
        self.assertEqual(result["cloud"], ["paused"])
        self.assertEqual(self.history.jobs()[0]["attempts"], 0)
        result = coordinator.tick(self.share, 101, observed_uplink="usb_host_ics", connectivity=Connectivity.ONLINE)
        self.assertEqual(result["cloud"], ["paused"])
        result = coordinator.tick(self.share, 102, observed_uplink="wifi", connectivity=Connectivity.ONLINE)
        self.assertEqual(result["cloud"], ["verified"])

    def test_cloud_failure_retries_and_hotspots_require_metered_approval(self):
        coordinator = NetworkBackupCoordinator(self.ingestor, BackupEngine(self.history),
                                               [self.provider], DevicePolicy())
        result = coordinator.tick(self.share, 100, observed_uplink="wifi", connectivity=Connectivity.ONLINE,
                                  metered=True)
        self.assertEqual(result["cloud"], ["paused"])
        with mock.patch.object(self.provider, "upload", side_effect=BackupError("cloud_unavailable", True)):
            result = coordinator.tick(self.share, 101, observed_uplink="wifi", connectivity=Connectivity.ONLINE)
        self.assertEqual(result["cloud"], ["retry"])
        self.assertEqual(coordinator.tick(self.share, 132, observed_uplink="wifi",
                                          connectivity=Connectivity.ONLINE)["cloud"], ["verified"])

    def test_candidates_include_all_regular_names_and_reject_links(self):
        (self.share / "unfinished.uploading").write_bytes(b"partial")
        (self.share / "nested").mkdir()
        (self.share / "nested/日本語").write_bytes(b"unicode")
        self.assertEqual(set(candidate_files(self.share)), {"video.dat", "nested/日本語", "unfinished.uploading"})
        (self.share / "link").symlink_to(self.root)
        with self.assertRaisesRegex(BackupError, "unsupported_share_entry"): list(candidate_files(self.share))

    def test_concurrent_rename_during_inventory_is_reconciled_without_stopping_worker(self):
        original_stat = Path.lstat
        vanished = self.share / "video.dat"
        def stat_or_vanish(path, *args, **kwargs):
            if path == vanished:
                raise FileNotFoundError()
            return original_stat(path, *args, **kwargs)
        with mock.patch("pathlib.Path.lstat", new=stat_or_vanish):
            self.assertEqual(list(candidate_files(self.share)), [])
        self.assertEqual(self.ingestor.scan_once(self.share, 101)["staged"], 1)

    def test_disappearance_between_candidate_scan_and_capture_is_deferred(self):
        with mock.patch("raspi_cloud_sync_pro.smb_ingest.candidate_files", return_value=iter(["vanished"])):
            self.assertEqual(self.ingestor.scan_once(self.share, 100), {"staged": 0, "deferred": 1})
        self.assertEqual(self.history.jobs(), [])

    def test_low_space_and_alternate_streams_are_rejected(self):
        with mock.patch("raspi_cloud_sync_pro.smb_ingest.shutil.disk_usage") as usage:
            usage.return_value.free = 0
            with self.assertRaisesRegex(BackupError, "space_low"):
                self.ingestor.capture("video.dat", 100)
        with self.assertRaisesRegex(BackupError, "unsupported_smb_stream"):
            stage_smb_version(self.reader, "video.dat:stream", self.staging)
        self.assertFalse(list(self.staging.glob("*.ready")))

    def test_premature_eof_does_not_become_a_completed_file(self):
        class ShortReader:
            @contextmanager
            def open_version(self, relative):
                class ShortStream(io.BytesIO):
                    def seek(self, offset, whence=0):
                        return 100 if whence == os.SEEK_END else super().seek(offset, whence)
                with ShortStream(b"short") as stream: yield stream
        with self.assertRaisesRegex(BackupError, "smb_short_read"):
            stage_smb_version(ShortReader(), "file", self.staging)
        self.assertFalse(list(self.staging.glob("*.ready")))

    def test_live_smb_reader_is_gated_before_any_network_session(self):
        with mock.patch("socket.socket") as socket:
            with self.assertRaisesRegex(BackupError, "hardware_operations_disabled"), \
                    LocalSambaReader().open_version("video.dat"):
                self.fail("must not connect")
        socket.assert_not_called()

    def test_client_requests_encrypted_deny_write_and_delete_handle(self):
        client = types.SimpleNamespace(open_file=mock.Mock(return_value=io.BytesIO(b"snapshot")))
        with mock.patch("raspi_cloud_sync_pro.linux_gadget.require_hardware_test_approval"), \
                mock.patch.dict("sys.modules", {"smbclient": client}):
            with LocalSambaReader(username="fixture", password="fixture").open_version("folder/file") as stream:
                self.assertEqual(stream.read(), b"snapshot")
        args, kwargs = client.open_file.call_args
        self.assertEqual(args[0], "\\\\127.0.0.1\\CloudSync\\folder\\file")
        self.assertEqual(kwargs["share_access"], "r")
        self.assertTrue(kwargs["encrypt"])
