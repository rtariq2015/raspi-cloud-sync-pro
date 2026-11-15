"""Source-only appliance hold checks: temporary files and simulated USB only."""

from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
import errno
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from raspi_cloud_sync_pro import mass_storage_appliance as appliance
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController
from raspi_cloud_sync_pro.maintenance import MaintenanceHold
from test_gadget_controller import SimulatedGadget


@contextmanager
def ordinary_directory(path, *unused):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


class MaintenanceApplianceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-maintenance-appliance-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state, self.private, self.public, self.mount = (
            self.root/name for name in ('state', 'private', 'published', 'mount'))
        for path in (self.state, self.private, self.public, self.mount):
            path.mkdir(mode=0o700)
        self.account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        self.stack = ExitStack();self.addCleanup(self.stack.close)
        for name, path in (('STATE', self.state), ('PRIVATE_STAGING', self.private),
                           ('STAGING', self.public), ('MOUNT', self.mount)):
            self.stack.enter_context(mock.patch.object(appliance, name, path))

    def enabled_admin(self):
        self.stack.enter_context(mock.patch.object(appliance, 'APPROVED_TEST_BUILD', True))
        self.stack.enter_context(mock.patch.object(appliance, 'MAINTENANCE_HOLD_ENABLED', True))

    def admin_boundaries(self):
        self.enabled_admin()
        settings = object()
        configured = self.stack.enter_context(mock.patch.object(appliance, '_settings', return_value=settings))
        approved = self.stack.enter_context(mock.patch.object(appliance, '_approved'))
        self.stack.enter_context(mock.patch.object(appliance, '_root_directory', ordinary_directory))
        operator = mock.Mock()
        self.stack.enter_context(mock.patch.object(appliance, '_ApplianceIO', return_value=operator))
        backend = mock.Mock()
        self.stack.enter_context(mock.patch.object(appliance, '_ApplianceBackend', return_value=backend))
        return settings, configured, approved, operator, backend

    def test_default_and_each_disabled_build_gate_precede_configuration(self):
        for approved_build, maintenance in ((False, False), (False, True), (True, False)):
            with self.subTest(approved_build=approved_build, maintenance=maintenance), \
                    mock.patch.object(appliance, 'APPROVED_TEST_BUILD', approved_build), \
                    mock.patch.object(appliance, 'MAINTENANCE_HOLD_ENABLED', maintenance), \
                    mock.patch.object(appliance, '_settings') as settings, \
                    mock.patch.object(appliance, '_ApplianceIO') as operator:
                result = appliance.run_mass_storage_maintenance('request', approved=True)
            self.assertEqual(result, {'maintenance_state':'disabled'})
            settings.assert_not_called();operator.assert_not_called()

    def test_missing_or_numeric_action_approval_refuses_before_configuration(self):
        self.enabled_admin()
        for approval in (False, 1, 'yes', None):
            with self.subTest(approval=approval), mock.patch.object(appliance, '_settings') as settings, \
                    mock.patch.object(appliance, '_ApplianceIO') as operator:
                result = appliance.run_mass_storage_maintenance('request', approved=approval)
            self.assertEqual(result, {'maintenance_state':'failed_review_required'})
            settings.assert_not_called();operator.assert_not_called()

    def test_existing_root_approval_guard_cannot_be_bypassed_by_admin_flags(self):
        self.enabled_admin()
        with mock.patch.object(appliance, '_settings', return_value=object()), \
                mock.patch.object(appliance.os, 'getresuid', return_value=(1000,)*3), \
                mock.patch.object(appliance, '_ApplianceIO') as operator:
            result = appliance.run_mass_storage_maintenance('request', approved=True)
        self.assertEqual(result, {'maintenance_state':'failed_review_required'})
        operator.assert_not_called()

    def test_pi_or_image_approval_failure_prevents_io_and_hides_private_errors(self):
        self.enabled_admin()
        with mock.patch.object(appliance, '_settings', return_value=object()), \
                mock.patch.object(appliance, '_approved', side_effect=BackupError('PRIVATE-fixture')), \
                mock.patch.object(appliance, '_ApplianceIO') as operator:
            result = appliance.run_mass_storage_maintenance('request', approved=True)
        self.assertEqual(result, {'maintenance_state':'failed_review_required'})
        self.assertNotIn('PRIVATE', json.dumps(result));operator.assert_not_called()

    def test_request_reuses_approved_identity_and_has_no_device_or_service_commands(self):
        settings, _, approved, operator, backend = self.admin_boundaries()
        identifier = 'a'*32
        with mock.patch.object(appliance, 'MaintenanceHold') as hold, \
                mock.patch.object(appliance.subprocess, 'run') as command:
            hold.return_value.submit.return_value = identifier
            result = appliance.run_mass_storage_maintenance('request', approved=True)
        approved.assert_called_once_with(settings)
        operator.require_mutations.assert_called_once_with();backend._pin_image.assert_called_once_with()
        hold.assert_called_once_with(self.state, backend, enabled=True)
        hold.return_value.submit.assert_called_once_with();command.assert_not_called()
        self.assertEqual(result, {'maintenance_state':'submitted_waiting_for_safe_eject', 'request_id':identifier})

    def test_release_routes_exact_hold_id_and_requires_separate_controller_restart(self):
        settings, _, approved, operator, backend = self.admin_boundaries()
        identifier = 'b'*32
        controller = mock.Mock()
        with mock.patch.object(appliance, 'GadgetController') as factory, \
                mock.patch.object(appliance.subprocess, 'run') as command:
            factory.return_value.session.return_value.__enter__.return_value = controller
            controller.release_maintenance.return_value = 'released_host_owned'
            result = appliance.run_mass_storage_maintenance('release', approved=True, request_id=identifier)
        approved.assert_called_once_with(settings)
        factory.assert_called_once_with(backend, self.state, maintenance_enabled=True)
        controller.release_maintenance.assert_called_once_with(identifier, self.private, approved=True)
        self.assertEqual(result, {'maintenance_state':'released_host_owned', 'controller_restart_requires_separate_action':True})
        command.assert_not_called();operator.provision_command.assert_not_called()

    def test_request_does_not_accept_a_conflicting_release_identifier(self):
        self.admin_boundaries()
        with mock.patch.object(appliance, 'MaintenanceHold') as hold:
            result = appliance.run_mass_storage_maintenance('request', approved=True, request_id='c'*32)
        self.assertEqual(result, {'maintenance_state':'failed_review_required'});hold.assert_not_called()

    def test_startup_checks_unresolved_hold_before_profile_adoption_or_provision(self):
        for phase in ('pending', 'active', 'releasing'):
            with self.subTest(phase=phase):
                backend = SimulatedGadget(self.root)
                backend._pin_image = mock.Mock()
                backend.root = self.root/'configfs'
                backend.lun = backend.root/'lun.0'
                backend.prepare = mock.Mock()
                record = {'schema_version':1,'image':str(backend.image),'image_identity':list(backend.image_identity),
                          'session_id':'a'*32,'request_id':'b'*32,'phase':phase,
                          'capture_sha256':None if phase=='pending' else 'c'*64}
                path = self.state/'maintenance.json';path.write_text(json.dumps(record));path.chmod(0o600)
                controller = GadgetController(backend, self.state, maintenance_enabled=False)
                operator = mock.Mock()
                with controller.session(), self.assertRaises(BackupError):
                    appliance._start_controller(controller, backend, operator)
                backend._pin_image.assert_called_once_with();backend.prepare.assert_not_called()
                operator.exists.assert_not_called();self.assertEqual(backend.events, [])
                path.unlink()
                # SimulatedGadget's generated filesystem is reused by the next subtest.
                for child in backend.mountpoint.iterdir():child.unlink()
                backend.mountpoint.rmdir()

    def runtime_fixture(self, *, fail_handoff=False):
        self.enabled_admin()
        self.stack.enter_context(mock.patch.object(appliance, 'ROOT_UID', os.getuid()))
        self.stack.enter_context(mock.patch.object(appliance, '_root_directory', ordinary_directory))
        self.stack.enter_context(mock.patch.object(appliance, '_state_directory', ordinary_directory))
        self.stack.enter_context(mock.patch.object(appliance, '_private_directory'))
        self.stack.enter_context(mock.patch.object(appliance.time, 'sleep'))
        backend = SimulatedGadget(self.root)
        operator = mock.Mock()
        operator._command.return_value = SimpleNamespace(returncode=0,
            stdout='{"filesystems":[{"source":"/dev/mmcblk0p2","fstype":"ext4","options":"rw"}]}')
        self.stack.enter_context(mock.patch.object(appliance, '_ApplianceIO', return_value=operator))
        self.stack.enter_context(mock.patch.object(appliance, '_ApplianceBackend', return_value=backend))
        events, controllers = [], []
        original_handoff = appliance.handoff_ready_batch

        def handoff(batch, account):
            events.append('handoff')
            if fail_handoff:
                raise OSError(errno.ENOSPC, 'injected fixture handoff failure')
            original_handoff(batch, account)
            events.append('handoff-complete')

        def factory(device, state, *, maintenance_enabled):
            controller = GadgetController(device, state, maintenance_enabled=maintenance_enabled)
            poll, complete = controller.poll, controller.complete_maintenance
            calls = 0

            def request_then_eject(directory):
                nonlocal calls
                calls += 1;events.append('poll-'+str(calls))
                if calls == 1:
                    identifier = MaintenanceHold(state, device, enabled=True).submit()
                    events.append('submitted-'+identifier)
                else:
                    device.eject()
                return poll(directory)

            def after_handoff(directory):
                events.append('complete-attempt')
                result = complete(directory)
                events.append('active' if result else 'still-pending')
                return result

            controller.poll = request_then_eject
            controller.complete_maintenance = after_handoff
            controllers.append(controller)
            return controller

        self.stack.enter_context(mock.patch.object(appliance, 'GadgetController', side_effect=factory))
        self.stack.enter_context(mock.patch.object(appliance, '_start_controller', side_effect=lambda c,b,i:c.start()))
        self.stack.enter_context(mock.patch.object(appliance, 'handoff_ready_batch', side_effect=handoff))
        return backend, events, controllers

    def test_runtime_waits_for_eject_and_handoff_before_reporting_active(self):
        backend, events, controllers = self.runtime_fixture()
        result = appliance._run(object(), self.account)
        self.assertEqual(result['appliance_state'], 'maintenance_active')
        self.assertEqual(result['batches_handed_off'], 1)
        self.assertLess(events.index('still-pending'), events.index('poll-2'))
        self.assertLess(events.index('handoff-complete'), events.index('active'))
        self.assertEqual(backend.events.count('attach'), 1)
        self.assertFalse(backend.bound);self.assertEqual(backend.backing, '')
        self.assertIsNone(backend.loop);self.assertFalse(backend.mounted)
        self.assertEqual(list(self.private.iterdir()), [])
        ready = list(self.public.glob('*.ready'));self.assertEqual(len(ready), 1)
        self.assertEqual((ready[0]/'files/original.txt').read_bytes(), b'retain original')
        self.assertEqual((backend.mountpoint/'original.txt').read_bytes(), b'retain original')
        self.assertEqual(json.loads((self.state/'maintenance.json').read_text())['phase'], 'active')
        self.assertFalse(controllers[0].in_session)

    def test_handoff_failure_retains_captured_latch_and_data_without_active_ack(self):
        backend, events, _ = self.runtime_fixture(fail_handoff=True)
        with self.assertRaises(OSError):appliance._run(object(), self.account)
        self.assertNotIn('active', events)
        self.assertEqual(events.count('complete-attempt'), 1)  # Pending poll only.
        self.assertEqual(json.loads((self.state/'maintenance.json').read_text())['phase'], 'captured')
        ready = list(self.private.glob('*.ready'));self.assertEqual(len(ready), 1)
        self.assertEqual((ready[0]/'files/original.txt').read_bytes(), b'retain original')
        self.assertTrue((self.state/'capture-manifest.json').is_file())
        self.assertEqual(backend.events.count('attach'), 1)
        self.assertFalse(backend.bound);self.assertIsNone(backend.loop)
        self.assertEqual(list(self.public.iterdir()), [])

    def test_admin_cli_passes_explicit_action_approval_and_exact_release_id(self):
        for arguments, call in ((['--request-maintenance-hold','--approve-maintenance-action'],
                                  ('request',None)),
                                 (['--release-maintenance-hold','d'*32,'--approve-maintenance-action'],
                                  ('release','d'*32))):
            with self.subTest(arguments=arguments), redirect_stdout(io.StringIO()), \
                    mock.patch.object(appliance, 'run_mass_storage_maintenance', return_value={'maintenance_state':'disabled'}) as admin:
                self.assertEqual(appliance.main(arguments),0)
            admin.assert_called_once_with(call[0],approved=True,request_id=call[1])

    def test_cli_approval_without_action_refuses_before_running_appliance(self):
        with redirect_stderr(io.StringIO()), mock.patch.object(appliance,'run_mass_storage_appliance') as run, \
                self.assertRaises(SystemExit) as error:
            appliance.main(['--approve-maintenance-action'])
        self.assertEqual(error.exception.code,2);run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
