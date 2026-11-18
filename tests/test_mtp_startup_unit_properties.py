"""Read-only systemd property contracts, with no Linux/device initialization."""
import unittest
from unittest.mock import patch

from raspi_cloud_sync_pro import mtp_startup as startup


COMMON = {'ActiveState', 'SubState', 'UnitFileState', 'FragmentPath', 'DropInPaths'}
TARGET = 'usb-gadget.target'
# Sanitized from the retained Pi systemd response: exact five-property shape,
# ordering, states and empty drop-ins; no private paths, account or host data.
TARGET_OUTPUT = '''ActiveState=active
SubState=active
FragmentPath=/usr/lib/systemd/system/usb-gadget.target
DropInPaths=
UnitFileState=static
'''
SERVICE = 'raspi-cloud-mtp-completion.service'
SERVICE_PROPERTIES = {
    'ActiveState': 'active', 'SubState': 'running', 'MainPID': '1234',
    'UnitFileState': 'static',
    'FragmentPath': '/etc/systemd/system/raspi-cloud-mtp-completion.service',
    'DropInPaths': '',
}


def properties(values):
    return ''.join(key + '=' + value + '\n' for key, value in values.items())


class UnitPropertyContractTests(unittest.TestCase):
    def setUp(self):
        # __init__ intentionally avoided: only _command is mocked, and the
        # actual parser executes without opening configuration or Linux paths.
        self.io = object.__new__(startup.LinuxStartupIO)

    def parse(self, unit, output):
        with patch.object(startup, '_command', return_value=output) as command:
            result = self.io._unit(unit)
        return result, command

    def refuse(self, unit, output):
        with patch.object(startup, '_command', return_value=output) as command:
            with self.assertRaises(startup.StartupError):
                self.io._unit(unit)
        command.assert_called_once()

    def test_captured_target_response_passes_without_inventing_mainpid(self):
        result, _ = self.parse(TARGET, TARGET_OUTPUT)
        self.assertEqual(set(result), COMMON)
        self.assertNotIn('MainPID', result)
        self.assertEqual(result['ActiveState'], 'active')
        self.assertEqual(result['SubState'], 'active')
        self.assertEqual(result['UnitFileState'], 'static')
        self.assertEqual(result['DropInPaths'], '')

    def test_target_query_requests_only_its_five_applicable_properties(self):
        _, command = self.parse(TARGET, TARGET_OUTPUT)
        args = command.call_args.args[0]
        self.assertEqual(args[:5], ['/usr/bin/systemctl', 'show', '--all', '--no-pager', TARGET])
        self.assertEqual(len(args), 6)
        self.assertTrue(args[5].startswith('--property='))
        self.assertEqual(set(args[5].removeprefix('--property=').split(',')), COMMON)
        self.assertNotIn('MainPID', args[5])

    def test_inactive_service_pid_zero_is_valid_observation(self):
        values = {**SERVICE_PROPERTIES, 'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '0'}
        result, _ = self.parse(SERVICE, properties(values))
        self.assertEqual(result, values)
        self.assertEqual(result['MainPID'], '0')

    def test_active_service_positive_ascii_pid_is_preserved(self):
        result, _ = self.parse(SERVICE, properties(SERVICE_PROPERTIES))
        self.assertEqual(result, SERVICE_PROPERTIES)
        self.assertEqual(result['MainPID'], '1234')

    def test_service_query_retains_all_six_properties_including_mainpid(self):
        _, command = self.parse(SERVICE, properties(SERVICE_PROPERTIES))
        args = command.call_args.args[0]
        self.assertEqual(args[:5], ['/usr/bin/systemctl', 'show', '--all', '--no-pager', SERVICE])
        self.assertEqual(len(args), 6)
        self.assertEqual(set(args[5].removeprefix('--property=').split(',')), COMMON | {'MainPID'})

    def test_service_still_requires_mainpid(self):
        self.refuse(SERVICE, properties({key: value for key, value in SERVICE_PROPERTIES.items()
                                         if key != 'MainPID'}))

    def test_service_rejects_invalid_pid_text(self):
        invalid = ('', '-1', '+1', ' 1', '1 ', '\t1', '1\t', '1.0', '0x10',
                   'True', 'False', 'true', 'false', '1_000', '١٢٣', '１２３', 'NaN')
        for value in invalid:
            with self.subTest(value=value):
                self.refuse(SERVICE, properties({**SERVICE_PROPERTIES, 'MainPID': value}))

    def test_every_common_field_remains_required_for_both_unit_types(self):
        target = dict(line.split('=', 1) for line in TARGET_OUTPUT.splitlines())
        for unit, values in ((TARGET, target), (SERVICE, SERVICE_PROPERTIES)):
            for key in COMMON:
                with self.subTest(unit=unit, missing=key):
                    self.refuse(unit, properties({name: value for name, value in values.items() if name != key}))

    def test_unknown_property_is_refused_for_both_unit_types(self):
        for unit, output in ((TARGET, TARGET_OUTPUT), (SERVICE, properties(SERVICE_PROPERTIES))):
            with self.subTest(unit=unit):
                self.refuse(unit, output + 'Description=unexpected\n')

    def test_duplicate_common_property_is_refused_even_if_value_matches(self):
        for unit, output in ((TARGET, TARGET_OUTPUT), (SERVICE, properties(SERVICE_PROPERTIES))):
            with self.subTest(unit=unit):
                self.refuse(unit, output + 'ActiveState=active\n')

    def test_duplicate_service_pid_is_refused(self):
        self.refuse(SERVICE, properties(SERVICE_PROPERTIES) + 'MainPID=1234\n')

    def test_target_rejects_unexpected_mainpid_instead_of_ignoring_it(self):
        for value in ('0', '1234', ''):
            with self.subTest(value=value):
                self.refuse(TARGET, TARGET_OUTPUT + 'MainPID=' + value + '\n')

    def test_unsupported_unit_suffix_is_refused_before_any_query(self):
        for unit in ('raspi-cloud-mtp.socket', 'raspi-cloud-mtp.mount', 'raspi-cloud-mtp',
                     'raspi-cloud-mtp.Target', 'raspi-cloud-mtp.target.extra'):
            with self.subTest(unit=unit), patch.object(startup, '_command') as command:
                with self.assertRaises(startup.StartupError):
                    self.io._unit(unit)
                command.assert_not_called()

    def test_malformed_property_lines_are_refused(self):
        for unit, output in ((TARGET, TARGET_OUTPUT), (SERVICE, properties(SERVICE_PROPERTIES))):
            for extra in ('invalid-line\n', '=unrecognized-empty-key\n', '\n'):
                with self.subTest(unit=unit, extra=extra):
                    self.refuse(unit, output + extra)

    def test_running_pid_zero_does_not_pass_component_readiness(self):
        settings = startup.StartupSettings(1234, 1234, 100)
        with patch.object(startup, '_command', return_value=properties({**SERVICE_PROPERTIES, 'MainPID': '0'})):
            with self.assertRaises(startup.StartupError):
                self.io.ready(settings)

    def test_running_pid_one_does_not_pass_component_readiness(self):
        settings = startup.StartupSettings(1234, 1234, 100)
        with patch.object(startup, '_command', return_value=properties({**SERVICE_PROPERTIES, 'MainPID': '1'})):
            with self.assertRaises(startup.StartupError):
                self.io.ready(settings)


if __name__ == '__main__':
    unittest.main()
