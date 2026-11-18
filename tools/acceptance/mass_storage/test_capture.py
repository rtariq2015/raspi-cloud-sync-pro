"""No native actions: real private staging/frames plus explicit hardware mocks."""

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).with_name('capture.py')
spec = importlib.util.spec_from_file_location('msc_capture', SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.mount, self.staging = self.root / 'mount', self.root / 'staging'
        self.image = self.root / 'image'
        with self.image.open('xb') as output:
            output.write(b'not a filesystem');output.truncate(capture.trial.LEGACY_SIZE)
        self.account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        self.events, self.released = [], False
        self.stack = contextlib.ExitStack();self.addCleanup(self.stack.close)
        for obj, name, value in ((capture, 'MOUNT', self.mount), (capture, 'STAGING', self.staging),
                                 (capture.trial, 'ROOT', self.root), (capture.trial, 'IMAGE', self.image)):
            self.stack.enter_context(mock.patch.object(obj, name, value))
        plan = {'schema_version':1, 'image_identity': capture.trial.identity(self.image.stat()), 'observed':{}}
        self.stack.enter_context(mock.patch.object(capture.trial, 'private_file', return_value=json.dumps(plan).encode()))
        self.stack.enter_context(mock.patch.object(capture.trial, 'protected'))
        self.stack.enter_context(mock.patch.object(capture, '_service_identity', return_value=self.account))

    def seen(self, backing=None, configured=True, bound=True):
        return SimpleNamespace(bound=bound, configured=configured, profile_ok=True, power_ok=True,
                               loops=(), mounts=(), backing=str(self.image) if backing is None else backing)

    def test_watch_arms_then_actual_eject_before_release(self):
        state, reports = [], []
        backend = mock.Mock(observe=mock.Mock(side_effect=[self.seen(), self.seen(backing=''), self.seen(backing='', bound=False)]))
        with mock.patch.object(capture.trial, 'save', side_effect=lambda name, value: reports.append(name)), mock.patch.object(capture.time, 'sleep'):
            self.assertEqual(capture.watched(backend, 10, state), 'ejected_stopped')
        self.assertEqual(state, [backend]);self.assertEqual(reports, ['capture-armed.json', 'capture-eject-observed.json'])
        backend.detach_gadget.assert_called_once()

    def test_disconnect_empty_start_and_timeout_never_release_or_read(self):
        cases = ([self.seen(backing='')], [self.seen(), self.seen(configured=False), self.seen(backing='')])
        for observations in cases:
            state = [];backend = mock.Mock(observe=mock.Mock(side_effect=observations))
            with mock.patch.object(capture.trial, 'save'), mock.patch.object(capture.time, 'sleep'), self.assertRaises(RuntimeError):
                capture.watched(backend, 10, state)
            self.assertEqual(state, []);backend.detach_gadget.assert_not_called()
        state = [];backend = mock.Mock(observe=mock.Mock(return_value=self.seen()))
        with mock.patch.object(capture.trial, 'save'), mock.patch.object(capture.time, 'sleep'), \
                mock.patch.object(capture.time, 'monotonic', side_effect=[0, .1, 11]), self.assertRaises(RuntimeError):
            capture.watched(backend, 10, state)
        self.assertEqual(state, []);backend.detach_gadget.assert_not_called()

    def backend(self, stage_error=False):
        backend = mock.Mock(root=self.root / 'configfs')
        backend.observe.return_value = self.seen(backing='', bound=False)
        def event(name, result=None):
            def action(*args):
                self.assertTrue(self.released);self.events.append(name);return result
            return action
        backend.attach_loop_read_only.side_effect = event('loop', '/dev/loop42')
        backend._block_devices.return_value = [{'name': '/dev/loop42p1'}]
        backend.verify_loop_read_only.side_effect = event('verify-loop')
        backend.check_filesystem.side_effect = event('fsck')
        backend.mount_read_only.side_effect = event('mount')
        backend.verify_mount_read_only.side_effect = event('verify-mount')
        backend.unmount.side_effect = event('unmount')
        backend.detach_loop.side_effect = event('detach-loop')
        backend._loops.return_value = []
        def operate(action, peer, timeout):
            self.assertEqual(action, 'watch-eject')
            # Use the actual invocation-local observer, without a receipt shortcut.
            observed = backend.observe
            backend.observe = mock.Mock(side_effect=[self.seen(), self.seen(backing=''), self.seen(backing='', bound=False)])
            try:
                with mock.patch.object(capture.time, 'sleep'):
                    result = capture.trial.observe_eject(backend, timeout)
            finally:backend.observe = observed
            self.released = True;return result
        self.stack.enter_context(mock.patch.object(capture.trial, 'operate', side_effect=operate))
        self.stack.enter_context(mock.patch.object(capture.trial, 'save', side_effect=lambda name, value: self.events.append(name)))
        self.stack.enter_context(mock.patch.object(capture, 'clean_header', side_effect=event('clean-header')))
        self.stack.enter_context(mock.patch.object(capture.os, 'statvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)))
        result = {'schema_version': 1, 'batch_id': 'a' * 32, 'file_count': 1, 'total_bytes': 14}
        def stage(account):
            self.assertTrue(self.released);self.events.append('stage')
            if stage_error:raise OSError('private child failure')
            return result
        self.stack.enter_context(mock.patch.object(capture, 'stage_child', side_effect=stage))
        return backend

    def test_capture_order_and_normal_cleanup_no_reattach(self):
        backend = self.backend();original = capture.trial.observe_eject
        result = capture.capture('192.0.2.9', 10)
        self.assertEqual(result['status'], 'captured');self.assertFalse(result['cloud_verified'])
        self.assertIs(capture.trial.observe_eject, original)
        for before, after in (('capture-eject-observed.json', 'loop'), ('fsck', 'mount'), ('mount', 'stage'),
                              ('stage', 'unmount'), ('unmount', 'detach-loop'), ('detach-loop', 'capture-complete.json')):
            self.assertLess(self.events.index(before), self.events.index(after))
        backend.attach_gadget.assert_not_called()

    def test_watch_failure_has_no_loop_mount_staging_or_header_read(self):
        original = capture.trial.observe_eject
        with mock.patch.object(capture.trial, 'operate', side_effect=RuntimeError('no eject')), \
                mock.patch.object(capture, 'CaptureIO') as io_class, mock.patch.object(capture, 'clean_header') as header:
            with self.assertRaises(RuntimeError):capture.capture('192.0.2.9', 10)
        io_class.assert_not_called();header.assert_not_called();self.assertFalse(self.staging.exists())
        self.assertIs(capture.trial.observe_eject, original)

    def test_failed_child_cleanup_retains_image_and_staging(self):
        backend = self.backend(stage_error=True)
        with self.image.open('rb') as incoming:before = incoming.read(512)
        with self.assertRaises(OSError):capture.capture('192.0.2.9', 10)
        backend.unmount.assert_called_once();backend.detach_loop.assert_called_once()
        with self.image.open('rb') as incoming:self.assertEqual(incoming.read(512),before)
        self.assertEqual(self.image.stat().st_size,capture.trial.LEGACY_SIZE)
        self.assertTrue(self.staging.is_dir())
        self.assertNotIn('capture-complete.json', self.events)

    def test_existing_staging_refuses_no_overwrite_after_release(self):
        backend = self.backend();self.staging.mkdir();sentinel = self.staging / 'keep';sentinel.write_bytes(b'retain')
        with self.assertRaises(RuntimeError):capture.capture('192.0.2.9', 10)
        backend.attach_loop_read_only.assert_not_called();self.assertEqual(sentinel.read_bytes(), b'retain')

    def test_scoped_commands_only_ro_loop_exact_mount_and_ordinary_cleanup(self):
        operator = capture.CaptureIO(self.root / 'profile', self.account)
        with mock.patch.object(operator, 'require_mutations'), mock.patch.object(capture.trial, 'command', return_value=b'/dev/loop42\n') as command:
            operator.command(['losetup', '--find', '--show', '--read-only', '--partscan', str(self.image)])
            operator.command(['mount', '-t', 'exfat', '-o', 'ro,nosuid,nodev,noexec', '/dev/loop42p1', str(self.mount)])
            args = command.call_args.args[0]
            self.assertIn('uid='+str(os.getuid()), args[4]);self.assertIn('fmask=0177,dmask=0077', args[4])
            for forbidden in (['mkfs.exfat', '/dev/loop42p1'], ['losetup', '--find', str(self.image)],
                              ['umount', '-l', str(self.mount)], ['fsck.exfat', '-a', '/dev/loop42p1']):
                with self.assertRaises(RuntimeError):operator.command(forbidden)
            with self.assertRaises(RuntimeError):operator.write(self.root / 'profile/UDC', 'any')

    def test_actual_child_snapshot_retains_original_and_publishes_private_frame(self):
        self.mount.mkdir(mode=0o700);self.staging.mkdir(mode=0o700)
        original = self.mount / 'qa.txt';original.write_bytes(b'actual USB fixture bytes')
        report, writer = os.pipe()
        args = ['child', str(self.mount), str(self.staging), str(writer)]
        source_stat = os.statvfs(self.mount)
        source_flags = SimpleNamespace(**{name: getattr(source_stat, name) for name in dir(source_stat) if name.startswith('f_')})
        source_flags.f_flag |= os.ST_RDONLY
        try:
            with mock.patch.object(sys, 'argv', args), mock.patch.object(os, 'getgroups', return_value=[]), \
                    mock.patch.object(os, 'statvfs', return_value=source_flags):
                exec(compile(capture.CHILD, '<staging-child>', 'exec'), {})
            frame = os.read(report, 4096);self.assertEqual(os.read(report, 1), b'')
        finally:os.close(report)
        value = json.loads(frame[4:]);self.assertEqual(int.from_bytes(frame[:4], 'big'), len(frame)-4)
        batch = self.staging / (value['batch_id']+'.ready')
        self.assertEqual((batch / 'files/qa.txt').read_bytes(), original.read_bytes())
        self.assertEqual(value['file_count'], 1);self.assertEqual(value['total_bytes'], original.stat().st_size)
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600)
                            for p in (batch, batch/'files', batch/'files/qa.txt', batch/'manifest.json')))

    def test_dirty_exfat_header_refused_without_repair(self):
        with self.image.open('r+b') as stream:
            count = capture.trial.LEGACY_SIZE//512-2048
            mbr, header = bytearray(512), bytearray(512)
            mbr[450] = 7;mbr[454:458] = (2048).to_bytes(4,'little');mbr[458:462] = count.to_bytes(4,'little')
            mbr[510:] = header[510:] = b'\x55\xaa'
            header[3:11] = b'EXFAT   ';header[108] = 9
            header[72:80] = count.to_bytes(8,'little')
            stream.write(mbr);stream.seek(1024*1024);stream.write(header)
        expected = capture.trial.identity(self.image.stat())[:3]
        capture.clean_header(expected)
        for flag in (2, 4):
            with self.image.open('r+b') as stream:stream.seek(1024*1024+106);stream.write(flag.to_bytes(2, 'little'))
            with self.assertRaises(RuntimeError):capture.clean_header(expected)

    def test_private_error_projection_and_python39_grammar(self):
        output = io.StringIO()
        with mock.patch.object(capture, 'capture', side_effect=OSError('secret-payload')), contextlib.redirect_stdout(output):
            self.assertEqual(capture.main(['--ssh-peer', '192.0.2.9']), 2)
        self.assertNotIn('secret', output.getvalue());self.assertFalse(json.loads(output.getvalue())['cloud_verified'])
        ast.parse(SCRIPT.read_text(), feature_version=(3, 9));ast.parse(capture.CHILD, feature_version=(3, 9))

    def manual_environment(self, *, backing='', approval=None, size=None):
        size = capture.trial.LEGACY_SIZE if size is None else size
        if size != capture.trial.LEGACY_SIZE:
            with self.image.open('r+b') as output:output.truncate(size)
        observed = {'ssh_peer': '192.0.2.9', 'udc': 'fake-udc', 'legacy_modules': [],
                    'ics': ['ActiveState=inactive', 'UnitFileState=enabled'], 'wifi': 'same'}
        prior = {**observed, 'legacy_modules': ['g_ether'], 'ics': ['ActiveState=active', 'UnitFileState=enabled']}
        plan = {'schema_version': 1, 'image_identity': capture.trial.identity(self.image.stat()), 'observed': prior}
        if size == capture.trial.EXPANDED_SIZE:
            plan.update(schema_version=2,image_size_bytes=size,image_layout=capture.trial.capacity_layout(size))
        raw = json.dumps(plan).encode()
        digest = hashlib.sha256(raw).hexdigest()
        manual = {'schema_version': 1, 'plan_sha256': digest, 'user_safe_eject_confirmed': True,
                  'previous_loaded_observation_confirmed': True, 'no_admin_forced_eject': True}
        if approval is not None:manual.update(approval)
        data = {'plan.json': raw, 'MANUAL_CAPTURE_APPROVED.json': json.dumps(manual).encode(),
                'ATTACH_APPROVED.json': json.dumps({'schema_version': 1, 'plan_sha256': digest, 'approve_host_export': True}).encode(),
                'attach-intent.json': json.dumps({'schema_version': 1, 'plan_sha256': digest}).encode()}
        (self.root / 'operator.lock').touch(mode=0o600)
        self.stack.enter_context(mock.patch.object(capture.trial, 'preflight', return_value=observed))
        self.stack.enter_context(mock.patch.object(capture.trial, 'private_file', side_effect=lambda path: data[path.name]))
        self.stack.enter_context(mock.patch.object(capture.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1)))
        backend = mock.Mock(root=self.root / 'profile')
        backend.observe.side_effect = [self.seen(backing=backing), self.seen(backing='', bound=False)]
        factory = self.stack.enter_context(mock.patch.object(capture.trial.gadget, 'LinuxGadgetBackend', return_value=backend))
        reports = []
        self.stack.enter_context(mock.patch.object(capture.trial, 'save', side_effect=lambda name, value: reports.append((name, value))))
        continuation = self.stack.enter_context(mock.patch.object(capture, 'capture_released', return_value={'status': 'captured'}))
        return backend, factory, continuation, reports, data

    def test_explicit_manual_confirmation_is_truthful_separate_route_not_fake_watch(self):
        backend, _, continuation, reports, _ = self.manual_environment()
        with mock.patch.object(capture.trial, 'operate') as watched_operator, mock.patch.object(capture, 'watched') as watched:
            self.assertEqual(capture.confirmed_handoff('192.0.2.9'), {'status': 'captured'})
        watched_operator.assert_not_called();watched.assert_not_called();backend.detach_gadget.assert_called_once()
        continuation.assert_called_once_with(backend, self.account, 'operator_confirmed_host_safe_eject')
        self.assertEqual(reports[0][0], 'manual-release.json')
        self.assertFalse(reports[0][1]['live_session_eject_observed'])
        self.assertIs(reports[0][1]['user_safe_eject_confirmed'], True)

    def test_manual_confirmation_never_clears_loaded_lun(self):
        backend, _, continuation, reports, _ = self.manual_environment(backing=str(self.image))
        with self.assertRaises(RuntimeError):capture.confirmed_handoff('192.0.2.9')
        backend.detach_gadget.assert_not_called();continuation.assert_not_called();self.assertEqual(reports, [])

    def test_manual_separate_approval_missing_false_or_wrong_binding_refuses(self):
        _, factory, continuation, _, data = self.manual_environment()
        valid = data['MANUAL_CAPTURE_APPROVED.json']
        for change in ({'no_admin_forced_eject': False}, {'user_safe_eject_confirmed': 1},
                       {'previous_loaded_observation_confirmed': False}, {'plan_sha256': '0'*64}):
            value = json.loads(valid);value.update(change);data['MANUAL_CAPTURE_APPROVED.json'] = json.dumps(value).encode()
            with self.subTest(change=change), self.assertRaises(RuntimeError):capture.confirmed_handoff('192.0.2.9')
        del data['MANUAL_CAPTURE_APPROVED.json']
        with self.assertRaises(KeyError):capture.confirmed_handoff('192.0.2.9')
        factory.assert_not_called();continuation.assert_not_called()

    def test_manual_mode_requires_explicit_cli_selection(self):
        output = io.StringIO()
        with mock.patch.object(capture, 'capture', return_value={'status': 'watched'}) as watched, \
                mock.patch.object(capture, 'confirmed_handoff', return_value={'status': 'manual'}) as manual, contextlib.redirect_stdout(output):
            self.assertEqual(capture.main(['--ssh-peer', '192.0.2.9']), 0)
            manual.assert_not_called();watched.assert_called_once()
            self.assertEqual(capture.main(['--ssh-peer', '192.0.2.9', '--operator-confirmed-handoff']), 0)
            manual.assert_called_once_with('192.0.2.9')

    def test_8gib_manual_handoff_uses_same_bound_capacity_plan_without_payload_reads(self):
        backend,_,continuation,_,_ = self.manual_environment(size=capture.trial.EXPANDED_SIZE)
        with mock.patch.object(capture.os,'pread') as read:
            self.assertEqual(capture.confirmed_handoff('192.0.2.9'),{'status':'captured'})
        read.assert_not_called();backend.detach_gadget.assert_called_once()
        continuation.assert_called_once_with(backend,self.account,'operator_confirmed_host_safe_eject')

    def test_manual_handoff_rejects_stale_8gib_layout_before_backend_or_payload_reads(self):
        _,factory,continuation,reports,data = self.manual_environment(size=capture.trial.EXPANDED_SIZE)
        plan = json.loads(data['plan.json'])
        plan['image_layout']['partition_sectors'] = capture.trial.LEGACY_SIZE//512-2048
        data['plan.json'] = json.dumps(plan).encode()
        with mock.patch.object(capture.os,'pread') as read,self.assertRaises(RuntimeError):
            capture.confirmed_handoff('192.0.2.9')
        read.assert_not_called();factory.assert_not_called();continuation.assert_not_called();self.assertEqual(reports,[])

    def test_8gib_watched_capture_preserves_release_before_readonly_capture(self):
        with self.image.open('r+b') as output:output.truncate(capture.trial.EXPANDED_SIZE)
        plan = {'schema_version':2,'image_identity':capture.trial.identity(self.image.stat()),'observed':{},
                'image_size_bytes':capture.trial.EXPANDED_SIZE,
                'image_layout':capture.trial.capacity_layout(capture.trial.EXPANDED_SIZE)}
        backend = self.backend()
        with mock.patch.object(capture.trial,'private_file',return_value=json.dumps(plan).encode()):
            result = capture.capture('192.0.2.9',10)
        self.assertEqual(result['status'],'captured')
        self.assertLess(self.events.index('capture-eject-observed.json'),self.events.index('loop'))
        self.assertLess(self.events.index('fsck'),self.events.index('clean-header'))
        backend.attach_gadget.assert_not_called()


if __name__ == '__main__':
    unittest.main()
