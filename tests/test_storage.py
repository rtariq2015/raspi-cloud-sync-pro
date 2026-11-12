import dataclasses
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.staging import ReadyBatch, relative_path, stage_snapshot
from raspi_cloud_sync_pro.storage import Ownership, StorageOwnership


def staging_owner():
    owner = StorageOwnership()
    owner.confirm_host_access(locally_unmounted=True, loop_detached=True)
    owner.confirm_host_release(clean_eject_observed=True)
    owner.confirm_pi_access(gadget_closed=True, io_drained=True, filesystem_clean=True)
    owner.begin_staging(loop_read_only=True, mount_read_only=True)
    return owner


class OwnershipTests(unittest.TestCase):
    def test_startup_and_host_ownership_refuse_reads(self):
        owner = StorageOwnership()
        self.assertEqual(owner.state, Ownership.UNKNOWN)
        for confirm in (False, True):
            if confirm:
                owner.confirm_host_access(locally_unmounted=True, loop_detached=True)
            with self.assertRaisesRegex(BackupError, "read_not_permitted"), owner.read_access():
                self.fail("must not read")

    def test_full_handoff_and_return(self):
        owner = staging_owner()
        with owner.read_access():
            self.assertEqual(owner.state, Ownership.STAGING)
        owner.finish_staging(unmounted=True, loop_detached=True)
        self.assertEqual(owner.state, Ownership.RETURNABLE)
        owner.confirm_host_access(locally_unmounted=True, loop_detached=True)
        self.assertEqual(owner.state, Ownership.HOST)

    def test_false_or_coerced_eject_evidence_fails_closed(self):
        for evidence in (False, 1, "true", None):
            with self.subTest(evidence=evidence):
                owner = StorageOwnership()
                owner.confirm_host_access(locally_unmounted=True, loop_detached=True)
                with self.assertRaises(BackupError):
                    owner.confirm_host_release(clean_eject_observed=evidence)
                self.assertEqual(owner.state, Ownership.ERROR)

    def test_failed_unmount_cannot_return_image_to_host(self):
        owner = staging_owner()
        with self.assertRaises(BackupError):
            owner.finish_staging(unmounted=False, loop_detached=True)
        with self.assertRaises(BackupError):
            owner.confirm_host_access(locally_unmounted=True, loop_detached=True)
        self.assertEqual(owner.state, Ownership.ERROR)

    def test_cannot_skip_host_release_or_pi_ownership(self):
        for operation in (
            lambda o: o.confirm_pi_access(gadget_closed=True, io_drained=True, filesystem_clean=True),
            lambda o: o.begin_staging(loop_read_only=True, mount_read_only=True),
        ):
            owner = StorageOwnership()
            with self.assertRaises(BackupError):
                operation(owner)
            self.assertEqual(owner.state, Ownership.ERROR)

    def test_interrupted_read_fails_closed(self):
        owner = staging_owner()
        with self.assertRaises(KeyboardInterrupt):
            with owner.read_access():
                raise KeyboardInterrupt()
        self.assertEqual(owner.state, Ownership.ERROR)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "exclusive-fixture"
        self.source.mkdir()
        self.destination = self.base / "staging"

    def stage(self, **kwargs):
        return stage_snapshot(self.source, self.destination, staging_owner(), **kwargs)

    def test_complete_inventory_streaming_and_atomic_ready_batch(self):
        payload = b"x" * (1024 * 1024 + 31)
        for name, data in (("old/a", payload), ("older/b", b"b"), ("oldest/c", b""),
                           ("nested/one/two/three/日本語.txt", b"unicode"), (".hidden", b"hidden")):
            file = self.source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            os.utime(file, (1, 1))
        (self.source / "empty").mkdir()
        batch = self.stage()
        self.assertEqual(batch.root.suffix, ".ready")
        self.assertEqual(len(batch.files), 5)
        self.assertFalse(list(self.destination.glob("*.incomplete")))
        self.assertTrue((batch.root / "files/empty").is_dir())
        self.assertEqual(ReadyBatch.load(batch.root).files, batch.files)
        self.assertEqual((self.source / "old/a").read_bytes(), payload)
        item = next(f for f in batch.files if f.relative == "old/a")
        self.assertEqual(item.sha256, hashlib.sha256(payload).hexdigest())

    def test_hash_detects_changed_bytes_with_unchanged_timestamp_and_size(self):
        file = self.source / "a"
        file.write_bytes(b"old")
        first = self.stage()
        timestamps = file.stat()
        file.write_bytes(b"new")
        os.utime(file, ns=(timestamps.st_atime_ns, timestamps.st_mtime_ns))
        second = self.stage()
        self.assertNotEqual(first.files[0].sha256, second.files[0].sha256)
        self.assertEqual((first.root / "files/a").read_bytes(), b"old")

    def test_symlinks_never_followed_or_published(self):
        for is_dir in (False, True):
            with self.subTest(directory=is_dir):
                link = self.source / "link"
                target = self.base / ("private-dir" if is_dir else "private-file")
                target.mkdir() if is_dir else target.write_bytes(b"private")
                link.symlink_to(target, target_is_directory=is_dir)
                owner = staging_owner()
                with self.assertRaisesRegex(BackupError, "unsupported_source_entry"):
                    stage_snapshot(self.source, self.destination, owner)
                self.assertEqual(owner.state, Ownership.ERROR)
                self.assertFalse(list(self.destination.glob("*.ready")))
                link.unlink()

    def test_host_owned_source_is_not_even_enumerated(self):
        with mock.patch("raspi_cloud_sync_pro.staging.os.walk") as walk:
            with self.assertRaises(BackupError):
                stage_snapshot(self.source, self.destination, StorageOwnership())
        walk.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_low_space_preserves_original_and_partial_batch(self):
        (self.source / "a").write_bytes(b"important")
        with mock.patch("raspi_cloud_sync_pro.staging.shutil.disk_usage") as usage:
            usage.return_value.free = 0
            with self.assertRaisesRegex(BackupError, "staging_space_low"):
                self.stage()
        self.assertEqual((self.source / "a").read_bytes(), b"important")
        self.assertTrue(list(self.destination.glob("*.incomplete")))
        self.assertFalse(list(self.destination.glob("*.ready")))

    def test_overlap_and_unsafe_names_rejected(self):
        with self.assertRaisesRegex(BackupError, "overlapping"):
            stage_snapshot(self.source, self.source / "staging", staging_owner())
        for name in (".", "..", "../a", "/a", "a//b", "a/./b", "a\\b", "a\nb", ""):
            with self.subTest(name=name), self.assertRaises(BackupError):
                relative_path(name)

    def test_manifest_tampering_and_payload_links_rejected(self):
        (self.source / "a").write_bytes(b"data")
        batch = self.stage()
        path = batch.root / "manifest.json"
        data = json.loads(path.read_text())
        data["files"][0]["relative"] = "../../private"
        path.write_text(json.dumps(data))
        with self.assertRaises(BackupError):
            ReadyBatch.load(batch.root)
        batch = self.stage()
        (batch.root / "files/a").unlink()
        (batch.root / "files/a").symlink_to(self.source / "a")
        with self.assertRaises(BackupError):
            ReadyBatch.load(batch.root)

    def test_manifest_objects_are_frozen_and_incomplete_batches_rejected(self):
        (self.source / "a").write_bytes(b"data")
        batch = self.stage()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            batch.files[0].sha256 = "0" * 64
        with self.assertRaisesRegex(BackupError, "batch_not_ready"):
            ReadyBatch.load(batch.root.with_suffix(".incomplete"))
