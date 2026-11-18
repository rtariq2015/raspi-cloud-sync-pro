"""Disposable-image preparation never touches block devices in these tests."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError

SOURCE = Path(__file__).resolve().parents[1] / "tools/acceptance/mass_storage/prepare_image.py"
spec = importlib.util.spec_from_file_location("msc_image_prepare", SOURCE)
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)


class MassStorageImagePreparationTests(unittest.TestCase):
    def test_mbr_has_one_aligned_partition_and_correct_signature(self):
        data = image.mbr()
        self.assertEqual(len(data), 512)
        self.assertEqual(data[510:], b"\x55\xaa")
        entry = struct.unpack("<B3sB3sII", data[446:462])
        self.assertEqual(entry[0], 0)
        self.assertEqual(entry[2], 7)
        self.assertEqual(entry[4], 2048)
        self.assertEqual(entry[5] * 512, image.SIZE - image.OFFSET)
        self.assertEqual(data[462:510], bytes(48))

    def test_invalid_image_layout_is_rejected(self):
        for size, offset in ((1000, 512), (512, 512), (4096, 513), (2 ** 41 + 512, 512)):
            with self.subTest(size=size, offset=offset), self.assertRaises(ValueError):
                image.mbr(size, offset)

    def test_development_host_is_refused_before_commands(self):
        with mock.patch.object(image.os, "geteuid", return_value=1000), \
                mock.patch.object(image, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "requires_reviewed_pi_root"):
                image.preflight()
            command.assert_not_called()

    def test_non_loop_device_is_rejected_before_format(self):
        with mock.patch.object(image, "image_identity") as identity, \
                mock.patch.object(image, "loops") as loops:
            with self.assertRaisesRegex(RuntimeError, "identity_changed"):
                image.verify_loop("/dev/mmcblk0p2", (1, 2, image.SIZE))
            identity.assert_not_called()
            loops.assert_not_called()

    def test_changed_image_identity_refuses_loop_use(self):
        with mock.patch.object(image, "image_identity", return_value=(1, 99, image.SIZE)), \
                mock.patch.object(image, "loops") as loops:
            with self.assertRaisesRegex(RuntimeError, "identity_changed"):
                image.verify_loop("/dev/loop7", (1, 2, image.SIZE))
            loops.assert_not_called()

    def test_command_timeout_requires_read_only_reconciliation(self):
        with mock.patch.object(image.subprocess, "run", side_effect=subprocess.TimeoutExpired("losetup", 90)):
            with self.assertRaisesRegex(RuntimeError, "read_only_reconciliation_required"):
                image.command(["/usr/sbin/losetup", "--find", "--show", str(image.IMAGE)])

    def test_shared_or_wrong_offset_loop_is_refused(self):
        identity = (1, 2, image.SIZE)
        valid = {"name": "/dev/loop7", "back-file": str(image.IMAGE), "offset": image.OFFSET,
                 "sizelimit": image.SIZE - image.OFFSET, "ro": False}
        for entries in ([valid, dict(valid, name="/dev/loop8")], [dict(valid, offset=0)],
                        [dict(valid, sizelimit=0)], [dict(valid, ro=True)]):
            with self.subTest(entries=entries), \
                    mock.patch.object(image, "image_identity", return_value=identity), \
                    mock.patch.object(image, "loops", return_value=entries):
                with self.assertRaisesRegex(RuntimeError, "exclusive_offset_loop_unverified"):
                    image.verify_loop("/dev/loop7", identity)

    def test_lazy_detach_waits_for_authoritative_inventory(self):
        associated = {"name": "/dev/loop7", "back-file": str(image.IMAGE)}
        with mock.patch.object(image, "loops", side_effect=[[associated], []]) as loops, \
                mock.patch.object(image.time, "sleep") as sleep:
            image.wait_detached("/dev/loop7")
            self.assertEqual(loops.call_count, 2)
            sleep.assert_called_once()

    def test_persistent_or_replaced_loop_refuses_export(self):
        associated = {"name": "/dev/loop7", "back-file": str(image.IMAGE)}
        with mock.patch.object(image, "loops", return_value=[associated]), \
                mock.patch.object(image.time, "monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(RuntimeError, "loop_detach_unverified"):
                image.wait_detached("/dev/loop7")
        with mock.patch.object(image, "loops", return_value=[dict(associated, name="/dev/loop8")]):
            with self.assertRaisesRegex(RuntimeError, "unexpected_loop_after_detach"):
                image.wait_detached("/dev/loop7")

    def _prepare_fixture(self, directory, *, fail_format=False, size=4 * 1024 ** 2):
        calls = []

        def command(arguments):
            calls.append(arguments)
            if "--find" in arguments:
                return "/dev/loop7"
            if arguments[0] == "/usr/sbin/mkfs.exfat" and fail_format:
                raise RuntimeError("simulated_format_failure")
            if arguments[0] == "/usr/sbin/mkfs.exfat":
                # Header fixture only; no actual formatter or filesystem is used.
                header = bytearray(512)
                header[3:11], header[108], header[510:] = b'EXFAT   ', 9, b'\x55\xaa'
                header[72:80] = ((size-image.OFFSET)//512).to_bytes(8,'little')
                with (target/'disk.img').open('r+b') as output:
                    output.seek(image.OFFSET);output.write(header)
            return ""

        target = Path(directory) / "trial"
        patches = [mock.patch.object(image, "DIRECTORY", target),
                   mock.patch.object(image, "IMAGE", target / "disk.img"),
                   mock.patch.object(image, "SIZE", size),
                   mock.patch.object(image, "preflight"),
                   mock.patch.object(image, "image_identity", side_effect=lambda **kwargs:
                       tuple(getattr((target/'disk.img').stat(),key) for key in ('st_dev','st_ino','st_size'))),
                   mock.patch.object(image, "loops", return_value=[]),
                   mock.patch.object(image, "verify_loop"),
                   mock.patch.object(image, "command", side_effect=command),
                   mock.patch.object(image.os, "posix_fallocate",
                                     side_effect=lambda fd, offset, length: os.ftruncate(fd, length))]
        if size == 4 * 1024 ** 2:
            # Preserve the old small lifecycle model without expanding production
            # capacity policy. The 8 GiB fixture below uses the actual validators.
            patches += [mock.patch.object(image, 'validate_image_size', return_value=size),
                        mock.patch.object(image, 'validate_image_headers')]
        return target, calls, patches

    def test_new_image_lifecycle_checks_before_format_and_detaches(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            target, calls, patches = self._prepare_fixture(directory)
            for patch in patches: stack.enter_context(patch)
            result = image.prepare()
            self.assertFalse(result["exported"])
            self.assertFalse(result["mounted"])
            self.assertTrue(result["formatted_and_checked"])
            self.assertEqual((target / "disk.img").stat().st_size, 4 * 1024 ** 2)
            self.assertEqual((target / "disk.img").read_bytes()[:512][510:], b"\x55\xaa")
            self.assertEqual([args[0] for args in calls], ["/usr/sbin/losetup", "/usr/sbin/mkfs.exfat",
                                                         "/usr/sbin/fsck.exfat", "/usr/sbin/losetup"])
            self.assertIn("-n", calls[2])
            self.assertEqual(calls[-1], ["/usr/sbin/losetup", "--detach", "/dev/loop7"])
            self.assertEqual(json.loads((target / "prepared.json").read_text())["partition"], 1)
            with self.assertRaises(FileExistsError): image.prepare()

    def test_format_failure_retains_image_and_intent_then_detaches(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            target, calls, patches = self._prepare_fixture(directory, fail_format=True)
            for patch in patches: stack.enter_context(patch)
            with self.assertRaisesRegex(RuntimeError, "simulated_format_failure"):
                image.prepare()
            self.assertTrue((target / "disk.img").is_file())
            self.assertTrue((target / "format-intent.json").is_file())
            self.assertFalse((target / "prepared.json").exists())
            self.assertEqual(calls[-1], ["/usr/sbin/losetup", "--detach", "/dev/loop7"])

    def test_short_partition_write_never_launches_formatter(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            target, calls, patches = self._prepare_fixture(directory)
            for patch in patches: stack.enter_context(patch)
            stack.enter_context(mock.patch.object(image.os, "pwrite", return_value=511))
            with self.assertRaisesRegex(RuntimeError, "short_partition_table_write"):
                image.prepare()
            self.assertEqual(calls, [])
            self.assertTrue((target / "disk.img").exists())
            self.assertFalse((target / "prepared.json").exists())

    def test_explicit_8gib_lifecycle_binds_capacity_and_exact_loop_limit(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            target, calls, patches = self._prepare_fixture(directory,size=image.EXPANDED_SIZE)
            for patch in patches:stack.enter_context(patch)
            result = image.prepare(size=image.EXPANDED_SIZE,approve_8gib=True)
            self.assertEqual((target/'disk.img').stat().st_size,image.EXPANDED_SIZE)
            self.assertEqual(result['schema_version'],2)
            self.assertEqual(result['image_size_bytes'],image.EXPANDED_SIZE)
            self.assertEqual(result['image_layout'],image.image_layout(image.EXPANDED_SIZE))
            self.assertIn(str(image.EXPANDED_SIZE-image.OFFSET),calls[0])
            self.assertEqual(json.loads((target/'creation-intent.json').read_text())['size'],image.EXPANDED_SIZE)
            self.assertFalse(result['exported']);self.assertFalse(result['mounted'])

    def test_default_1gib_lifecycle_keeps_legacy_receipt_without_new_approval(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            target, calls, patches = self._prepare_fixture(directory,size=image.LEGACY_SIZE)
            for patch in patches:stack.enter_context(patch)
            result = image.prepare()
            self.assertEqual((target/'disk.img').stat().st_size,image.LEGACY_SIZE)
            self.assertEqual(result['schema_version'],1)
            self.assertNotIn('image_size_bytes',result);self.assertNotIn('image_layout',result)
            self.assertIn(str(image.LEGACY_SIZE-image.OFFSET),calls[0])
            self.assertEqual(result['image_identity'][2],image.LEGACY_SIZE)
            self.assertFalse(result['exported']);self.assertFalse(result['mounted'])

    def test_invalid_or_unapproved_capacity_never_reaches_preflight(self):
        for size in (False,True,4*1024**3,16*1024**3,image.LEGACY_SIZE-1,float(image.LEGACY_SIZE),'8'):
            with self.subTest(size=size),mock.patch.object(image,'preflight') as preflight:
                with self.assertRaises(BackupError):image.prepare(size=size,approve_8gib=True)
                preflight.assert_not_called()
        for approval in (False,1,'yes'):
            with self.subTest(approval=approval),mock.patch.object(image,'preflight') as preflight:
                with self.assertRaisesRegex(RuntimeError,'explicit_8gib'):
                    image.prepare(size=image.EXPANDED_SIZE,approve_8gib=approval)
                preflight.assert_not_called()

    def test_8gib_loop_limit_cannot_reuse_legacy_limit(self):
        identity = (1,2,image.EXPANDED_SIZE)
        legacy = {'name':'/dev/loop7','back-file':str(image.IMAGE),'offset':image.OFFSET,
                  'sizelimit':image.LEGACY_SIZE-image.OFFSET,'ro':False}
        with mock.patch.object(image,'image_identity',return_value=identity),mock.patch.object(image,'loops',return_value=[legacy]):
            with self.assertRaisesRegex(RuntimeError,'exclusive_offset_loop_unverified'):
                image.verify_loop('/dev/loop7',identity,size=image.EXPANDED_SIZE)


if __name__ == "__main__":
    unittest.main()
