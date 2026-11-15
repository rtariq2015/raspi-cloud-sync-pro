"""Source-only enrollment boundaries; mocks and private disposable directories."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from raspi_cloud_sync_pro import mass_storage_appliance as appliance
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController
from raspi_cloud_sync_pro.linux_gadget import GadgetSettings, LinuxGadgetBackend
from test_gadget_controller import SimulatedGadget


class EnrollmentApplianceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-enrollment-boundary-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state, self.private, self.mount = (self.root/name for name in ('state', 'private', 'mount'))
        for path in (self.state, self.private, self.mount):
            path.mkdir(mode=0o700)
        self.settings = GadgetSettings(self.root/'disk.img', self.mount, self.state, self.private,
                                       'test-udc', name='test-gadget', serial='test-serial')
        self.txid, self.hold = 'a'*32, 'b'*32

    def gates(self):
        return mock.patch.multiple(appliance, APPROVED_TEST_BUILD=True,
                                   MAINTENANCE_HOLD_ENABLED=True, IMAGE_ENROLLMENT_ENABLED=True)

    def test_each_disabled_gate_precedes_config_or_device_access(self):
        for values in ((False, True, True), (True, False, True), (True, True, False)):
            with self.subTest(values=values), mock.patch.multiple(appliance,
                    APPROVED_TEST_BUILD=values[0], MAINTENANCE_HOLD_ENABLED=values[1],
                    IMAGE_ENROLLMENT_ENABLED=values[2]), mock.patch.object(appliance, '_settings') as settings:
                result = appliance.run_mass_storage_enrollment('commit', approved=True, transaction_id=self.txid)
            self.assertEqual(result, {'enrollment_state':'disabled'});settings.assert_not_called()

    def test_missing_numeric_and_wrong_action_approvals_refuse_before_platform(self):
        for approved in (False, 1, 'yes', None):
            with self.subTest(approved=approved), self.gates(), \
                    mock.patch.object(appliance, '_enrollment_platform_approved') as platform:
                result = appliance.run_mass_storage_enrollment('commit', approved=approved, transaction_id=self.txid)
            self.assertEqual(result, {'enrollment_state':'failed_review_required'});platform.assert_not_called()
        with self.gates(), mock.patch.object(appliance, '_enrollment_platform_approved') as platform:
            result = appliance.run_mass_storage_enrollment('arbitrary', approved=True, transaction_id=self.txid)
        self.assertEqual(result, {'enrollment_state':'failed_review_required'});platform.assert_not_called()

    def test_incomplete_or_conflicting_action_parameters_refuse_before_platform(self):
        cases = [dict(action='prepare', candidate='/candidate', hold_id=self.hold),
                 dict(action='commit', transaction_id=self.txid, hold_id=self.hold),
                 dict(action='recover', transaction_id=self.txid),
                 dict(action='recover', transaction_id='stale', recovery_action='commit'),
                 dict(action='activate', transaction_id=self.txid, recovery_action='host')]
        for fields in cases:
            with self.subTest(fields=fields), self.gates(), \
                    mock.patch.object(appliance, '_enrollment_platform_approved') as platform:
                result = appliance.run_mass_storage_enrollment(approved=True, **fields)
            self.assertEqual(result, {'enrollment_state':'failed_review_required'});platform.assert_not_called()

    def test_existing_root_pi_and_namespace_approval_is_still_required(self):
        with self.gates(), mock.patch.object(appliance.os, 'getresuid', return_value=(1000,)*3), \
                mock.patch.object(appliance, '_settings') as settings:
            result = appliance.run_mass_storage_enrollment('commit', approved=True, transaction_id=self.txid)
        self.assertEqual(result, {'enrollment_state':'failed_review_required'});settings.assert_not_called()

    def test_recovery_does_not_require_canonical_image_approval(self):
        from raspi_cloud_sync_pro import image_enrollment
        def directory(path):
            from contextlib import nullcontext
            return nullcontext()
        with self.gates(), mock.patch.object(appliance, '_enrollment_platform_approved'), \
                mock.patch.object(appliance, '_settings', return_value=self.settings), \
                mock.patch.object(appliance, '_root_directory', side_effect=directory), \
                mock.patch.object(appliance, '_approved') as active, \
                mock.patch.object(image_enrollment, 'ImageEnrollment') as transaction:
            result = appliance.run_mass_storage_enrollment('recover', approved=True,
                transaction_id=self.txid, recovery_action='rollback')
        active.assert_not_called()
        transaction.open.return_value.recover.assert_called_once_with(self.txid, action='rollback', approved=True)
        self.assertEqual(result['enrollment_state'], 'rolled_back_detached')

    def test_verifier_io_forbids_exports_writable_mounts_repairs_and_unrelated_commands(self):
        selected = appliance._EnrollmentIO(self.settings)
        invalid = [['mount', '-t', 'exfat', '-o', 'rw', '/dev/loop4p1', str(self.mount)],
                   ['fsck.exfat', '-y', '/dev/loop4p1'],
                   ['losetup', '--find', '--show', '--partscan', str(self.settings.image)],
                   ['umount', '/unrelated'], ['systemctl', 'restart', 'anything']]
        with mock.patch.object(selected, 'require_mutations'), mock.patch.object(selected, '_command') as command:
            for arguments in invalid:
                with self.subTest(arguments=arguments), self.assertRaises(BackupError):selected.command(arguments)
            command.assert_not_called()
            for callback in (lambda:selected.write('/sys/UDC', 'test'),
                             lambda:selected.mkdir('/sys/new'),
                             lambda:selected.symlink('/a','/b'),
                             lambda:selected.provision_command(['anything'])):
                with self.assertRaises(BackupError):callback()
            command.assert_not_called()

    def test_verifier_io_allows_only_bound_readonly_operations(self):
        selected = appliance._EnrollmentIO(self.settings)
        valid = [['losetup', '--find', '--show', '--read-only', '--partscan', str(self.settings.image)],
                 ['fsck.exfat', '-n', '/dev/loop4p1'],
                 ['mount', '-t', 'exfat', '-o', 'ro,nosuid,nodev,noexec', '/dev/loop4p1', str(self.mount)],
                 ['umount', str(self.mount)], ['losetup', '--detach', '/dev/loop4']]
        with mock.patch.object(selected, 'require_mutations') as approved, \
                mock.patch.object(selected, '_command') as command:
            for arguments in valid:selected.command(arguments)
        self.assertEqual(command.call_count,len(valid));self.assertEqual(approved.call_count,len(valid))

    def test_cli_routes_explicit_enrollment_approval_without_changing_services(self):
        cases = [(['--prepare-image-enrollment','/private/candidate.img','--candidate-image-approval','/private/approval',
                   '--maintenance-hold-id',self.hold,'--approve-image-enrollment'], 'prepare', None),
                 (['--commit-image-enrollment',self.txid,'--approve-image-enrollment'], 'commit', self.txid),
                 (['--recover-image-enrollment',self.txid,'--enrollment-recovery-action','rollback',
                   '--approve-image-enrollment'], 'recover', self.txid),
                 (['--activate-image-enrollment',self.txid,'--approve-image-enrollment'], 'activate', self.txid)]
        for arguments, action, identifier in cases:
            with self.subTest(action=action), redirect_stdout(io.StringIO()), \
                    mock.patch.object(appliance, 'run_mass_storage_enrollment', return_value={'enrollment_state':'disabled'}) as run:
                self.assertEqual(appliance.main(arguments),0)
            self.assertEqual(run.call_args.args,(action,));self.assertIs(run.call_args.kwargs['approved'],True)
            self.assertEqual(run.call_args.kwargs['transaction_id'],identifier)

    def test_cli_conflicting_or_missing_explicit_action_never_starts_appliance(self):
        for arguments in (['--approve-image-enrollment'], ['--candidate-image-approval','/private/file'],
                          ['--commit-image-enrollment',self.txid,'--approve-maintenance-action'],
                          ['--request-maintenance-hold','--commit-image-enrollment',self.txid]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), \
                    mock.patch.object(appliance,'run_mass_storage_appliance') as run, self.assertRaises(SystemExit):
                appliance.main(arguments)
            run.assert_not_called()

    def test_enrolled_maintenance_record_does_not_authorize_ordinary_release(self):
        from raspi_cloud_sync_pro.maintenance import MaintenanceHold
        simulated = self.root/'simulated';simulated.mkdir()
        backend = SimulatedGadget(simulated)
        backend.image_identity = (1,2,8*1024**3)
        value={'schema_version':1,'image':str(backend.image),'image_identity':list(backend.image_identity),
               'session_id':self.txid,'request_id':self.hold,'phase':'enrolled','capture_sha256':'c'*64}
        path=self.state/'maintenance.json';path.write_text(json.dumps(value));path.chmod(0o600)
        hold=MaintenanceHold(self.state,backend,enabled=True)
        with hold.locked():
            self.assertEqual(hold.record()['phase'],'enrolled')
            with self.assertRaises(BackupError):hold.assert_normal()

    def test_controller_and_direct_backend_export_guards_precede_resources(self):
        from raspi_cloud_sync_pro import image_enrollment
        simulated = self.root/'simulated';simulated.mkdir()
        backend = SimulatedGadget(simulated)
        controller=GadgetController(backend,self.state)
        with controller.session(), mock.patch.object(image_enrollment,'guard_enrollment',side_effect=BackupError('enrollment_pending')):
            for operation in (controller.start,controller.validate_maintenance_start,controller._attach_checked):
                with self.assertRaises(BackupError):operation()
            self.assertEqual(backend.events,[])
        direct=LinuxGadgetBackend(self.settings,mock.Mock())
        with mock.patch.object(direct,'_pin_image'), mock.patch.object(direct,'observe') as observe, \
                mock.patch.object(image_enrollment,'guard_enrollment',side_effect=BackupError('enrollment_pending')):
            with self.assertRaises(BackupError):direct.attach_gadget()
        observe.assert_not_called();direct.io.write.assert_not_called()

    def test_protected_appliance_low_level_export_cannot_bypass_guard(self):
        from raspi_cloud_sync_pro import image_enrollment
        operator=appliance._ApplianceIO(self.settings)
        with mock.patch.object(appliance,'STATE',self.state), \
                mock.patch.object(appliance,'IMAGE',self.settings.image), \
                mock.patch.object(operator,'regular_image',return_value=(1,2,1024**3)), \
                mock.patch.object(operator,'require_mutations') as mutation, \
                mock.patch.object(image_enrollment,'guard_enrollment',side_effect=BackupError('enrollment_pending')):
            for path,value in ((operator.root/'UDC',self.settings.udc),
                               (operator.root/'functions/mass_storage.0/lun.0/file',str(self.settings.image))):
                with self.assertRaises(BackupError):operator.write(path,value)
        mutation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
