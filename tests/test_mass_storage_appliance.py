"""Real inode/publication tests with explicitly mocked root/ownership boundaries."""

import ast
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import mass_storage_appliance as appliance
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.staging import stage_snapshot
from raspi_cloud_sync_pro.storage import StorageOwnership


class ApplianceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.private, self.target = (self.root / name for name in ('source', 'private', 'target'))
        for path in (self.source, self.private, self.target):path.mkdir(mode=0o700)
        (self.source / 'folder').mkdir();(self.source / 'folder/QA.txt').write_bytes(b'USB capture, retain original')
        self.account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        self.stack = ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(appliance, 'PRIVATE_STAGING', self.private))
        self.stack.enter_context(mock.patch.object(appliance, 'STAGING', self.target))

    @staticmethod
    @contextmanager
    def ordinary_directory(path, *args):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:yield fd
        finally:os.close(fd)

    def root_boundary(self):
        # Root privilege/ancestor trust is mocked; actual temp trees remain local UID.
        self.stack.enter_context(mock.patch.object(appliance, 'ROOT_UID', os.getuid()))
        self.stack.enter_context(mock.patch.object(appliance, '_root_directory', self.ordinary_directory))
        self.stack.enter_context(mock.patch.object(appliance, '_state_directory', self.ordinary_directory))

    def batch(self):
        ownership = StorageOwnership()
        ownership.confirm_host_access(locally_unmounted=True, loop_detached=True)
        ownership.confirm_host_release(clean_eject_observed=True)
        ownership.confirm_pi_access(gadget_closed=True, io_drained=True, filesystem_clean=True)
        ownership.begin_staging(loop_read_only=True, mount_read_only=True)
        before = os.umask(0o077)
        try:return stage_snapshot(self.source, self.private, ownership)
        finally:os.umask(before)

    def tree(self, root):
        return {str(path.relative_to(root)): (path.stat().st_ino, path.stat().st_mode, path.read_bytes() if path.is_file() else None)
                for path in root.rglob('*')}

    def test_public_test_build_gate_returns_before_configuration_or_hardware(self):
        with mock.patch.object(appliance, '_settings') as settings, mock.patch.object(appliance, '_run') as run:
            self.assertEqual(appliance.run_mass_storage_appliance()['appliance_state'], 'disabled')
        settings.assert_not_called();run.assert_not_called()
        from raspi_cloud_sync_pro.linux_gadget import HARDWARE_OPERATIONS_ENABLED
        self.assertIs(HARDWARE_OPERATIONS_ENABLED, False)

    def test_disabled_config_and_invalid_approval_never_start_controller(self):
        with mock.patch.object(appliance, 'APPROVED_TEST_BUILD', True), mock.patch.object(appliance, '_settings', return_value=None), \
                mock.patch.object(appliance, '_approved') as approval, mock.patch.object(appliance, '_run') as run:
            self.assertEqual(appliance.run_mass_storage_appliance()['appliance_state'], 'disabled')
        approval.assert_not_called();run.assert_not_called()
        with mock.patch.object(appliance, 'APPROVED_TEST_BUILD', True), mock.patch.object(appliance, '_settings', return_value=object()), \
                mock.patch.object(appliance, '_approved', side_effect=OSError('private details')), mock.patch.object(appliance, '_run') as run:
            result = appliance.run_mass_storage_appliance()
        self.assertEqual(result['appliance_state'], 'failed_review_required');self.assertNotIn('private', json.dumps(result));run.assert_not_called()

    def test_settings_have_no_browser_or_arbitrary_path_fields(self):
        valid = {'schema_version': 1, 'enabled': True, 'udc': 'fake-udc', 'serial': appliance.SERIAL,
                 'approval_file': str(appliance.APPROVAL)}
        with mock.patch.object(appliance, '_json', return_value=valid):
            settings = appliance._settings()
        self.assertEqual((settings.image, settings.mountpoint, settings.state_directory, settings.staging_directory),
                         (appliance.IMAGE, appliance.MOUNT, appliance.STATE, appliance.PRIVATE_STAGING))
        for change in ({'image': '/arbitrary'}, {'schema_version': True}, {'enabled': 'true'},
                       {'serial': 'foreign'}, {'approval_file': '/different'}):
            with mock.patch.object(appliance, '_json', return_value={**valid, **change}), self.assertRaises(BackupError):
                appliance._settings()

    def test_approval_binds_same_regular_image_identity_and_explicit_true(self):
        settings = object();valid = {'schema_version': 1, 'mass_storage_appliance_approved': True,
                                    'image_identity': [7, 88, 1024**3]}
        with mock.patch.object(appliance, 'APPROVED_TEST_BUILD', True), mock.patch.object(appliance.os, 'getresuid', return_value=(0,)*3), \
                mock.patch.object(appliance.os, 'getresgid', return_value=(0,)*3), \
                mock.patch.object(appliance.os, 'readlink', return_value='same-host-mount-namespace'), \
                mock.patch.object(appliance.platform, 'system', return_value='Linux'), \
                mock.patch.object(appliance.platform, 'machine', return_value='aarch64'), \
                mock.patch.object(Path, 'read_bytes', return_value=b'Raspberry Pi Zero 2 W Rev 1.0\0'), \
                mock.patch.object(appliance, '_settings', return_value=settings), \
                mock.patch.object(appliance.LiveLinuxIO, 'regular_image', return_value=(7,88,1024**3)):
            with mock.patch.object(appliance, '_json', return_value=valid):appliance._approved(settings)
            for change in ({'image_identity': [7,89,1024**3]}, {'mass_storage_appliance_approved': 1}, {'schema_version': True}):
                with mock.patch.object(appliance, '_json', return_value={**valid, **change}), self.assertRaises(BackupError):
                    appliance._approved(settings)

    def test_handoff_preserves_payload_inodes_originals_and_private_modes(self):
        self.root_boundary();batch = self.batch();before = self.tree(batch.root)
        appliance.handoff_ready_batch(batch, self.account)
        ready = self.target / batch.root.name
        self.assertFalse(batch.root.exists());self.assertEqual(self.tree(ready), before)
        self.assertEqual((self.source / 'folder/QA.txt').read_bytes(), (ready / 'files/folder/QA.txt').read_bytes())
        for path in (ready, *ready.rglob('*')):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_top_directory_chowned_last_and_no_replace_publish_after_chowns(self):
        self.root_boundary();batch = self.batch();top = batch.root.stat().st_ino;events = []
        original_chown, original_publish = os.fchown, appliance._publish
        def chown(fd, uid, gid):
            events.append(('chown', os.fstat(fd).st_ino));return original_chown(fd, uid, gid)
        def publish(source, target, name):
            self.assertEqual(events[-1], ('chown', top));self.assertEqual(batch.root.stat().st_uid, self.account.pw_uid)
            events.append(('publish', name));return original_publish(source, target, name)
        with mock.patch.object(appliance.os, 'fchown', side_effect=chown), mock.patch.object(appliance, '_publish', side_effect=publish):
            appliance.handoff_ready_batch(batch, self.account)
        self.assertEqual(events[-2], ('chown', top));self.assertEqual(events[-1][0], 'publish')

    def test_existing_ready_or_symlink_never_overwritten_and_source_retained(self):
        self.root_boundary()
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                batch = self.batch();destination = self.target / batch.root.name
                if symlink:destination.symlink_to(self.source, target_is_directory=True)
                else:destination.mkdir(mode=0o700)
                before = destination.lstat().st_ino
                with self.assertRaisesRegex(BackupError, 'batch_exists'):appliance.handoff_ready_batch(batch, self.account)
                self.assertTrue(batch.root.is_dir());self.assertEqual(destination.lstat().st_ino, before)

    def test_symlink_hardlink_and_extra_file_refused_before_any_chown(self):
        self.root_boundary()
        for kind in ('symlink', 'hardlink', 'extra'):
            with self.subTest(kind=kind):
                batch = self.batch();path = batch.root / 'files/unsafe'
                if kind == 'symlink':path.symlink_to(self.source / 'folder/QA.txt')
                elif kind == 'hardlink':os.link(batch.root / 'files/folder/QA.txt', path)
                else:path.write_bytes(b'not in manifest');path.chmod(0o600)
                with mock.patch.object(appliance.os, 'fchown') as chown, self.assertRaises(BackupError):
                    appliance.handoff_ready_batch(batch, self.account)
                chown.assert_not_called();self.assertTrue(batch.root.exists())

    def test_wrong_mode_manifest_and_injected_publish_failure_retain_snapshot(self):
        self.root_boundary();batch = self.batch();payload = batch.root / 'files/folder/QA.txt'
        payload.chmod(0o644)
        with self.assertRaises(BackupError):appliance.handoff_ready_batch(batch, self.account)
        payload.chmod(0o600)
        before = payload.read_bytes()
        with mock.patch.object(appliance, '_publish', side_effect=OSError('injected full disk')), self.assertRaises(OSError):
            appliance.handoff_ready_batch(batch, self.account)
        self.assertTrue(batch.root.exists());self.assertEqual(payload.read_bytes(), before);self.assertFalse(list(self.target.iterdir()))
        (batch.root / 'manifest.json').write_text('{"schema_version":1,"files":[]}')
        with self.assertRaises(BackupError):appliance.handoff_ready_batch(batch, self.account)

    def test_target_replacement_before_publish_fails_closed(self):
        self.root_boundary();batch = self.batch();original = self.ordinary_directory;count = 0
        @contextmanager
        def state(path, owner):
            nonlocal count
            count += 1
            if count == 2:
                path.rename(self.root / 'retained-target');path.mkdir(mode=0o700)
            with original(path) as fd:yield fd
        with mock.patch.object(appliance, '_state_directory', state), self.assertRaises(BackupError):
            appliance.handoff_ready_batch(batch, self.account)
        self.assertTrue(batch.root.exists());self.assertFalse(list(self.target.iterdir()))
        self.assertFalse(list((self.root / 'retained-target').iterdir()))

    def test_different_filesystem_refused_before_chown(self):
        self.root_boundary();batch = self.batch();original = os.fstat
        def fstat(fd):
            info = original(fd)
            if (info.st_dev, info.st_ino) == (self.target.stat().st_dev, self.target.stat().st_ino):
                return SimpleNamespace(st_dev=info.st_dev+1, st_ino=info.st_ino, st_mode=info.st_mode, st_uid=info.st_uid)
            return info
        with mock.patch.object(appliance.os, 'fstat', side_effect=fstat), mock.patch.object(appliance.os, 'fchown') as chown, self.assertRaises(BackupError):
            appliance.handoff_ready_batch(batch, self.account)
        chown.assert_not_called();self.assertTrue(batch.root.exists())

    def test_flat_inventory_limit_is_enforced_before_ownership_changes(self):
        self.root_boundary()
        for index in range(4):
            path = self.private / str(index);path.write_bytes(b'retain');path.chmod(0o600)
        fd = os.open(self.private, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(appliance, '_MAX_ENTRIES', 3), mock.patch.object(appliance.os, 'fchown') as chown, self.assertRaises(BackupError):
                appliance._inventory(fd)
            chown.assert_not_called();self.assertEqual(len(list(self.private.iterdir())), 4)
        finally:os.close(fd)

    def test_runtime_holds_one_session_through_poll_and_handoff_adopts_explicitly(self):
        events = [];batch = self.batch();controller = mock.Mock()
        @contextmanager
        def session():
            events.append('enter')
            try:yield controller
            finally:events.append('exit')
        controller.session.side_effect = session
        controller.start.side_effect = lambda **kwargs: events.append(('start', kwargs))
        def poll(root):
            current = os.umask(0o077);self.assertEqual(current, 0o077)
            events.append('poll');return batch
        controller.poll.side_effect = poll
        io = mock.Mock();io._command.return_value = SimpleNamespace(returncode=0,
            stdout='{"filesystems":[{"source":"/dev/mmcblk0p2","fstype":"ext4","options":"rw"}]}')
        previous = os.umask(0o000)
        try:
            with mock.patch.object(appliance, '_ApplianceIO', return_value=io), mock.patch.object(appliance, '_private_directory'), \
                mock.patch.object(appliance, '_state_directory', self.ordinary_directory), \
                mock.patch.object(appliance, '_ApplianceBackend'), mock.patch.object(appliance, 'GadgetController', return_value=controller), \
                mock.patch.object(appliance, 'handoff_ready_batch', side_effect=lambda *args: events.append('handoff')):
                result = appliance._run(object(), self.account, once=True)
            restored = os.umask(0o000);self.assertEqual(restored, 0o000)
        finally:os.umask(previous)
        self.assertEqual(events, ['enter', ('start', {'adopt_host': True}), 'poll', 'handoff', 'exit'])
        self.assertEqual(result['batches_handed_off'], 1);self.assertFalse(result['root_cloud_operations'])

    def test_runtime_handoff_failure_exits_session_without_another_poll(self):
        controller = mock.MagicMock();controller.session.return_value.__enter__.return_value = controller
        controller.poll.return_value = self.batch()
        io = mock.Mock();io._command.return_value = SimpleNamespace(returncode=0,
            stdout='{"filesystems":[{"source":"/dev/mmcblk0p2","fstype":"ext4","options":"rw"}]}')
        with mock.patch.object(appliance, '_ApplianceIO', return_value=io), mock.patch.object(appliance, '_private_directory'), \
                mock.patch.object(appliance, '_state_directory', self.ordinary_directory), mock.patch.object(appliance, '_ApplianceBackend'), \
                mock.patch.object(appliance, 'GadgetController', return_value=controller), \
                mock.patch.object(appliance, 'handoff_ready_batch', side_effect=OSError('retained')), self.assertRaises(OSError):
            appliance._run(object(), self.account, once=True)
        controller.poll.assert_called_once();controller.session.return_value.__exit__.assert_called_once()

    def test_appliance_io_cannot_provision_or_change_other_profile_attributes(self):
        settings = SimpleNamespace(udc='fake')
        io = appliance._ApplianceIO(settings)
        for operation in (lambda: io.provision_command(['anything']), lambda: io.mkdir('/sys/any'),
                          lambda: io.symlink('/sys/a', '/sys/b'), lambda: io.write(io.root/'idVendor', 'x'),
                          lambda: io.write(io.root/'UDC', 'foreign')):
            with self.assertRaises(BackupError):operation()
        ast.parse(Path(appliance.__file__).read_text(), feature_version=(3, 9))

    def test_clean_volume_header_only_after_host_release_and_ro_check(self):
        image = self.root / 'disposable.img'
        with image.open('xb') as stream:
            stream.truncate(1024**3)
            mbr, boot = bytearray(512), bytearray(512)
            mbr[450] = 7;mbr[454:458] = (2048).to_bytes(4, 'little')
            mbr[458:462] = (1024**3//512-2048).to_bytes(4, 'little')
            mbr[510:] = boot[510:] = b'\x55\xaa';boot[3:11] = b'EXFAT   '
            stream.write(mbr);stream.seek(1024*1024);stream.write(boot)
        backend = object.__new__(appliance._ApplianceBackend)
        info = image.stat();backend.image_identity = info.st_dev, info.st_ino, info.st_size
        observed = SimpleNamespace(bound=False, backing='', loops=('/dev/loop42',), mounts=())
        with mock.patch.object(appliance, 'IMAGE', image), mock.patch.object(appliance, 'ROOT_UID', os.getuid()), \
                mock.patch.object(backend, 'observe', return_value=observed), \
                mock.patch.object(appliance.LinuxGadgetBackend, 'check_filesystem') as checker:
            backend.check_filesystem('/dev/loop42');checker.assert_called_once()
            for flag in (2, 4):
                with image.open('r+b') as stream:stream.seek(1024*1024+106);stream.write(flag.to_bytes(2, 'little'))
                with self.assertRaises(BackupError):backend.check_filesystem('/dev/loop42')
            observed.bound = True
            with mock.patch.object(appliance.os, 'pread') as read, self.assertRaises(BackupError):backend.check_filesystem('/dev/loop42')
            read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
