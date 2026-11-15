"""Offline capacity-policy wiring and ownership/recovery integration tests."""

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from raspi_cloud_sync_pro import mass_storage_appliance as appliance
from raspi_cloud_sync_pro.capture_space import CAPACITY_POLICY, LEGACY_POLICY, CaptureSpacePolicy
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController
from raspi_cloud_sync_pro.maintenance import MaintenanceHold
from raspi_cloud_sync_pro.mass_storage_image import EXPANDED_SIZE, LEGACY_SIZE, image_layout
from raspi_cloud_sync_pro.storage import Ownership
from test_gadget_controller import SimulatedGadget

GIB = 1024 ** 3


def observed_space(available=40*GIB):
    return SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=64*GIB//4096,
                           f_bfree=available//4096, f_bavail=available//4096,
                           f_files=1_000_000, f_favail=900_000)


@contextmanager
def ordinary_directory(path, *unused):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


class ApplianceSpaceConfigurationTests(unittest.TestCase):
    @staticmethod
    def config(version=1):
        value = {'schema_version':version, 'enabled':True, 'udc':'fake-udc',
                 'serial':appliance.SERIAL, 'approval_file':str(appliance.APPROVAL)}
        if version == 2:
            value['capture_space_policy'] = CAPACITY_POLICY
        return value

    def test_legacy_and_explicit_expanded_configs_select_fixed_profiles(self):
        for version, profile in ((1, LEGACY_POLICY), (2, CAPACITY_POLICY)):
            with self.subTest(version=version), mock.patch.object(appliance,'_json',return_value=self.config(version)):
                settings = appliance._settings()
            self.assertEqual(settings.capture_space_policy, profile)
            self.assertEqual(settings.image, appliance.IMAGE)
            self.assertEqual(settings.staging_directory, appliance.PRIVATE_STAGING)

    def test_malformed_or_downgraded_config_does_not_silently_select_legacy(self):
        valid = self.config(2)
        variants = []
        missing = dict(valid);del missing['capture_space_policy'];variants.append(missing)
        for change in ({'schema_version':True}, {'schema_version':3},
                       {'schema_version':1}, {'capture_space_policy':LEGACY_POLICY},
                       {'capture_space_policy':None}, {'capture_space_policy':True},
                       {'capture_space_policy':{'os_reserve_bytes':0}},
                       {'capture_space_policy':'capacity-reserve-v0'},
                       {'reserve_bytes':0}, {'image_size_bytes':4*GIB}):
            variants.append({**valid, **change})
        for value in variants:
            with self.subTest(value=value), mock.patch.object(appliance,'_json',return_value=value), self.assertRaises(BackupError):
                appliance._settings()

    @contextmanager
    def approval_boundary(self, size, profile):
        settings = SimpleNamespace(capture_space_policy=profile)
        identity = (7, 88, size)
        value = {'schema_version':1 if size == LEGACY_SIZE else 2,
                 'mass_storage_appliance_approved':True, 'image_identity':list(identity)}
        if size == EXPANDED_SIZE:
            value.update(image_size_bytes=size, image_layout=image_layout(size))
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(appliance,'APPROVED_TEST_BUILD',True))
            stack.enter_context(mock.patch.object(appliance.os,'getresuid',return_value=(0,)*3))
            stack.enter_context(mock.patch.object(appliance.os,'getresgid',return_value=(0,)*3))
            stack.enter_context(mock.patch.object(appliance.os,'readlink',return_value='same-namespace'))
            stack.enter_context(mock.patch.object(appliance.platform,'system',return_value='Linux'))
            stack.enter_context(mock.patch.object(appliance.platform,'machine',return_value='aarch64'))
            stack.enter_context(mock.patch.object(Path,'read_bytes',return_value=b'Raspberry Pi Zero 2 W Rev 1.0\0'))
            stack.enter_context(mock.patch.object(appliance,'_settings',return_value=settings))
            stack.enter_context(mock.patch.object(appliance,'_json',return_value=value))
            stack.enter_context(mock.patch.object(appliance.LiveLinuxIO,'regular_image',return_value=identity))
            opened = stack.enter_context(mock.patch.object(appliance.os,'open'))
            payload = stack.enter_context(mock.patch.object(appliance.os,'pread'))
            yield settings, opened, payload

    def test_image_approval_requires_legacy_1gib_or_explicit_strong_8gib(self):
        for size, profile, allowed in ((LEGACY_SIZE,LEGACY_POLICY,True),
                                       (EXPANDED_SIZE,CAPACITY_POLICY,True),
                                       (EXPANDED_SIZE,LEGACY_POLICY,False),
                                       (LEGACY_SIZE,CAPACITY_POLICY,False)):
            with self.subTest(size=size,profile=profile), self.approval_boundary(size,profile) as (settings, opened, payload):
                if allowed:
                    appliance._approved(settings)
                else:
                    with self.assertRaisesRegex(BackupError,'invalid_capture_space_policy'):
                        appliance._approved(settings)
                opened.assert_not_called();payload.assert_not_called()

    def test_runtime_wires_only_the_explicit_8gib_profile_to_controller(self):
        for profile in (LEGACY_POLICY, CAPACITY_POLICY):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); published = root/'published';published.mkdir(mode=0o700)
                settings = SimpleNamespace(capture_space_policy=profile)
                account = SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
                operator = mock.Mock()
                operator._command.return_value = SimpleNamespace(returncode=0,stdout=
                    '{"filesystems":[{"source":"/dev/mmcblk0p2","fstype":"ext4","options":"rw"}]}')
                controller = mock.MagicMock();controller.session.return_value.__enter__.return_value = controller
                controller.poll.return_value = 'waiting_for_host_eject'
                with mock.patch.object(appliance,'PRIVATE_STAGING',root/'private'), \
                        mock.patch.object(appliance,'STAGING',published), \
                        mock.patch.object(appliance,'MAINTENANCE_HOLD_ENABLED',False), \
                        mock.patch.object(appliance,'_ApplianceIO',return_value=operator), \
                        mock.patch.object(appliance,'_private_directory'), \
                        mock.patch.object(appliance,'_state_directory',ordinary_directory), \
                        mock.patch.object(appliance,'_ApplianceBackend') as backend, \
                        mock.patch.object(appliance,'_start_controller'), \
                        mock.patch.object(appliance,'GadgetController',return_value=controller) as factory:
                    result = appliance._run(settings,account,once=True)
                policy = factory.call_args.kwargs['space_policy']
                if profile == CAPACITY_POLICY:
                    self.assertIs(type(policy),CaptureSpacePolicy)
                    self.assertEqual(policy.image_size_bytes,EXPANDED_SIZE)
                else:self.assertIsNone(policy)
                self.assertEqual(result['appliance_state'],'host_owned')
                controller.poll.assert_called_once();operator.require_mutations.assert_called_once_with()

    def test_low_space_and_unknown_capacity_reports_are_fixed_and_filename_free(self):
        for code in ('capture_space_low','capture_space_unknown','invalid_capture_space_policy'):
            with self.subTest(code=code), mock.patch.object(appliance,'APPROVED_TEST_BUILD',True), \
                    mock.patch.object(appliance,'_settings',return_value=object()), \
                    mock.patch.object(appliance,'_approved'), mock.patch.object(appliance,'_service_identity'), \
                    mock.patch.object(appliance,'_run',side_effect=BackupError(code)):
                result = appliance.run_mass_storage_appliance()
            self.assertEqual(result['appliance_state'],'failed_review_required')
            self.assertEqual(result['error_code'],code)
            self.assertTrue(result['message'])
            self.assertNotIn('PRIVATE',json.dumps(result))
        with mock.patch.object(appliance,'APPROVED_TEST_BUILD',True), \
                mock.patch.object(appliance,'_settings',return_value=object()), \
                mock.patch.object(appliance,'_approved'),mock.patch.object(appliance,'_service_identity'), \
                mock.patch.object(appliance,'_run',side_effect=OSError('PRIVATE-filename-token')):
            result = appliance.run_mass_storage_appliance()
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('message',result)


class CaptureSpaceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-space-controller-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, *, size=EXPANDED_SIZE, strong=True, maintenance=False, name='fixture'):
        root = self.root/name;root.mkdir(mode=0o700)
        backend = SimulatedGadget(root)
        with backend.image.open('r+b') as output:output.truncate(size)
        info = backend.image.stat();backend.image_identity=(info.st_dev,info.st_ino,info.st_size)
        controller = GadgetController(backend,root/'state',maintenance_enabled=maintenance,
                                      space_policy=CaptureSpacePolicy() if strong else None)
        private = root/'private';private.mkdir(mode=0o700)
        return root,backend,controller,private

    @staticmethod
    def arm(controller,private):
        controller.start()
        self_result = controller.poll(private)
        if self_result != 'waiting_for_host_eject':raise AssertionError(self_result)

    def test_normal_approved_8gib_capture_still_returns_usb(self):
        root,backend,controller,private=self.fixture()
        with controller.session():
            self.arm(controller,private);backend.events.clear();backend.eject()
            with mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs',return_value=observed_space()):
                batch=controller.poll(private)
            self.assertTrue(backend.bound);self.assertEqual(controller.ownership.state,Ownership.HOST)
            self.assertEqual((batch.root/'files/original.txt').read_bytes(),b'retain original')
            self.assertEqual(controller.journal.data['phase'],'host')
            self.assertLess(backend.events.index('unmount'),backend.events.index('attach'))

    def test_low_space_retains_old_inventory_and_versions_and_blocks_restart(self):
        root,backend,controller,private=self.fixture()
        with controller.session():
            self.arm(controller,private);backend.eject()
            with mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs',return_value=observed_space()):
                previous=controller.poll(private)
            inventory=(root/'state/capture-manifest.json').read_bytes()
            previous_bytes=(previous.root/'files/original.txt').read_bytes()
            (backend.mountpoint/'original.txt').write_bytes(b'new synthetic version')
            controller.poll(private);backend.eject();backend.events.clear()
            paths=set(private.iterdir())
            with mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs',return_value=observed_space(0)), \
                    self.assertRaisesRegex(BackupError,'capture_space_low'):
                controller.poll(private)
            self.assertEqual(controller.ownership.state,Ownership.ERROR)
            self.assertNotIn('attach',backend.events);self.assertFalse(backend.bound)
            self.assertEqual(set(private.iterdir()),paths)
            self.assertEqual((root/'state/capture-manifest.json').read_bytes(),inventory)
            self.assertEqual((previous.root/'files/original.txt').read_bytes(),previous_bytes)
            self.assertEqual((backend.mountpoint/'original.txt').read_bytes(),b'new synthetic version')
        restarted=GadgetController(backend,root/'state',space_policy=CaptureSpacePolicy())
        with restarted.session(),mock.patch.object(backend,'observe') as observation,self.assertRaises(BackupError):
            restarted.start(cold_start=True,staging_directory=private)
        observation.assert_not_called()

    def test_legacy_1gib_keeps_existing_16mib_low_space_path(self):
        root,backend,controller,private=self.fixture(size=LEGACY_SIZE,strong=False)
        with controller.session():
            self.arm(controller,private);backend.eject();backend.events.clear()
            with mock.patch('raspi_cloud_sync_pro.staging.shutil.disk_usage',return_value=SimpleNamespace(free=0)), \
                    mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs') as observed, \
                    self.assertRaisesRegex(BackupError,'staging_space_low'):
                controller.poll(private)
            observed.assert_not_called();self.assertNotIn('attach',backend.events)
            self.assertTrue(list(private.glob('*.incomplete')))
            self.assertEqual((backend.mountpoint/'original.txt').read_bytes(),b'retain original')

    def test_pending_maintenance_and_low_space_remain_unresolved(self):
        root,backend,controller,private=self.fixture(maintenance=True)
        with controller.session():
            self.arm(controller,private)
            MaintenanceHold(root/'state',backend,enabled=True).submit()
            self.assertEqual(controller.poll(private),'maintenance_pending')
            backend.eject();backend.events.clear()
            with mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs',return_value=observed_space(0)), \
                    self.assertRaisesRegex(BackupError,'capture_space_low'):
                controller.poll(private)
            self.assertNotIn('attach',backend.events)
            self.assertEqual(json.loads((root/'state/maintenance.json').read_text())['phase'],'capturing')
        restarted=GadgetController(backend,root/'state',maintenance_enabled=False,space_policy=CaptureSpacePolicy())
        with restarted.session(),mock.patch.object(backend,'observe') as observation,self.assertRaises(BackupError):
            restarted.start()
        observation.assert_not_called()

    def test_active_hold_blocks_startup_before_any_capacity_or_source_read(self):
        root,backend,controller,private=self.fixture(maintenance=True)
        with controller.session():
            self.arm(controller,private)
            MaintenanceHold(root/'state',backend,enabled=True).submit();backend.eject()
            with mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs',return_value=observed_space()):
                batch=controller.poll(private)
            batch.root.rename(root/'retained.ready');self.assertTrue(controller.complete_maintenance(private))
        restarted=GadgetController(backend,root/'state',space_policy=CaptureSpacePolicy())
        with restarted.session(),mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs') as capacity, \
                mock.patch.object(backend,'observe') as observation,self.assertRaises(BackupError):restarted.start()
        capacity.assert_not_called();observation.assert_not_called()

    def test_strong_policy_cannot_capture_an_identity_with_legacy_size(self):
        root,backend,controller,private=self.fixture(size=LEGACY_SIZE,strong=True)
        with controller.session():
            self.arm(controller,private);backend.eject();backend.events.clear()
            with mock.patch('raspi_cloud_sync_pro.capture_space.os.fstatvfs') as observed, \
                    self.assertRaisesRegex(BackupError,'invalid_capture_space_policy'):
                controller.poll(private)
            observed.assert_not_called();self.assertNotIn('attach',backend.events)
            self.assertFalse((root/'state/capture-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
