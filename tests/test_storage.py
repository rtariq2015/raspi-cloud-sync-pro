import dataclasses
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.staging import CHUNK_SIZE, ReadyBatch, hash_stream, relative_path, stage_changed_snapshot, stage_snapshot
from raspi_cloud_sync_pro.storage import Ownership, StorageOwnership


def staging_owner():
    owner = StorageOwnership()
    owner.confirm_host_access(locally_unmounted=True, loop_detached=True)
    owner.confirm_host_release(clean_eject_observed=True)
    owner.confirm_pi_access(gadget_closed=True, io_drained=True, filesystem_clean=True)
    owner.begin_staging(loop_read_only=True, mount_read_only=True)
    return owner


class OwnershipTests(unittest.TestCase):
    def test_hash_stream_deadline_callback_stops_after_one_actual_chunk(self):
        with tempfile.TemporaryFile() as stream:
            stream.write(b"x" * (CHUNK_SIZE * 3 + 17))
            stream.seek(0)
            def check():
                if stream.tell() >= CHUNK_SIZE:
                    raise BackupError("synthetic_deadline")
            with self.assertRaisesRegex(BackupError, "synthetic_deadline"):
                hash_stream(stream, check=check)
            self.assertEqual(stream.tell(), CHUNK_SIZE)

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

    def test_unchanged_inventory_hashes_but_creates_no_new_capture(self):
        (self.source / 'qa.txt').write_bytes(b'USB QA')
        first, complete = stage_changed_snapshot(self.source, self.destination, staging_owner())
        before = sorted(self.destination.iterdir())
        with mock.patch('raspi_cloud_sync_pro.staging.stage_snapshot') as copy, \
                mock.patch('raspi_cloud_sync_pro.staging.hash_stream', wraps=hash_stream) as hashed:
            second, current = stage_changed_snapshot(self.source, self.destination, staging_owner(), complete)
        self.assertIsNone(second);self.assertEqual(current, complete);copy.assert_not_called();hashed.assert_called_once()
        self.assertEqual(sorted(self.destination.iterdir()), before)
        self.assertEqual((first.root / 'files/qa.txt').read_bytes(), b'USB QA')

    def test_only_changed_and_new_versions_copied_even_with_restored_mtime(self):
        for name in ('changed', 'unchanged'):(self.source / name).write_bytes(b'old')
        first, previous = stage_changed_snapshot(self.source, self.destination, staging_owner())
        file = self.source / 'changed';before = file.stat();file.write_bytes(b'new')
        os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
        (self.source / 'new').write_bytes(b'added')
        batch, complete = stage_changed_snapshot(self.source, self.destination, staging_owner(), previous)
        self.assertEqual([item.relative for item in batch.files], ['changed', 'new'])
        self.assertEqual([item.relative for item in complete], ['changed', 'new', 'unchanged'])
        self.assertFalse((batch.root / 'files/unchanged').exists())
        self.assertEqual((first.root / 'files/changed').read_bytes(), b'old')
        self.assertEqual((self.source / 'unchanged').read_bytes(), b'old')

    def test_delete_then_recreate_never_deletes_old_capture_and_recaptures_version(self):
        file = self.source / 'qa.txt';file.write_bytes(b'retain')
        first, previous = stage_changed_snapshot(self.source, self.destination, staging_owner())
        file.unlink()
        deleted, empty = stage_changed_snapshot(self.source, self.destination, staging_owner(), previous)
        self.assertIsNone(deleted);self.assertEqual(empty, ())
        self.assertEqual((first.root / 'files/qa.txt').read_bytes(), b'retain')
        file.write_bytes(b'retain')
        recreated, current = stage_changed_snapshot(self.source, self.destination, staging_owner(), empty)
        self.assertEqual([item.relative for item in recreated.files], ['qa.txt']);self.assertEqual(len(current), 1)
        self.assertTrue(first.root.exists())

    def test_source_change_after_inventory_never_returns_a_baseline(self):
        file = self.source / 'qa.txt';file.write_bytes(b'original')
        real_copy = stage_snapshot
        def changed_copy(*args, **kwargs):
            file.write_bytes(b'changed before copy')
            return real_copy(*args, **kwargs)
        with mock.patch('raspi_cloud_sync_pro.staging.stage_snapshot', side_effect=changed_copy), self.assertRaisesRegex(BackupError, 'source_changed'):
            stage_changed_snapshot(self.source, self.destination, staging_owner())
        self.assertTrue(list(self.destination.glob('*.ready')))  # Completed partial outcome is retained for review.

    def test_change_to_skipped_file_after_hash_is_detected_without_copying(self):
        from raspi_cloud_sync_pro import staging
        file = self.source / 'qa.txt';file.write_bytes(b'old')
        _, previous = stage_changed_snapshot(self.source, self.destination, staging_owner())
        inventory = staging._capture_inventory
        def mutate_after_hash(*args, **kwargs):
            result = inventory(*args, **kwargs)
            if kwargs['contents']:file.write_bytes(b'new')
            return result
        with mock.patch.object(staging, '_capture_inventory', side_effect=mutate_after_hash), \
                mock.patch.object(staging, 'stage_snapshot') as copy, self.assertRaisesRegex(BackupError, 'source_changed'):
            stage_changed_snapshot(self.source, self.destination, staging_owner(), previous)
        copy.assert_not_called();self.assertEqual(len(list(self.destination.glob('*.ready'))), 1)

    def test_changed_snapshot_still_requires_exclusive_ownership_and_safe_entries(self):
        with mock.patch('raspi_cloud_sync_pro.staging.os.walk') as walk, self.assertRaises(BackupError):
            stage_changed_snapshot(self.source, self.destination, StorageOwnership())
        walk.assert_not_called()
        (self.source / 'unsafe').symlink_to(self.base / 'private')
        with self.assertRaisesRegex(BackupError, 'unsupported_source_entry'):
            stage_changed_snapshot(self.source, self.destination, staging_owner())
        self.assertFalse(self.destination.exists())

    def test_changed_snapshot_flat_count_and_depth_limits_before_copy(self):
        for index in range(4):(self.source / str(index)).write_bytes(b'')
        with mock.patch('raspi_cloud_sync_pro.staging._CAPTURE_ENTRY_LIMIT', 5), \
                mock.patch('raspi_cloud_sync_pro.staging.stage_snapshot') as copied, self.assertRaisesRegex(BackupError, 'capture_inventory_limit'):
            stage_changed_snapshot(self.source, self.destination, staging_owner())
        copied.assert_not_called();self.assertFalse(self.destination.exists())

        with mock.patch('raspi_cloud_sync_pro.staging._CAPTURE_METADATA_LIMIT', 4097), \
                mock.patch('raspi_cloud_sync_pro.staging.stage_snapshot') as copied, self.assertRaisesRegex(BackupError, 'capture_inventory_limit'):
            stage_changed_snapshot(self.source, self.destination, staging_owner())
        copied.assert_not_called();self.assertFalse(self.destination.exists())
        (self.source / 'one/two').mkdir(parents=True)
        with mock.patch('raspi_cloud_sync_pro.staging._CAPTURE_DEPTH_LIMIT', 2), \
                mock.patch('raspi_cloud_sync_pro.staging.stage_snapshot') as copied, self.assertRaisesRegex(BackupError, 'capture_inventory_limit'):
            stage_changed_snapshot(self.source, self.destination, staging_owner())
        copied.assert_not_called();self.assertFalse(self.destination.exists())

    def test_exfat_cross_pass_inode_changes_do_not_invalidate_identical_content(self):
        from raspi_cloud_sync_pro import staging
        (self.source/'qa.txt').write_bytes(b'actual unchanged file')
        original = staging._capture_inventory
        def inode_changes(*args, **kwargs):
            files, values = original(*args, **kwargs)
            if not kwargs['contents']:
                values = {path: value[:1] + (value[1]+1000,) + value[2:] for path, value in values.items()}
            return files, values
        with mock.patch.object(staging, '_capture_inventory', side_effect=inode_changes):
            batch, previous = stage_changed_snapshot(self.source, self.destination, staging_owner())
            unchanged, current = stage_changed_snapshot(self.source, self.destination, staging_owner(), previous)
        self.assertIsNone(unchanged);self.assertEqual(current, previous)
        self.assertEqual((batch.root/'files/qa.txt').read_bytes(), b'actual unchanged file')
        self.assertEqual(len(list(self.destination.glob('*.ready'))), 1)

    def test_cross_pass_device_size_and_times_still_invalidate_capture(self):
        from raspi_cloud_sync_pro import staging
        (self.source/'qa.txt').write_bytes(b'unchanged file')
        original = staging._capture_inventory
        for field in (0, 2, 3, 4):
            def metadata_change(*args, **kwargs):
                files, values = original(*args, **kwargs)
                if not kwargs['contents']:
                    values = {path: value[:field] + (value[field]+1,) + value[field+1:] for path, value in values.items()}
                return files, values
            with self.subTest(field=field), mock.patch.object(staging, '_capture_inventory', side_effect=metadata_change), \
                    self.assertRaisesRegex(BackupError, 'source_changed'):
                stage_changed_snapshot(self.source, self.destination, staging_owner())
