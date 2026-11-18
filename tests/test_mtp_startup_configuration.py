"""Disposable config-file and early-approval gates; never run root commands."""
from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from raspi_cloud_sync_pro import mtp_startup as startup
from raspi_cloud_sync_pro.config import ConfigError


class StartupConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'startup.json'
        self.path.write_text('{"enabled":false}')
        self.path.chmod(0o600)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(startup, 'CONFIG', self.path))
        self.stack.enter_context(patch.object(startup, '_protected', return_value=None))
        self.uid = 0
        fstat, lstat = os.fstat, Path.lstat
        def root_info(info):
            # Only ownership is simulated: all identity/mode/link/size fields
            # come from the actual disposable file and remain subject to checks.
            values = {name: getattr(info, name) for name in
                      ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
                       'st_uid', 'st_gid', 'st_mode', 'st_nlink')}
            values['st_uid'] = self.uid
            return SimpleNamespace(**values)
        self.stack.enter_context(patch.object(os, 'fstat', side_effect=lambda fd: root_info(fstat(fd))))
        self.stack.enter_context(patch.object(Path, 'lstat', lambda path: root_info(lstat(path))))

    def test_private_regular_configuration_reads_without_any_command(self):
        with patch.object(startup, '_command', side_effect=AssertionError('No commands permitted')):
            self.assertEqual(startup._startup_configuration(), {'enabled': False})

    def test_exact_128kib_valid_manifest_is_accepted(self):
        overhead = len(json.dumps({'pad': ''}, separators=(',', ':')).encode())
        value = {'pad': 'x' * (128 * 1024 - overhead)}
        self.path.write_text(json.dumps(value, separators=(',', ':')))
        self.assertEqual(self.path.stat().st_size, 128 * 1024)
        self.assertEqual(startup._startup_configuration(), value)

    def test_oversize_file_is_refused_before_read(self):
        self.path.write_bytes(b' ' * (128 * 1024 + 1))
        with patch.object(os, 'read', side_effect=AssertionError('Oversize file must not be read')) as read:
            with self.assertRaises(startup.StartupError):
                startup._startup_configuration()
            read.assert_not_called()

    def test_empty_file_is_refused(self):
        self.path.write_bytes(b'')
        with self.assertRaises(startup.StartupError):
            startup._startup_configuration()

    def test_duplicate_json_keys_are_refused(self):
        self.path.write_text('{"enabled":true,"enabled":false}')
        with self.assertRaises(ConfigError):
            startup._startup_configuration()

    def test_configuration_requires_exact_private_mode(self):
        for mode in (0o644, 0o640, 0o400, 0o666):
            with self.subTest(mode=mode):
                self.path.chmod(mode)
                with self.assertRaises(startup.StartupError):
                    startup._startup_configuration()

    def test_nonroot_owner_is_refused(self):
        self.uid = os.getuid() or 1234
        with self.assertRaises(startup.StartupError):
            startup._startup_configuration()

    def test_symlink_is_refused_by_nofollow_open(self):
        real = self.root / 'regular.json'
        self.path.rename(real)
        self.path.symlink_to(real)
        with self.assertRaises(OSError) as error:
            startup._startup_configuration()
        self.assertEqual(error.exception.errno, errno.ELOOP)
        self.assertEqual(real.read_text(), '{"enabled":false}')

    def test_hardlinked_configuration_is_refused(self):
        alias = self.root / 'alias.json'
        os.link(self.path, alias)
        with self.assertRaises(startup.StartupError):
            startup._startup_configuration()
        self.assertEqual(alias.read_text(), '{"enabled":false}')

    def test_replaced_path_while_descriptor_is_held_is_refused(self):
        read = os.read
        def replace_after_read(fd, size):
            data = read(fd, size)
            candidate = self.root / 'replacement.json'
            candidate.write_bytes(data)
            candidate.chmod(0o600)
            os.replace(candidate, self.path)
            return data
        with patch.object(os, 'read', side_effect=replace_after_read):
            with self.assertRaises(startup.StartupError) as error:
                startup._startup_configuration()
        self.assertEqual(error.exception.code, 'mtp_startup_configuration_changed')
        self.assertEqual(self.path.read_text(), '{"enabled":false}')

    def test_content_mutation_while_reading_is_refused(self):
        read = os.read
        def mutate_after_read(fd, size):
            data = read(fd, size)
            before = self.path.stat()
            self.path.write_text('{"enabled":true} ')
            os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000))
            return data
        with patch.object(os, 'read', side_effect=mutate_after_read):
            with self.assertRaises(startup.StartupError) as error:
                startup._startup_configuration()
        self.assertEqual(error.exception.code, 'mtp_startup_configuration_changed')

    def test_short_read_is_refused_even_if_remaining_json_would_parse(self):
        self.path.write_text('{"enabled":false} ')
        read = os.read
        with patch.object(os, 'read', side_effect=lambda fd, size: read(fd, size)[:-1]):
            with self.assertRaises(startup.StartupError):
                startup._startup_configuration()


