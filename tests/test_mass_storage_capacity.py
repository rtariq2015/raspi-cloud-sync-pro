"""Stage 3B policy tests; sparse header fixtures are not real exFAT acceptance."""

from contextlib import ExitStack
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from raspi_cloud_sync_pro import mass_storage_appliance as appliance
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController
from raspi_cloud_sync_pro.mass_storage_image import (
    EXPANDED_SIZE, LEGACY_SIZE, PARTITION_OFFSET, image_layout,
    validate_image_approval, validate_image_headers, validate_image_size,
)
from test_gadget_controller import SimulatedGadget


def approval(identity, version=2):
    value = {'schema_version': version, 'mass_storage_appliance_approved': True,
             'image_identity': list(identity)}
    if version == 2:
        value.update(image_size_bytes=identity[2], image_layout=image_layout(identity[2]))
    return value


def headers(size, partition_offset=0):
    mbr, boot = bytearray(512), bytearray(512)
    mbr[450] = 7
    mbr[454:458] = (PARTITION_OFFSET // 512).to_bytes(4, 'little')
    mbr[458:462] = image_layout(size)['partition_sectors'].to_bytes(4, 'little')
    mbr[510:] = boot[510:] = b'\x55\xaa'
    boot[3:11] = b'EXFAT   '
    boot[64:72] = partition_offset.to_bytes(8, 'little')
    boot[72:80] = image_layout(size)['partition_sectors'].to_bytes(8, 'little')
    boot[108] = 9
    return mbr, boot


class CapacityPolicyTests(unittest.TestCase):
    def test_only_exact_integer_1_or_8_gib_is_supported(self):
        for size in (LEGACY_SIZE, EXPANDED_SIZE):
            self.assertEqual(validate_image_size(size), size)
        for size in (0, True, False, LEGACY_SIZE - 1, LEGACY_SIZE + 1,
                     2*LEGACY_SIZE, 4*LEGACY_SIZE, 16*LEGACY_SIZE,
                     float(LEGACY_SIZE), str(EXPANDED_SIZE), None):
            with self.subTest(size=size), self.assertRaises(BackupError):
                validate_image_size(size)

    def test_legacy_schema_remains_1_gib_only(self):
        identity = (7, 88, LEGACY_SIZE)
        self.assertEqual(validate_image_approval(approval(identity, 1), identity), LEGACY_SIZE)
        expanded = (7, 88, EXPANDED_SIZE)
        with self.assertRaises(BackupError):
            validate_image_approval(approval(expanded, 1), expanded)

    def test_explicit_capacity_record_matches_exact_identity_and_layout(self):
        for size in (LEGACY_SIZE, EXPANDED_SIZE):
            identity = (7, 88, size)
            self.assertEqual(validate_image_approval(approval(identity), identity), size)
            self.assertEqual(image_layout(size)['partition_sectors'], size//512-2048)

    def test_stale_or_replaced_image_identity_never_reuses_approval(self):
        identity = (7, 88, EXPANDED_SIZE)
        valid = approval(identity)
        for actual in ((8, 88, EXPANDED_SIZE), (7, 89, EXPANDED_SIZE),
                       (7, 88, LEGACY_SIZE), (7, 88, 4*LEGACY_SIZE)):
            with self.subTest(actual=actual), self.assertRaises(BackupError):
                validate_image_approval(valid, actual)
        with self.assertRaises(BackupError):
            validate_image_approval(approval((7, 88, LEGACY_SIZE), 1), identity)

    def test_missing_extra_or_mutated_approval_fields_fail_closed(self):
        identity = (7, 88, EXPANDED_SIZE)
        valid = approval(identity)
        invalid = []
        for field in valid:
            changed = copy.deepcopy(valid); del changed[field]; invalid.append(changed)
        for field, value in (('extra', True), ('schema_version', 3),
                             ('schema_version', True), ('mass_storage_appliance_approved', 1),
                             ('image_size_bytes', float(EXPANDED_SIZE)),
                             ('image_size_bytes', LEGACY_SIZE),
                             ('image_identity', [7, 88.0, EXPANDED_SIZE])):
            changed = copy.deepcopy(valid); changed[field] = value; invalid.append(changed)
        for field, value in (('partition_table', 'gpt'), ('partition', True),
                             ('start_sector', 4096), ('partition_sectors', 2048),
                             ('filesystem', 'vfat'), ('bytes_per_sector', 4096), ('extra', 1)):
            changed = copy.deepcopy(valid); changed['image_layout'][field] = value; invalid.append(changed)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(BackupError):
                validate_image_approval(value, identity)

    def test_legacy_record_does_not_accept_new_fields_or_typed_aliases(self):
        identity = (7, 88, LEGACY_SIZE)
        for field, value in (('schema_version', True), ('mass_storage_appliance_approved', 1),
                             ('image_identity', [7.0, 88, LEGACY_SIZE]),
                             ('image_size_bytes', LEGACY_SIZE), ('extra', False)):
            changed = approval(identity, 1); changed[field] = value
            with self.subTest(field=field), self.assertRaises(BackupError):
                validate_image_approval(changed, identity)

    def test_valid_headers_support_existing_offset_loop_and_partition_offset(self):
        for size in (LEGACY_SIZE, EXPANDED_SIZE):
            for offset in (0, PARTITION_OFFSET//512):
                with self.subTest(size=size, offset=offset):
                    validate_image_headers(*headers(size, offset), size)

    def test_invalid_geometry_and_dirty_or_wrong_filesystem_headers_refused(self):
        changes = (
            ('mbr', 446, b'\x80'), ('mbr', 450, b'\x0c'),
            ('mbr', 454, (4096).to_bytes(4, 'little')),
            ('mbr', 458, (4096).to_bytes(4, 'little')), ('mbr', 462, b'\x01'),
            ('mbr', 510, b'\x00'), ('boot', 3, b'FAT32   '),
            ('boot', 64, (1).to_bytes(8, 'little')),
            ('boot', 72, (4096).to_bytes(8, 'little')), ('boot', 108, b'\x0c'),
            ('boot', 106, b'\x02\x00'), ('boot', 106, b'\x04\x00'),
            ('boot', 510, b'\x00'),
        )
        for size in (LEGACY_SIZE, EXPANDED_SIZE):
            for which, offset, value in changes:
                mbr, boot = headers(size)
                selected = mbr if which == 'mbr' else boot
                selected[offset:offset+len(value)] = value
                with self.subTest(size=size, field=(which, offset, value)), self.assertRaises(BackupError):
                    validate_image_headers(mbr, boot, size)
            for mbr, boot in ((headers(size)[0][:-1], headers(size)[1]),
                              (headers(size)[0], headers(size)[1][:-1])):
                with self.assertRaises(BackupError):
                    validate_image_headers(mbr, boot, size)


class ApplianceCapacityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-capacity-policy-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def sparse_image(self, size, name='header-only.img'):
        image = self.root/name
        mbr, boot = headers(size)
        with image.open('xb') as output:
            output.truncate(size)
            output.write(mbr); output.seek(PARTITION_OFFSET); output.write(boot)
        image.chmod(0o600)
        return image

    @staticmethod
    def backend(image):
        backend = object.__new__(appliance._ApplianceBackend)
        info = image.stat()
        backend.image_identity = info.st_dev, info.st_ino, info.st_size
        return backend

    def test_approved_8_gib_metadata_path_does_not_open_image_payload(self):
        settings = object(); identity = (7, 88, EXPANDED_SIZE)
        def model_only(path):
            self.assertEqual(path, Path('/proc/device-tree/model'))
            return b'Raspberry Pi Zero 2 W Rev 1.0\0'
        with ExitStack() as stack:
            for name, value in (('APPROVED_TEST_BUILD', True), ('_settings', None)):
                if name == '_settings':
                    stack.enter_context(mock.patch.object(appliance, name, return_value=settings))
                else:
                    stack.enter_context(mock.patch.object(appliance, name, value))
            stack.enter_context(mock.patch.object(appliance.os, 'getresuid', return_value=(0,)*3))
            stack.enter_context(mock.patch.object(appliance.os, 'getresgid', return_value=(0,)*3))
            stack.enter_context(mock.patch.object(appliance.os, 'readlink', return_value='same-namespace'))
            stack.enter_context(mock.patch.object(appliance.platform, 'system', return_value='Linux'))
            stack.enter_context(mock.patch.object(appliance.platform, 'machine', return_value='aarch64'))
            stack.enter_context(mock.patch.object(Path, 'read_bytes', autospec=True, side_effect=model_only))
            stack.enter_context(mock.patch.object(appliance, '_json', return_value=approval(identity)))
            stack.enter_context(mock.patch.object(appliance.LiveLinuxIO, 'regular_image', return_value=identity))
            opened = stack.enter_context(mock.patch.object(appliance.os, 'open'))
            read = stack.enter_context(mock.patch.object(appliance.os, 'pread'))
            appliance._approved(settings)
            opened.assert_not_called(); read.assert_not_called()

    def test_both_capacities_use_exclusive_fsck_before_sparse_header_reads(self):
        for size in (LEGACY_SIZE, EXPANDED_SIZE):
            image = self.sparse_image(size, str(size)+'.img'); backend = self.backend(image)
            seen = SimpleNamespace(bound=False, backing='', loops=('/dev/loop42',), mounts=())
            events = []; pread = os.pread
            def record_read(*args):
                events.append('header-read'); return pread(*args)
            with mock.patch.object(appliance, 'IMAGE', image), \
                    mock.patch.object(appliance, 'ROOT_UID', os.getuid()), \
                    mock.patch.object(backend, 'observe', return_value=seen), \
                    mock.patch.object(appliance.LinuxGadgetBackend, 'check_filesystem', side_effect=lambda loop: events.append('fsck')), \
                    mock.patch.object(appliance.os, 'pread', side_effect=record_read):
                backend.check_filesystem('/dev/loop42')
            self.assertEqual(events, ['fsck', 'header-read', 'header-read'])
            self.assertEqual(image.stat().st_size, size)

    def test_unsafe_ownership_prevents_filesystem_check_and_payload_open(self):
        image = self.sparse_image(EXPANDED_SIZE); backend = self.backend(image)
        valid = {'bound': False, 'backing': '', 'loops': ('/dev/loop42',), 'mounts': ()}
        for change in ({'bound': True}, {'backing': str(image)}, {'mounts': ('untrusted',)},
                       {'loops': ()}, {'loops': ('/dev/loop99',)},
                       {'loops': ('/dev/loop42', '/dev/loop99')}):
            with self.subTest(change=change), \
                    mock.patch.object(backend, 'observe', return_value=SimpleNamespace(**{**valid, **change})), \
                    mock.patch.object(appliance.LinuxGadgetBackend, 'check_filesystem') as checker, \
                    mock.patch.object(appliance.os, 'open') as opened, \
                    mock.patch.object(appliance.os, 'pread') as read, self.assertRaises(BackupError):
                backend.check_filesystem('/dev/loop42')
            checker.assert_not_called(); opened.assert_not_called(); read.assert_not_called()

    def test_fsck_failure_never_advances_to_header_reads(self):
        image = self.sparse_image(EXPANDED_SIZE); backend = self.backend(image)
        seen = SimpleNamespace(bound=False, backing='', loops=('/dev/loop42',), mounts=())
        with mock.patch.object(backend, 'observe', return_value=seen), \
                mock.patch.object(appliance.LinuxGadgetBackend, 'check_filesystem', side_effect=BackupError('dirty_filesystem')), \
                mock.patch.object(appliance.os, 'open') as opened, self.assertRaises(BackupError):
            backend.check_filesystem('/dev/loop42')
        opened.assert_not_called()

    def test_stale_pinned_size_or_inode_rejected_before_header_reads(self):
        image = self.sparse_image(EXPANDED_SIZE); backend = self.backend(image)
        original = backend.image_identity
        seen = SimpleNamespace(bound=False, backing='', loops=('/dev/loop42',), mounts=())
        for identity in ((original[0], original[1], LEGACY_SIZE),
                         (original[0], original[1]+1, EXPANDED_SIZE)):
            backend.image_identity = identity
            with self.subTest(identity=identity), mock.patch.object(appliance, 'IMAGE', image), \
                    mock.patch.object(appliance, 'ROOT_UID', os.getuid()), \
                    mock.patch.object(backend, 'observe', return_value=seen), \
                    mock.patch.object(appliance.LinuxGadgetBackend, 'check_filesystem'), \
                    mock.patch.object(appliance.os, 'pread') as read, self.assertRaises(BackupError):
                backend.check_filesystem('/dev/loop42')
            read.assert_not_called()

    def test_named_image_replacement_during_header_read_is_refused(self):
        image = self.sparse_image(EXPANDED_SIZE); backend = self.backend(image)
        seen = SimpleNamespace(bound=False, backing='', loops=('/dev/loop42',), mounts=())
        pread = os.pread; count = 0
        def replace_after_read(*args):
            nonlocal count
            result = pread(*args); count += 1
            if count == 2:
                image.rename(self.root/'original-retained.img')
                self.sparse_image(EXPANDED_SIZE)
            return result
        with mock.patch.object(appliance, 'IMAGE', image), mock.patch.object(appliance, 'ROOT_UID', os.getuid()), \
                mock.patch.object(backend, 'observe', return_value=seen), \
                mock.patch.object(appliance.LinuxGadgetBackend, 'check_filesystem'), \
                mock.patch.object(appliance.os, 'pread', side_effect=replace_after_read), self.assertRaises(BackupError):
            backend.check_filesystem('/dev/loop42')
        self.assertTrue((self.root/'original-retained.img').exists()); self.assertTrue(image.exists())

    def test_stale_controller_journal_capacity_or_inode_is_not_rebound(self):
        for changed in ((1, 2, EXPANDED_SIZE), (1, 3, LEGACY_SIZE)):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); backend = SimulatedGadget(root)
                backend.image_identity = (1, 2, LEGACY_SIZE)
                state = root/'state'; staging = root/'staging'; staging.mkdir()
                first = GadgetController(backend, state)
                with first.session():
                    first.journal.save('host', image=str(backend.image), image_identity=list(backend.image_identity), loop=None)
                original = (state/'controller.json').read_bytes()
                backend.image_identity = changed
                restarted = GadgetController(backend, state)
                with restarted.session(), mock.patch.object(backend, 'observe') as observe, self.assertRaises(BackupError):
                    restarted.start(cold_start=True, staging_directory=staging)
                observe.assert_not_called(); self.assertEqual(backend.events, [])
                self.assertEqual((state/'controller.json').read_bytes(), original)

    def test_stale_capture_inventory_capacity_or_inode_is_not_rebound(self):
        for old_identity in ((1, 2, LEGACY_SIZE), (1, 3, EXPANDED_SIZE)):
            with self.subTest(old_identity=old_identity), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); backend = SimulatedGadget(root)
                backend.image_identity = (1, 2, EXPANDED_SIZE)
                state = root/'state'; staging = root/'staging'; staging.mkdir()
                first = GadgetController(backend, state)
                with first.session():
                    first.journal.save('host', image=str(backend.image), image_identity=list(backend.image_identity), loop=None)
                    first.journal.save_capture(backend.image, old_identity, ())
                original = {name:(state/name).read_bytes() for name in ('controller.json', 'capture-manifest.json')}
                restarted = GadgetController(backend, state)
                with restarted.session(), mock.patch.object(backend, 'observe') as observe, self.assertRaises(BackupError):
                    restarted.start(cold_start=True, staging_directory=staging)
                observe.assert_not_called(); self.assertEqual(backend.events, [])
                self.assertEqual({name:(state/name).read_bytes() for name in original}, original)


if __name__ == '__main__':
    unittest.main()
