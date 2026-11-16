"""Protected host-check unit pin requirements; no real kernel or command IO."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from raspi_cloud_sync_pro import mtp_startup as startup


class PinsAccepted(Exception):
    """Stop the disposable fixture after file verification, before directory IO."""


class HostCheckManifestTests(unittest.TestCase):
    def setUp(self):
        self.settings = startup.StartupSettings(1234, 1234, 100)
        self.io = object.__new__(startup.LinuxStartupIO)
        self.io.configuration_hash = 'a' * 64
        self.io.boot_id = 'fixture-boot'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        units = (startup.BROKER, startup.RESPONDER, startup.HOST_CHECK,
                 'raspi-cloud-mtp-prepare.service', 'raspi-cloud-mtp-bind.service',
                 'raspi-cloud-mtp.target', *startup.CLAIMERS)
        self.unit_values = {}
        for unit in units:
            self.unit_values[unit] = {
                'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '0',
                'UnitFileState': 'disabled', 'FragmentPath': '/etc/systemd/system/' + unit,
                'DropInPaths': '',
            }
        self.host_fragment = self.unit_values[startup.HOST_CHECK]['FragmentPath']
        self.host_dropin = '/etc/systemd/system/' + startup.HOST_CHECK + '.d/10-fixture.conf'
        self.unit_values[startup.HOST_CHECK]['DropInPaths'] = self.host_dropin
        files = {str(startup.PREFIX / name): 'a' * 64 for name in
                 ('umtprd', 'umtprd.conf', 'complete-object', 'serve-completions', 'start-mtp')}
        files.update({str(startup.PREFIX / 'site/raspi_cloud_sync_pro' / name): 'a' * 64
                      for name in ('__init__.py', 'config.py', 'mtp_ingest.py', 'mtp_broker.py',
                                   'mtp_startup.py', 'errors.py')})
        files[str(startup.COMPLETION_CONFIG)] = 'a' * 64
        files.update({value['FragmentPath']: 'a' * 64 for value in self.unit_values.values()})
        files[self.host_dropin] = 'a' * 64
        self.value = {
            'schema_version': 1, 'enabled': True, 'source_uid': 1234, 'source_gid': 1234,
            'max_power_ma': 100, 'udc': self.settings.udc, 'files': files,
            'directories': {str(path): [1, 2, 0, 0, 0o40700] for path in
                            (startup.ROOT, startup.EXPORT.parent, startup.EXPORT,
                             startup.WORK, startup.STAGING)},
            'parent_entries': {},
        }
        self.io.approved = deepcopy(self.value)
        self.io._unit = Mock(side_effect=lambda unit: dict(self.unit_values[unit]))
        self.actual_hashes = dict(files)
        def digest(path):
            if path == startup.CONFIG:
                return self.io.configuration_hash
            return self.actual_hashes[str(path)]
        self.digest = self.stack.enter_context(patch.object(startup, '_digest', side_effect=digest))
        self.stack.enter_context(patch.object(startup, '_startup_configuration', side_effect=lambda: deepcopy(self.value)))
        self.stack.enter_context(patch.object(startup, '_read_configuration', return_value={
            'schema_version': 1, 'mtp_startup_approved': True,
            'configuration_sha256': self.io.configuration_hash}))
        self.stack.enter_context(patch.object(startup.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(startup.os, 'uname', return_value=SimpleNamespace(machine='aarch64')))
        self.stack.enter_context(patch.object(startup.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1234, pw_gid=1234)))
        def text(path, *args, **kwargs):
            if path == Path('/proc/device-tree/model'):
                return 'Raspberry Pi Zero 2 W fixture'
            raise AssertionError('Unexpected real file read')
        self.stack.enter_context(patch.object(Path, 'read_text', text))
        self.stack.enter_context(patch.object(Path, 'readlink', return_value=Path('mnt:[fixture-host]')))
        def children(path):
            if path == Path('/sys/module'):
                return iter(())
            if path == startup.UDCS:
                return iter((startup.UDCS / self.settings.udc,))
            raise AssertionError('Unexpected directory inventory')
        self.stack.enter_context(patch.object(Path, 'iterdir', children))
        device = Path('/sys/devices/platform/fixture') / self.settings.udc
        resolves = {
            startup.UDCS / self.settings.udc / 'device': device,
            device / 'driver': Path('/sys/bus/platform/drivers/dwc2'),
            device / 'driver' / device.name: device,
        }
        def resolve(path, *args, **kwargs):
            if path in resolves:
                return resolves[path]
            raise AssertionError('Unexpected path resolution')
        self.stack.enter_context(patch.object(Path, 'resolve', resolve))
        self.stack.enter_context(patch.object(startup, '_mounts', return_value=[{
            'id': '10', 'target': startup.GADGETS, 'options': ['rw'],
            'fs': 'configfs', 'source': 'configfs'}]))
        self.protected = self.stack.enter_context(patch.object(startup, '_protected', side_effect=PinsAccepted))
        self.command = self.stack.enter_context(patch.object(startup, '_command', side_effect=AssertionError('No real commands permitted')))

    def test_reviewed_host_fragment_and_dropin_bytes_pass_pin_boundary(self):
        with self.assertRaises(PinsAccepted):
            self.io.preflight(self.settings)
        checked = {str(call.args[0]) for call in self.digest.call_args_list}
        self.assertTrue({self.host_fragment, self.host_dropin} <= checked)
        self.protected.assert_called_once()
        self.command.assert_not_called()

    def test_host_check_effective_fragment_must_be_in_approved_manifest(self):
        del self.value['files'][self.host_fragment]
        self.io.approved = deepcopy(self.value)
        with self.assertRaises(startup.StartupError) as error:
            self.io.preflight(self.settings)
        self.assertEqual(error.exception.code, 'mtp_startup_unit_unpinned')
        self.protected.assert_not_called()

    def test_host_check_effective_dropin_must_be_in_approved_manifest(self):
        del self.value['files'][self.host_dropin]
        self.io.approved = deepcopy(self.value)
        with self.assertRaises(startup.StartupError) as error:
            self.io.preflight(self.settings)
        self.assertEqual(error.exception.code, 'mtp_startup_unit_unpinned')
        self.protected.assert_not_called()

    def test_changed_host_fragment_bytes_refuse_before_directory_or_device_io(self):
        self.actual_hashes[self.host_fragment] = 'b' * 64
        with self.assertRaises(startup.StartupError) as error:
            self.io.preflight(self.settings)
        self.assertEqual(error.exception.code, 'mtp_startup_runtime_changed')
        self.protected.assert_not_called()
        self.command.assert_not_called()

    def test_changed_host_dropin_bytes_refuse_before_directory_or_device_io(self):
        self.actual_hashes[self.host_dropin] = 'b' * 64
        with self.assertRaises(startup.StartupError) as error:
            self.io.preflight(self.settings)
        self.assertEqual(error.exception.code, 'mtp_startup_runtime_changed')
        self.protected.assert_not_called()
        self.command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
