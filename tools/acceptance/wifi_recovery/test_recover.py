import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('wifi_recovery_operator', Path(__file__).with_name('recover.py'))
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
APPROVED = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
BOOT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
CFG = {'schema_version': 1, 'enabled': True, 'interface': 'wlan0', 'approved_uuid': APPROVED,
       'grace_failures': 3, 'cooldown_seconds': 300}


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        radio = root / 'rfkill0'
        radio.mkdir()
        for name, value in [('type', 'wlan'), ('soft', '0'), ('hard', '0')]:
            (radio / name).write_text(value)
        self.radio = radio
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(recovery, 'Path', return_value=root).start()

    def observe(self, *, state='100 (connected)', active=APPROVED, managed='yes', radio='enabled',
                ping=0, neighbour='REACHABLE', address='192.0.2.2/24', gateway='192.0.2.1', profile=None, neighbour_payload=None):
        fields = '\n'.join(['GENERAL.STATE:' + state, 'GENERAL.NM-MANAGED:' + managed,
                            'GENERAL.CON-UUID:' + active, 'IP4.ADDRESS[1]:' + address,
                            'IP4.GATEWAY:' + gateway]) + '\n'
        def run(args, timeout=8):
            if args[1:3] == ['radio', 'wifi']:
                return 0, radio + '\n'
            if 'device' in args:
                return 0, fields
            if 'connection.type,connection.interface-name' in args:
                return (0, '802-11-wireless\nwlan0\n') if profile is None else profile
            if args[0].endswith('/ping'):
                return ping, ''
            if args[0].endswith('/ip'):
                return 0, neighbour_payload if neighbour_payload is not None else json.dumps([{'dst': gateway, 'dev': 'wlan0', 'state': [neighbour]}])
            self.fail('Unexpected command: ' + repr(args))
        with mock.patch.object(recovery, 'command', side_effect=run) as called:
            result = recovery.observe(CFG)
        self.assertFalse(any('up' in call.args[0] for call in called.call_args_list))
        return result, called

    def test_healthy_approved_ipv4_link(self):
        self.assertEqual(self.observe()[0], 'healthy')

    def test_other_profile_is_never_replaced_or_probed(self):
        result, called = self.observe(active=OTHER)
        self.assertEqual(result, 'other_profile')
        self.assertEqual(called.call_count, 2)

    def test_radio_disabled(self):
        self.assertEqual(self.observe(radio='disabled')[0], 'unknown')

    def test_soft_and_hard_blocks(self):
        for flag in ('soft', 'hard'):
            with self.subTest(flag=flag):
                (self.radio / flag).write_text('1')
                self.assertEqual(self.observe()[0], 'unknown')
                (self.radio / flag).write_text('0')

    def test_unknown_and_connecting_states_do_not_recover(self):
        for state in ['0 (unknown)', '10 (unmanaged)', '20 (unavailable)', '40 (prepare)',
                      '50 (config)', '60 (need auth)', '70 (ip config)', '90 (secondaries)',
                      '110 (deactivating)', '120 (failed)']:
            with self.subTest(state=state):
                self.assertEqual(self.observe(state=state)[0], 'unknown')
        self.assertEqual(self.observe(managed='no')[0], 'unknown')

    def test_disconnected_approved_saved_profile_is_eligible(self):
        self.assertEqual(self.observe(state='30 (disconnected)', active='--')[0], 'disconnected')

    def test_missing_or_wrong_saved_profile_refuses(self):
        for profile in [(10, ''), (0, '802-3-ethernet\nwlan0\n'), (0, '802-11-wireless\nwlan1\n')]:
            self.assertEqual(self.observe(state='30 (disconnected)', active='--', profile=profile)[0], 'unknown')

    def test_only_combined_gateway_ping_failure_and_failed_neighbour_is_eligible(self):
        self.assertEqual(self.observe(ping=1, neighbour='FAILED')[0], 'layer2_failed')
        self.assertEqual(self.observe(ping=0, neighbour='FAILED')[0], 'healthy')

    def test_actual_filtered_ip_neighbour_shape_can_omit_device(self):
        actual = '[{"dst":"192.0.2.1","lladdr":"00:11:22:33:44:55","state":["STALE"]}]'
        self.assertEqual(self.observe(ping=1, neighbour_payload=actual)[0], 'healthy')
        failed = '[{"dst":"192.0.2.1","state":["FAILED"]}]'
        self.assertEqual(self.observe(ping=1, neighbour_payload=failed)[0], 'layer2_failed')

    def test_malformed_neighbour_rows_or_conflicting_device_are_unknown(self):
        for payload in ['[null]', '[true]', '["FAILED"]', 'null', '[{"dst":"192.0.2.1","dev":"usb0","state":["FAILED"]}]', 'invalid json']:
            with self.subTest(payload=payload):
                self.assertEqual(self.observe(ping=1, neighbour_payload=payload)[0], 'unknown')

    def test_ping_deadline_allows_default_three_arp_probes_and_still_refuses_incomplete(self):
        result, called = self.observe(ping=1, neighbour='INCOMPLETE')
        self.assertEqual(result, 'unknown')
        ping = next(call for call in called.call_args_list if call.args[0][0].endswith('/ping'))
        self.assertEqual(ping.args[0], ['/usr/bin/ping', '-I', 'wlan0', '-c', '1', '-W', '4', '192.0.2.1'])
        self.assertEqual(ping.kwargs, {'timeout': 6})

    def test_isp_dns_or_router_icmp_failure_with_reachable_neighbour_does_not_reset(self):
        self.assertEqual(self.observe(ping=1, neighbour='REACHABLE')[0], 'healthy')
        self.assertEqual(self.observe(ping=1, neighbour='STALE')[0], 'healthy')

    def test_ping_permission_error_or_unknown_neighbour_is_not_failure(self):
        for ping in [None, 2]:
            self.assertEqual(self.observe(ping=ping, neighbour='FAILED')[0], 'unknown')
        for neighbour in ['INCOMPLETE', 'INVALID']:
            self.assertEqual(self.observe(ping=1, neighbour=neighbour)[0], 'unknown')

    def test_missing_or_off_subnet_ipv4_is_unknown(self):
        for fields in [{'address': ''}, {'gateway': ''}, {'gateway': '198.51.100.1'}, {'gateway': 'not-an-ip'}]:
            self.assertEqual(self.observe(**fields)[0], 'unknown')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(recovery, 'STATE', self.state).start()
        mock.patch.object(recovery, 'OWNER', os.getuid()).start()
        mock.patch.object(recovery, 'parents').start()

    def write(self, value):
        recovery.save(value)

    def initial(self, failures=0, last=None, boot=BOOT):
        return {'schema_version': 1, 'boot_id': boot, 'failures': failures, 'last_attempt': last}

    def test_three_consecutive_failures_then_only_exact_approved_activation(self):
        with mock.patch.object(recovery, 'observe', return_value='disconnected'), mock.patch.object(recovery, 'command', return_value=(0, '')) as cmd:
            self.assertEqual(recovery.tick(CFG, BOOT, 100)['failures'], 1)
            self.assertEqual(recovery.tick(CFG, BOOT, 130)['failures'], 2)
            self.assertEqual(recovery.tick(CFG, BOOT, 160)['status'], 'activation_requested')
        cmd.assert_called_once_with(['/usr/bin/nmcli', '--wait', '25', 'connection', 'up', 'uuid', APPROVED, 'ifname', 'wlan0'], timeout=30)
        self.assertEqual(recovery.read_json(self.state / 'state.json'), self.initial(last=160))

    def test_unknown_healthy_other_profile_reset_consecutive_grace(self):
        for health in ['unknown', 'healthy', 'other_profile']:
            self.write(self.initial(failures=2))
            with mock.patch.object(recovery, 'observe', return_value=health), mock.patch.object(recovery, 'command') as cmd:
                self.assertEqual(recovery.tick(CFG, BOOT, 100)['failures'], 0)
                cmd.assert_not_called()

    def test_fresh_profile_change_or_radio_off_cancels_pending_activation(self):
        for changed in ['other_profile', 'unknown', 'healthy']:
            self.write(self.initial(failures=2))
            with mock.patch.object(recovery, 'observe', side_effect=['disconnected', changed]), mock.patch.object(recovery, 'command') as cmd:
                self.assertEqual(recovery.tick(CFG, BOOT, 100)['status'], 'changed_before_activation')
                cmd.assert_not_called()

    def test_cooldown_survives_fresh_reads_and_failure_or_timeout(self):
        self.write(self.initial(failures=2))
        def attempted(args, timeout):
            self.assertEqual(recovery.read_json(self.state / 'state.json')['last_attempt'], 100)
            return None, 'SYNTHETIC_SECRET_MUST_NOT_RETURN'
        with mock.patch.object(recovery, 'observe', return_value='layer2_failed'), mock.patch.object(recovery, 'command', side_effect=attempted) as cmd:
            result = recovery.tick(CFG, BOOT, 100)
            self.assertEqual(result, {'status': 'activation_failed', 'returncode': None})
            self.assertEqual(recovery.tick(CFG, BOOT, 399)['status'], 'cooldown')
            self.assertEqual(cmd.call_count, 1)
        self.assertNotIn('SECRET', repr(result))

    def test_new_boot_resets_counters_without_treating_old_monotonic_as_current(self):
        self.write(self.initial(failures=2, last=10000, boot=OTHER))
        with mock.patch.object(recovery, 'observe', return_value='healthy'), mock.patch.object(recovery, 'command') as cmd:
            recovery.tick(CFG, BOOT, 10)
            cmd.assert_not_called()
        self.assertEqual(recovery.read_json(self.state / 'state.json'), self.initial())

    def test_healthy_samples_do_not_rewrite_unchanged_state(self):
        self.write(self.initial())
        with mock.patch.object(recovery, 'observe', return_value='healthy'), mock.patch.object(recovery, 'save') as save:
            recovery.tick(CFG, BOOT, 100)
            save.assert_not_called()

    def test_corrupt_or_future_state_refuses_before_probe(self):
        for value in [self.initial(last=1000), dict(self.initial(), failures=True), dict(self.initial(), last_attempt=float('nan')), dict(self.initial(), last_attempt=10**1000), dict(self.initial(), boot_id={}), dict(self.initial(), boot_id=True)]:
            (self.state / 'state.json').write_text(json.dumps(value))
            (self.state / 'state.json').chmod(0o600)
            with mock.patch.object(recovery, 'observe') as observe, self.assertRaises(recovery.Refused):
                recovery.tick(CFG, BOOT, 100)
            observe.assert_not_called()

    def test_private_file_modes_symlinks_duplicates_and_hardlinks_refuse(self):
        path = self.state / 'probe.json'
        path.write_text('{"schema_version":1}')
        path.chmod(0o644)
        with self.assertRaises(recovery.Refused):
            recovery.read_json(path)
        path.chmod(0o600)
        alias = self.state / 'alias.json'
        alias.symlink_to(path)
        with self.assertRaises(OSError):
            recovery.read_json(alias)
        alias.unlink()
        os.link(path, alias)
        with self.assertRaises(recovery.Refused):
            recovery.read_json(path)
        alias.unlink()
        path.write_text('{"enabled":false,"enabled":true}')
        with self.assertRaises(recovery.Refused):
            recovery.read_json(path)

    def test_config_strict_uuid_interface_and_fixed_retry_policy(self):
        path = self.state / 'config.json'
        for change in [{'approved_uuid': 'shell;other'}, {'approved_uuid': {}}, {'approved_uuid': True}, {'interface': 'usb0'}, {'grace_failures': True},
                       {'cooldown_seconds': 0}, {'enabled': 1}, {'schema_version': True}]:
            path.write_text(json.dumps(dict(CFG, **change))); path.chmod(0o600)
            with mock.patch.object(recovery, 'CONFIG', path), self.assertRaises((recovery.Refused, ValueError)):
                recovery.config()


