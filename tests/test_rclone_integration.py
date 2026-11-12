"""Optional real-rclone test using only a temporary local alias remote."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.engine import BackupEngine
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.network import Connectivity
from raspi_cloud_sync_pro.providers import RcloneProvider
from raspi_cloud_sync_pro.staging import stage_snapshot
from test_storage import staging_owner

RCLONE = os.environ.get("RASPI_TEST_RCLONE") or shutil.which("rclone")


@unittest.skipUnless(RCLONE, "set RASPI_TEST_RCLONE to run the local-only rclone integration")
class RcloneIntegrationTests(unittest.TestCase):
    def test_copy_and_readback_hash_with_real_rclone_local_alias(self):
        # Do not let inherited rclone remote overrides change this local-only fixture.
        environment = {k: v for k, v in os.environ.items() if not k.startswith("RCLONE_")}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, environment, clear=True):
            base = Path(directory)
            source = base / "exclusive-fixture"
            source.mkdir()
            for name, data in (("empty", b""), ("日本語.txt", b"unicode"),
                               ("file;$(touch never).txt", b"real local adapter check")):
                (source / name).write_bytes(data)
            batch = stage_snapshot(source, base / "staging", staging_owner())
            remote_root = base / "remote"
            remote_root.mkdir()
            config = base / "rclone.conf"
            config.write_text(f"[fixture]\ntype = alias\nremote = {remote_root}\n")
            config.chmod(0o600)
            provider = RcloneProvider("koofr_webdav", "fixture:backup", config,
                                      enabled=True, executable=str(Path(RCLONE).absolute()), process_timeout=30)
            with History(base / "history.sqlite3") as history:
                history.enqueue(batch, provider.destination_id, 100)
                engine = BackupEngine(history)
                for item in batch.files:
                    self.assertEqual(engine.run_once(provider, now=100, connectivity=Connectivity.ONLINE), "verified")
                    self.assertEqual((remote_root / "backup" / item.remote_key).read_bytes(),
                                     (batch.root / "files" / item.relative).read_bytes())
                self.assertTrue(all(job["state"] == "verified" for job in history.jobs()))
            # Repeating upload is idempotent; corrupted remote bytes never verify.
            item = batch.files[-1]
            provider.upload(batch, item)
            (remote_root / "backup" / item.remote_key).write_bytes(b"corrupt")
            with self.assertRaises(BackupError) as result:
                provider.verify(batch, item)
            self.assertTrue(result.exception.retryable)
            with self.assertRaises(BackupError):
                provider.upload(batch, item)
            self.assertEqual((source / item.relative).read_bytes(), (batch.root / "files" / item.relative).read_bytes())
            self.assertFalse((base / "never").exists())