class StartupApprovalGateTests(unittest.TestCase):
    def setUp(self):
        self.settings = startup.StartupSettings(1234, 1234, 100)
        self.io = object.__new__(startup.LinuxStartupIO)
        self.value = {'schema_version': 1, 'enabled': True, 'source_uid': 1234, 'source_gid': 1234,
                      'max_power_ma': 100, 'udc': self.settings.udc, 'files': {}, 'directories': {},
                      'parent_entries': {}}
        self.io.approved = dict(self.value)
        self.io.configuration_hash = 'a' * 64
        self.approval = {'schema_version': 1, 'mtp_startup_approved': True,
                         'configuration_sha256': 'a' * 64}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(os, 'uname', return_value=SimpleNamespace(machine='aarch64')))
        self.stack.enter_context(patch.object(Path, 'read_text', return_value='Raspberry Pi Zero 2 W fixture'))
        self.stack.enter_context(patch.object(Path, 'readlink', return_value=Path('mnt:[fixture-host]')))
        self.stack.enter_context(patch.object(startup, '_startup_configuration', side_effect=lambda: dict(self.value)))
        self.stack.enter_context(patch.object(startup, '_digest', return_value='a' * 64))
        def configuration(path):
            if path == startup.APPROVAL:
                return dict(self.approval)
            raise AssertionError('Rejected approval unexpectedly reached private runtime files')
        self.stack.enter_context(patch.object(startup, '_read_configuration', side_effect=configuration))
        self.lookup = self.stack.enter_context(patch.object(startup.pwd, 'getpwnam', side_effect=AssertionError('Rejected approval must precede runtime access')))
        self.command = self.stack.enter_context(patch.object(startup, '_command', side_effect=AssertionError('No commands permitted')))

    def test_changed_configuration_precedes_runtime_access(self):
        self.value['max_power_ma'] = 101
        with self.assertRaises(startup.StartupError):
            self.io.preflight(self.settings)
        self.lookup.assert_not_called()

    def test_disabled_configuration_precedes_runtime_access(self):
        self.value['enabled'] = False
        self.io.approved = dict(self.value)
        with self.assertRaises(startup.StartupError):
            self.io.preflight(self.settings)
        self.lookup.assert_not_called()

    def test_wrong_approval_hash_precedes_runtime_access(self):
        self.approval['configuration_sha256'] = 'b' * 64
        with self.assertRaises(startup.StartupError):
            self.io.preflight(self.settings)
        self.lookup.assert_not_called()

    def test_false_approval_precedes_runtime_access(self):
        self.approval['mtp_startup_approved'] = False
        with self.assertRaises(startup.StartupError):
            self.io.preflight(self.settings)
        self.lookup.assert_not_called()

    def test_foreign_mount_namespace_precedes_configuration_or_runtime(self):
        with patch.object(Path, 'readlink', side_effect=[Path('mnt:[private]'), Path('mnt:[host]')]):
            with self.assertRaises(startup.StartupError) as error:
                self.io.preflight(self.settings)
        self.assertEqual(error.exception.code, 'mtp_startup_host_namespace_required')
        self.lookup.assert_not_called()

    def test_approval_rejects_boolean_integer_aliases(self):
        for field, value in (('schema_version', True), ('mtp_startup_approved', 1)):
            with self.subTest(field=field):
                before = dict(self.approval)
                self.approval[field] = value
                try:
                    with self.assertRaises(startup.StartupError):
                        self.io.preflight(self.settings)
                    self.lookup.assert_not_called()
                finally:
                    self.approval = before


class StartupSettingsTests(unittest.TestCase):
    def test_invalid_settings_cannot_select_another_device_or_privileged_user(self):
        cases = [(0, 1234, 100, '3f980000.usb'), (True, 1234, 100, '3f980000.usb'),
                 (1234, 0, 100, '3f980000.usb'), (1234, True, 100, '3f980000.usb'),
                 (1234, 1234, 501, '3f980000.usb'), (1234, 1234, 100, 'other-udc')]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(startup.StartupError):
                startup.StartupSettings(*values)


if __name__ == '__main__':
    unittest.main()
