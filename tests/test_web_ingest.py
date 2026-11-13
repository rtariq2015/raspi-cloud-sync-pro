import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.staging import ReadyBatch
from raspi_cloud_sync_pro.web_ingest import stage_upload


class WebIngestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_completed_unicode_and_empty_uploads_have_verifiable_manifests(self):
        for name, content in (("QA α.csv", b"value\n42\n"), ("empty.txt", b"")):
            batch = stage_upload(io.BytesIO(content), name, self.root)
            self.assertEqual(ReadyBatch.load(batch.root).files, batch.files)
            self.assertEqual((batch.root / "files" / name).read_bytes(), content)
            self.assertEqual(batch.files[0].size, len(content))

    def test_unsafe_names_are_rejected_before_creating_a_batch(self):
        for name in ("../secret", "/etc/passwd", "folder/file", "..", ".env", "a\\b", "a\nb"):
            with self.subTest(name=name), self.assertRaises(BackupError):
                stage_upload(io.BytesIO(b"data"), name, self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_disconnect_size_limit_and_low_space_never_publish_ready_data(self):
        class BrokenStream:
            def read(self, size):
                raise OSError("client disconnected")
        for stream, options in ((BrokenStream(), {}), (io.BytesIO(b"oversize"), {"max_bytes": 2})):
            with self.assertRaises(BackupError):
                stage_upload(stream, "file.txt", self.root, **options)
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=0)), self.assertRaises(BackupError):
            stage_upload(io.BytesIO(b"full"), "file.txt", self.root)
        self.assertEqual(len(list(self.root.glob("*.incomplete"))), 3)
        self.assertEqual(list(self.root.glob("*.ready")), [])

    def test_symlink_staging_root_is_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(BackupError):
            stage_upload(io.BytesIO(b"data"), "file.txt", alias)

    def test_publication_failure_retains_payload_and_cannot_enter_ready_queue(self):
        with mock.patch("pathlib.Path.rename", side_effect=OSError("power loss")), self.assertRaises(BackupError):
            stage_upload(io.BytesIO(b"keep me"), "file.txt", self.root)
        temporary = next(self.root.glob("*.incomplete"))
        self.assertEqual((temporary / "files/file.txt").read_bytes(), b"keep me")
        self.assertEqual(json.loads((temporary / "manifest.json").read_text())["files"][0]["size"], 7)
        self.assertEqual(list(self.root.glob("*.ready")), [])