class GateTests(unittest.TestCase):
    def test_public_gate_precedes_config_guard_and_every_probe(self):
        with mock.patch.object(recovery, 'guard') as guard, mock.patch.object(recovery, 'config') as cfg, mock.patch.object(recovery, 'observe') as observe:
            self.assertEqual(recovery.main(), {'status': 'hardware_disabled'})
            guard.assert_not_called(); cfg.assert_not_called(); observe.assert_not_called()

    def test_disabled_private_config_performs_no_state_or_network_work(self):
        with mock.patch.object(recovery, 'APPROVED_TEST_BUILD', True), mock.patch.object(recovery, 'guard'), mock.patch.object(recovery, 'config', return_value=dict(CFG, enabled=False)), mock.patch.object(recovery, 'tick') as tick:
            self.assertEqual(recovery.main(), {'status': 'configuration_disabled'})
            tick.assert_not_called()

    def test_parent_ownership_and_writability_are_required(self):
        for owner, mode in [(0, stat.S_IFDIR | 0o777), (1, stat.S_IFDIR | 0o755), (0, stat.S_IFLNK | 0o777)]:
            path = mock.Mock()
            path.parents = [mock.Mock()]
            path.parents[0].lstat.return_value = mock.Mock(st_uid=owner, st_mode=mode)
            with mock.patch.object(recovery, 'OWNER', 0), self.assertRaises(recovery.Refused):
                recovery.parents(path)


if __name__ == '__main__':
    unittest.main()
