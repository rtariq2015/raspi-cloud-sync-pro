"""Local synthetic files and mocked root/configfs boundaries; no gadget actions."""

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).with_name('trial.py')
spec = importlib.util.spec_from_file_location('msc_trial', SCRIPT)
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.image = self.root / 'disk.img'
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(trial, 'ROOT', self.root))
        self.stack.enter_context(mock.patch.object(trial, 'IMAGE', self.image))
        self.observed = {'ssh_peer': '192.0.2.9', 'udc': 'fake-udc', 'root_disks': ['SD', 'SD'],
                         'wifi_profile': 'fixed', 'legacy_modules': ['g_ether'],
                         'ics': ['ActiveState=active', 'UnitFileState=enabled']}

    def filesystem(self, size=None):
        size = trial.SIZE if size is None else size
        start, count = 2048, size // 512 - 2048
        mbr, boot = bytearray(512), bytearray(512)
        mbr[450] = 7
        mbr[454:458] = start.to_bytes(4, 'little')
        mbr[458:462] = count.to_bytes(4, 'little')
        mbr[510:] = boot[510:] = b'\x55\xaa'
        boot[3:11], boot[108] = b'EXFAT   ', 9
        boot[64:72], boot[72:80] = start.to_bytes(8, 'little'), count.to_bytes(8, 'little')
        with self.image.open('xb') as stream:
            stream.truncate(size)
            stream.write(mbr)
            stream.seek(start * 512)
            stream.write(boot)
        self.image.chmod(0o600)

    def root_boundaries(self):
        # Only privileged ownership and hardware observations are mocked.
        self.stack.enter_context(mock.patch.object(trial, 'protected'))
        self.stack.enter_context(mock.patch.object(trial, 'preflight', side_effect=lambda peer, **kwargs: dict(self.observed)))
        original_fstat = os.fstat
        def fstat(fd):
            info = original_fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_size == 0:
                values = list(info);values[4] = 0;return os.stat_result(values)
            return info
        self.stack.enter_context(mock.patch.object(trial.os, 'fstat', side_effect=fstat))

    def approve(self):
        raw = (self.root / 'plan.json').read_bytes()
        trial.save('ATTACH_APPROVED.json', {'schema_version': 1, 'approve_host_export': True,
                                         'plan_sha256': hashlib.sha256(raw).hexdigest()})

    def test_sparse_regular_image_layout_and_identity_preserved(self):
        self.filesystem();before = self.image.stat()
        with mock.patch.object(trial, 'protected'):
            self.assertEqual(trial.image_layout(self.image), trial.identity(before))
        self.assertEqual(trial.identity(self.image.stat()), trial.identity(before))

    def test_wrong_size_partition_or_fat32_refused(self):
        self.filesystem()
        with mock.patch.object(trial, 'protected'):
            for offset, replacement in ((450, b'\x0c'), (510, b'xx'), (2048 * 512 + 3, b'FAT32   ')):
                with self.subTest(offset=offset), self.image.open('r+b') as stream:
                    stream.seek(offset);old = stream.read(len(replacement));stream.seek(offset);stream.write(replacement);stream.flush()
                    with self.assertRaises(RuntimeError):trial.image_layout(self.image)
                    stream.seek(offset);stream.write(old)
            with self.image.open('r+b') as stream:stream.truncate(trial.SIZE - 1)
            with self.assertRaises(RuntimeError):trial.image_layout(self.image)

    def test_8gib_image_requires_explicit_capacity_and_exact_filesystem_geometry(self):
        self.filesystem(trial.EXPANDED_SIZE)
        with mock.patch.object(trial,'protected'):
            with self.assertRaises(RuntimeError):trial.image_layout(self.image)
            before = trial.identity(self.image.stat())
            self.assertEqual(trial.image_layout(self.image,size=trial.EXPANDED_SIZE),before)
            with self.image.open('r+b') as output:
                output.seek(1024*1024+72);output.write((trial.LEGACY_SIZE//512-2048).to_bytes(8,'little'))
            with self.assertRaises(RuntimeError):trial.image_layout(self.image,size=trial.EXPANDED_SIZE)

    def test_8gib_plan_and_hash_approval_attach_without_payload_reads(self):
        self.filesystem(trial.EXPANDED_SIZE);self.root_boundaries()
        self.assertEqual(trial.operate('plan',self.observed['ssh_peer'],size=trial.EXPANDED_SIZE,approve_8gib=True),'planned')
        plan = json.loads((self.root/'plan.json').read_text())
        self.assertEqual(plan['schema_version'],2)
        self.assertEqual(plan['image_size_bytes'],trial.EXPANDED_SIZE)
        self.assertEqual(plan['image_layout'],trial.capacity_layout(trial.EXPANDED_SIZE))
        self.approve();self.observed.update(legacy_modules=[],ics=['ActiveState=inactive','UnitFileState=enabled'])
        backend,events = self.backend()
        with mock.patch.object(trial.gadget,'LinuxGadgetBackend',return_value=backend), \
                mock.patch.object(trial,'image_layout',side_effect=AssertionError('No payload reads after plan')):
            self.assertEqual(trial.operate('attach',self.observed['ssh_peer']),'host_owned')
        self.assertEqual(events,['prepare','attach'])

    def test_invalid_or_unapproved_capacity_refused_before_trial_preflight(self):
        for size in (True,4*1024**3,16*1024**3,trial.LEGACY_SIZE-1,float(trial.LEGACY_SIZE),'8'):
            with self.subTest(size=size),mock.patch.object(trial,'preflight') as preflight:
                with self.assertRaises(RuntimeError):trial.operate('plan','192.0.2.9',size=size,approve_8gib=True)
                preflight.assert_not_called()
        with mock.patch.object(trial,'preflight') as preflight:
            with self.assertRaises(RuntimeError):trial.operate('plan','192.0.2.9',size=trial.EXPANDED_SIZE)
            preflight.assert_not_called()

    def test_8gib_stale_plan_or_legacy_schema_never_constructs_backend(self):
        self.filesystem(trial.EXPANDED_SIZE);self.root_boundaries()
        trial.operate('plan',self.observed['ssh_peer'],size=trial.EXPANDED_SIZE,approve_8gib=True)
        self.approve();self.observed.update(legacy_modules=[],ics=['ActiveState=inactive','UnitFileState=enabled'])
        valid = json.loads((self.root/'plan.json').read_text())
        variants = [dict(valid,schema_version=1),dict(valid,image_size_bytes=trial.LEGACY_SIZE),
                    dict(valid,image_layout=dict(valid['image_layout'],partition_sectors=99)),
                    dict(valid,image_identity=[1,999,trial.EXPANDED_SIZE,0,0])]
        for plan in variants:
            (self.root/'plan.json').write_text(json.dumps(plan))
            with self.subTest(plan=plan),mock.patch.object(trial.gadget,'LinuxGadgetBackend') as backend, \
                    mock.patch.object(trial.os,'pread') as read,self.assertRaises(RuntimeError):
                trial.operate('attach',self.observed['ssh_peer'])
            backend.assert_not_called();read.assert_not_called()

    def test_capacity_plan_rejects_numeric_aliases_and_extra_fields(self):
        plan = {'schema_version':2,'image_identity':[1,2,trial.EXPANDED_SIZE,0,0],'observed':{},
                'image_size_bytes':trial.EXPANDED_SIZE,'image_layout':trial.capacity_layout(trial.EXPANDED_SIZE)}
        self.assertEqual(trial.validate_plan_metadata(plan),trial.EXPANDED_SIZE)
        for change in ({'schema_version':True},{'image_size_bytes':float(trial.EXPANDED_SIZE)},
                       {'extra':True},{'image_identity':[1,2,trial.EXPANDED_SIZE,False,0]},
                       {'image_layout':dict(plan['image_layout'],partition=True)}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                trial.validate_plan_metadata(dict(plan,**change))

    def test_real_symlink_hardlink_and_untrusted_owner_refused(self):
        self.filesystem()
        link = self.root / 'alias';link.symlink_to(self.image)
        for path in (link, self.image):
            with self.assertRaises(RuntimeError):trial.protected(path)
        hard = self.root / 'hard';os.link(self.image, hard)
        with mock.patch.object(trial, 'protected'), self.assertRaises(RuntimeError):trial.image_layout(self.image)
        hard.unlink()

    def test_plan_durable_private_before_switch_and_no_device_writes(self):
        self.filesystem();self.root_boundaries()
        with mock.patch.object(trial.gadget, 'LinuxGadgetBackend') as backend:
            self.assertEqual(trial.operate('plan', self.observed['ssh_peer']), 'planned')
        backend.assert_not_called()
        self.assertEqual(json.loads((self.root / 'plan.json').read_bytes())['observed']['legacy_modules'], ['g_ether'])
        self.assertIn('Never detach a loaded LUN', (self.root / 'rollback.json').read_text())
        self.assertEqual(stat.S_IMODE((self.root / 'plan.json').stat().st_mode), 0o600)
        retained = (self.root / 'plan.json').read_bytes()
        with self.assertRaises(FileExistsError):trial.operate('plan', self.observed['ssh_peer'])
        self.assertEqual((self.root / 'plan.json').read_bytes(), retained)

    def test_unapproved_or_g_ether_present_never_constructs_backend(self):
        self.filesystem();self.root_boundaries();trial.operate('plan', self.observed['ssh_peer'])
        for modules in (['g_ether'], []):
            self.observed['legacy_modules'] = modules
            with mock.patch.object(trial.gadget, 'LinuxGadgetBackend') as backend, self.assertRaises(Exception):
                trial.operate('attach', self.observed['ssh_peer'])
            backend.assert_not_called()
        self.assertFalse((self.root / 'attach-intent.json').exists())

    def backend(self, fail=False):
        events = []
        root = self.root / 'configfs-profile'
        def prepare():
            self.assertTrue((self.root / 'attach-intent.json').exists())
            self.assertTrue((self.root / 'rollback.json').exists())
            events.append('prepare')
        def attach():
            events.append('attach')
            if fail:raise OSError('private failure payload')
        result = SimpleNamespace(root=root, prepare=prepare, attach_gadget=attach,
            observe=lambda: SimpleNamespace(bound=True, backing=str(self.image), loops=(), mounts=(), profile_ok=True))
        return result, events

    def test_attach_order_no_image_payload_reads_and_no_reattach(self):
        self.filesystem();self.root_boundaries();trial.operate('plan', self.observed['ssh_peer']);self.approve()
        self.observed.update(legacy_modules=[], ics=['ActiveState=inactive', 'UnitFileState=enabled'])
        backend, events = self.backend()
        with mock.patch.object(trial.gadget, 'LinuxGadgetBackend', return_value=backend), \
                mock.patch.object(trial, 'image_layout', side_effect=AssertionError('No image contents after plan')):
            self.assertEqual(trial.operate('attach', self.observed['ssh_peer']), 'host_owned')
        self.assertEqual(events, ['prepare', 'attach'])
        with mock.patch.object(trial.gadget, 'LinuxGadgetBackend', return_value=backend):
            with self.assertRaises(FileExistsError):trial.operate('attach', self.observed['ssh_peer'])
        self.assertEqual(events, ['prepare', 'attach'])

    def test_partial_failure_retains_image_and_intent_never_detaches(self):
        self.filesystem();self.root_boundaries();trial.operate('plan', self.observed['ssh_peer']);self.approve()
        self.observed.update(legacy_modules=[], ics=['ActiveState=inactive', 'UnitFileState=enabled'])
        backend, events = self.backend(fail=True)
        before = trial.identity(self.image.stat())
        with mock.patch.object(trial.gadget, 'LinuxGadgetBackend', return_value=backend), self.assertRaises(OSError):
            trial.operate('attach', self.observed['ssh_peer'])
        self.assertEqual(events, ['prepare', 'attach']);self.assertTrue((self.root / 'attach-intent.json').exists())
        self.assertFalse((self.root / 'host-owned.json').exists());self.assertEqual(trial.identity(self.image.stat()), before)

    def test_changed_image_or_wifi_fails_before_backend(self):
        self.filesystem();self.root_boundaries();trial.operate('plan', self.observed['ssh_peer']);self.approve()
        self.observed.update(legacy_modules=[], wifi_profile='changed', ics=['ActiveState=inactive', 'UnitFileState=enabled'])
        with mock.patch.object(trial.gadget, 'LinuxGadgetBackend') as backend, self.assertRaises(RuntimeError):
            trial.operate('attach', self.observed['ssh_peer'])
        backend.assert_not_called()
        self.observed['wifi_profile'] = 'fixed'
        with self.image.open('r+b') as stream:stream.truncate(trial.SIZE - 1)
        with mock.patch.object(trial.gadget, 'LinuxGadgetBackend') as backend, self.assertRaises(RuntimeError):
            trial.operate('attach', self.observed['ssh_peer'])
        backend.assert_not_called()

    def test_scoped_io_has_no_mount_format_driver_or_global_gate_bypass(self):
        root = self.root / 'profile';operator = trial.TrialIO(root)
        with mock.patch.object(trial.os, 'geteuid', return_value=0), self.assertRaises(RuntimeError):
            operator.write(root / 'UDC', 'fake')
        operator.approved = True
        for method, args in ((operator.command, ['mount', '/anything']), (operator.provision_command, ['rpi-usb-gadget', 'on'])):
            with self.assertRaises(RuntimeError):method(args)
        self.assertIs(trial.gadget.HARDWARE_OPERATIONS_ENABLED, False)
        with mock.patch.object(trial.os, 'geteuid', return_value=0), mock.patch.object(trial.os, 'readlink', return_value='same'):
            with self.assertRaises(RuntimeError):operator.write(self.root / 'foreign', 'x')
        ast.parse(SCRIPT.read_text(), feature_version=(3, 9))

    def test_saved_active_ics_must_be_stopped_without_changing_enablement(self):
        self.filesystem();self.root_boundaries();trial.operate('plan', self.observed['ssh_peer']);self.approve()
        self.observed['legacy_modules'] = []
        for fields in (['ActiveState=active', 'UnitFileState=enabled'], ['ActiveState=inactive', 'UnitFileState=disabled']):
            self.observed['ics'] = fields
            with mock.patch.object(trial.gadget, 'LinuxGadgetBackend') as backend, self.assertRaises(RuntimeError):
                trial.operate('attach', self.observed['ssh_peer'])
            backend.assert_not_called()
        self.observed['ics'] = ['ActiveState=inactive', 'UnitFileState=enabled']
        backend, events = self.backend()
        with mock.patch.object(trial.gadget, 'LinuxGadgetBackend', return_value=backend):
            self.assertEqual(trial.operate('attach', self.observed['ssh_peer']), 'host_owned')
        self.assertEqual(events, ['prepare', 'attach'])

    def test_public_error_has_no_private_exception(self):
        output = io.StringIO()
        with mock.patch.object(trial, 'operate', side_effect=OSError('TOKEN-private-path')), contextlib.redirect_stdout(output):
            self.assertEqual(trial.main(['attach', '--ssh-peer', '192.0.2.9']), 2)
        self.assertNotIn('TOKEN', output.getvalue());self.assertFalse(json.loads(output.getvalue())['cloud_enabled'])

    def state(self, *, backing=None, configured=True):
        return SimpleNamespace(bound=True, profile_ok=True, power_ok=True, loops=(), mounts=(),
                               configured=configured, backing=str(self.image) if backing is None else backing)

    def test_eject_observation_requires_loaded_configured_then_empty_before_unbind(self):
        observations = [self.state(), self.state(backing=''), SimpleNamespace(bound=False, backing='', loops=(), mounts=())]
        backend = mock.Mock(observe=mock.Mock(side_effect=observations))
        def detach():self.assertTrue((self.root / 'eject-observed.json').is_file())
        backend.detach_gadget.side_effect = detach
        with mock.patch.object(trial.time, 'sleep'):
            self.assertEqual(trial.observe_eject(backend, 10), 'ejected_stopped')
        backend.detach_gadget.assert_called_once();self.assertTrue((self.root / 'ejected-stopped.json').exists())

    def test_empty_start_or_disconnect_cannot_authorize_detach(self):
        for observations in ([self.state(backing='')],
                             [self.state(), self.state(configured=False), self.state(backing='')]):
            with self.subTest(observations=len(observations)):
                backend = mock.Mock(observe=mock.Mock(side_effect=observations))
                with mock.patch.object(trial.time, 'sleep'), self.assertRaises(RuntimeError):trial.observe_eject(backend, 10)
                backend.detach_gadget.assert_not_called()

    def test_watch_timeout_leaves_host_lun_loaded(self):
        backend = mock.Mock(observe=mock.Mock(return_value=self.state()))
        with mock.patch.object(trial.time, 'monotonic', side_effect=[0, .1, 11]), mock.patch.object(trial.time, 'sleep'):
            with self.assertRaises(RuntimeError):trial.observe_eject(backend, 10)
        backend.detach_gadget.assert_not_called();self.assertFalse((self.root / 'eject-observed.json').exists())


if __name__ == '__main__':
    unittest.main()
