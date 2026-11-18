"""Ordinary local synthetic files and real private Unix frames; no cloud/PID1."""
import ast
import copy
import hashlib
import inspect
import json
import os
import socket
import stat
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import google_broker as preparation
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_operational as operational
from raspi_cloud_sync_pro import google_test_broker as trusted
from raspi_cloud_sync_pro import google_test_executor as executor
from raspi_cloud_sync_pro import wifi_confinement
from raspi_cloud_sync_pro.errors import BackupError

from raspi_cloud_sync_pro import google_upload_dispatcher as dispatcher
import test_google_test_broker as support
import test_google_upload as fixtures

FILE = Path(dispatcher.__file__)
BATCH, CONTROL = fixtures.BATCH, fixtures.CONTROL
PRIVATE = 'PRIVATE-upload-folder-token-account-exception'


def admitted():
    value = fixtures.admission()
    value['checked_at'] = time.time()
    value['test']['tested_at'] = value['checked_at'] - 10
    return value


def completed(value, metadata, state='verified'):
    prepared = value['preparation']
    binding = operational._binding(prepared['request_id'], prepared['folder_name'],
                                   prepared['authorization_sha256'], prepared['rclone_config_sha256'])
    counts = dict.fromkeys(dispatcher._COUNTS, 0)
    counts[{'verified': 'files_verified_now', 'historical': 'files_previously_verified',
            'pending': 'files_pending', 'failed': 'files_failed'}[state]] = metadata['file_count']
    return {'schema_version': 1, 'provider': 'google_drive', 'batch_state': state,
            'batch_id': metadata['batch_id'], 'manifest_sha256': metadata['manifest_sha256'],
            'control_request_id': value['control_request_id'], 'preparation': copy.deepcopy(prepared),
            'test': copy.deepcopy(value['test']),
            'destination_id': operational._destination_id(value['test']['generation_id'], binding),
            'completed_at': time.time(), **counts, 'automatic_deletion': False}


class Source:
    def __init__(self, value, events, change=None, clock=None):
        self.value, self.events, self.change, self.clock = value, events, change, clock
        self.calls = 0
        self.latest = None

    def admission(self, deadline):
        self.calls += 1
        if self.calls > 2:
            raise AssertionError('No admission cancellation after release')
        self.events.append('source' + str(self.calls))
        if self.clock is not None:
            self.clock[0] = 1000 if self.calls == 1 else 1040
        value = copy.deepcopy(self.value)
        value['checked_at'] = time.time()
        if self.calls == 2 and self.change:
            self.change(value)
        self.latest = copy.deepcopy(value)
        return value


class Manager(support.FakeManager):
    def __init__(self, source, metadata, events, *, state='verified', changes=None, **kwargs):
        super().__init__(source.value, events, **kwargs)
        self.source, self.metadata, self.state, self.changes = source, metadata, state, changes or {}
        self.gate = self.admission = self.completion = None
        self.expected_exit = None

    def work(self):
        deadline = time.monotonic() + 3
        try:
            canary = self.clients['canary']
            executor.canary_write_frame(canary.fileno(), {'schema_version': 1, 'provider': 'google_drive',
                'phase': 'ready', 'worker_pid': 12345}, deadline)
            go = executor.canary_read_frame(canary.fileno(), deadline)
            self.events.append('canary_go')
            report = {**go, 'phase': 'report', **dict.fromkeys(trusted._CANARY_FLAGS, True),
                      'official_https_bytes': 64, **self.report_changes}
            executor.canary_write_frame(canary.fileno(), report, deadline)
            if self.trailing:
                os.write(canary.fileno(), b'x')
            canary.shutdown(socket.SHUT_WR)
            acknowledgement = executor.canary_read_frame(canary.fileno(), deadline)
            if acknowledgement != {**go, 'phase': 'ack'}:
                raise AssertionError('Wrong canary ACK')
            executor._eof(canary.fileno(), deadline)
            self.admission = destination._read_frame(self.clients['request'].fileno(), deadline)
            self.gate = destination._read_frame(self.clients['gate'].fileno(), deadline)
            expected = fixtures.release(self.admission, self.metadata['batch_id'], self.metadata['manifest_sha256'],
                                       worker_pid=12345, expires_at=self.gate['expires_at'])
            if (self.admission != self.source.latest or self.gate != expected
                    or go['request_id'] != self.admission['control_request_id']
                    or not time.time() < self.gate['expires_at'] <= time.time() + 30):
                raise AssertionError('Wrong upload release')
            self.released = True
            self.events.append('credential_release')
            receipt = {**completed(self.admission, self.metadata, self.state), **self.changes}
            destination._write_frame(self.clients['receipt'].fileno(), destination._canonical(receipt), deadline)
            self.clients['receipt'].shutdown(socket.SHUT_WR)
            self.completion = destination._read_frame(self.clients['receipt'].fileno(), deadline)
            expected = {'schema_version': 1, 'provider': 'google_drive', 'phase': 'upload_complete',
                'worker_pid': 12345, 'control_request_id': go['request_id'],
                'batch_id': self.metadata['batch_id'], 'manifest_sha256': self.metadata['manifest_sha256'], 'nonce': go['nonce']}
            if self.completion != expected:
                raise AssertionError('Wrong completion ACK')
            self.events.append('complete_ack')
        except Exception as error:
            self.errors.append(type(error).__name__)

    def wait_exit(self, unit, description, invocation, worker, deadline, expected_code):
        self.expected_exit = expected_code
        self.thread.join(1)
        self.events.append('worker_exit')
        if (self.fail == 'exit' or self.errors or self.thread.is_alive()
                or expected_code != (0 if self.state in ('verified', 'historical') else 2)):
            raise ValueError(PRIVATE)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime, self.staging = self.root / 'runtime', self.root / 'staging'
        for path in self.runtime, self.staging:
            path.mkdir(mode=0o700)
        self.settings = dispatcher.GoogleUploadDispatcherSettings(enabled=True,
            interpreter=Path('/opt/approved/venv/bin/python3'), rclone=Path('/opt/approved/rclone'),
            rclone_sha256='2' * 64, source_state=self.root / 'source', backup_state=self.root / 'prepared',
            operational_state=self.root / 'operational', staging=self.staging,
            dashboard_user='source-user', backup_user='raspi-backup', public_origin='https://backup.example',
            approval_file=self.root / 'UPLOAD_APPROVED', runtime_directory=self.runtime,
            lock_file=self.runtime / 'broker.lock', wifi_profile_uuid=support.UUID, dns_ipv4='1.1.1.1',
            config_path=self.root / 'config.json')
        self.metadata = {'schema_version': 1, 'batch_id': BATCH, 'manifest_sha256': '3' * 64,
                         'file_count': 2, 'total_bytes': 10}
        self.backup = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        self.events, self.proofs = [], 0
        self.proof_failure = None
        self.config_checks = 0
        self.config_failure = None
        self.batch_checks = 0
        self.batch_failure = None
        self.paths = []

    @contextmanager
    def runtime_context(self, settings):
        path = self.runtime / ('4' * 32)
        path.mkdir(mode=0o700)
        self.paths.append(path)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield path, fd, '4' * 32
        finally:
            os.close(fd)

    @contextmanager
    def proof(self, group, interface):
        self.assertEqual(group, '/system.slice/raspi-cloud-google-test-' + '4' * 32 + '.service')
        self.assertEqual(interface, 7)
        outer = self
        class Held:
            def recheck(self):
                outer.proofs += 1
                outer.events.append('proof' + str(outer.proofs))
                if outer.proof_failure == outer.proofs:
                    raise ValueError(PRIVATE)
        self.events.append('proof_held')
        try:
            yield Held()
        finally:
            self.events.append('proof_closed')

    def config(self, settings):
        self.config_checks += 1
        self.events.append('config' + str(self.config_checks))
        if self.config_checks == self.config_failure:
            raise ValueError(PRIVATE)

    def batch(self):
        self.batch_checks += 1
        self.events.append('batch' + str(self.batch_checks))
        if self.batch_checks == self.batch_failure:
            raise ValueError(PRIVATE)

    def dispatch(self, *, state='verified', change=None, clock=None, **kwargs):
        value = admitted()
        if clock is not None:
            value['checked_at'], value['test']['tested_at'] = 1000, 900
        source = Source(value, self.events, change=change, clock=clock)
        manager = Manager(source, self.metadata, self.events, state=state, **kwargs)
        outer = self
        class Echo:
            def __init__(self, nonce):
                self.nonce, self.port = nonce.encode(), 12345
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def positive(self):
                outer.events.append('loopback_positive')
            def assert_quiet(self):
                outer.events.append('loopback_quiet')
        with mock.patch.object(trusted, '_runtime', side_effect=self.runtime_context), \
                mock.patch.object(trusted, '_network', return_value={'interface_index': 7}), \
                mock.patch.object(trusted, '_Loopback', Echo), \
                mock.patch.object(dispatcher, '_configuration_current', side_effect=self.config), \
                mock.patch.object(wifi_confinement, 'hold_wifi_confinement', side_effect=self.proof):
            result = dispatcher._dispatch(self.settings, self.backup, self.metadata, self.batch,
                                          time.monotonic() + 5, manager, source)
        return result, source, manager

    def safe(self, result):
        text = json.dumps(result)
        for private in (PRIVATE, BATCH, CONTROL, 'Synthetic Folder', str(self.root), '3' * 64, 'Traceback'):
            self.assertNotIn(private, text)
        for flag in ('uploads_enabled', 'current_worker_state_checked', 'account_verified', 'automatic_deletion'):
            self.assertIs(result[flag], False)

    def test_all_four_valid_receipts_finish_live_and_match_expected_exit(self):
        for state in ('verified', 'historical', 'pending', 'failed'):
            with self.subTest(state=state):
                result, source, manager = self.dispatch(state=state)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['code'], 'google_upload_dispatcher_batch_' + state)
                self.assertEqual(sum(result[name] for name in dispatcher._COUNTS), 2)
                self.assertEqual(source.calls, 2)
                self.assertTrue(manager.released)
                self.assertEqual(manager.expected_exit, 0 if state in ('verified', 'historical') else 2)
                self.assertFalse(manager.errors)
                self.assertTrue(manager.closed)
                self.assertEqual(len(manager.stopped), 1)
                self.assertLess(self.events.index('completion_recheck'), self.events.index('complete_ack'))
                self.assertLess(self.events.index('complete_ack'), self.events.index('worker_exit'))
                self.assertLess(self.events.index('worker_exit'), self.events.index('unit_stop'))
                self.assertEqual(stat.S_IMODE((self.paths[-1] / 'receipt.json').stat().st_mode), 0o600)
                self.safe(result)
                (self.paths[-1]).rename(self.runtime / state)
                self.events.clear()

    def test_latest_frame_checked_at_changes_but_full_latest_hash_is_gate_binding(self):
        clock = [1000]
        with mock.patch.object(dispatcher.time, 'time', side_effect=lambda: clock[0]):
            result, source, manager = self.dispatch(clock=clock)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(manager.admission['checked_at'], 1040)
        self.assertEqual(manager.gate['admission_sha256'], hashlib.sha256(destination._canonical(manager.admission)).hexdigest())
        self.assertNotEqual(manager.gate['admission_sha256'], hashlib.sha256(destination._canonical({**manager.admission, 'checked_at': 1000})).hexdigest())
        self.assertEqual(source.calls, 2)

    def test_changed_control_generation_folder_or_denial_prevents_release(self):
        mutations = [lambda v: v.update(control_request_id='9' * 32),
            lambda v: v['test'].update(generation_id='8' * 32),
            lambda v: v['test'].update(folder_id=PRIVATE),
            lambda v: v.update(admission_authorized=False, control_request_id=None, preparation=None, test=None)]
        for index, change in enumerate(mutations):
            result, source, manager = self.dispatch(change=change)
            self.assertEqual(result['code'], 'google_upload_dispatcher_authority_changed')
            self.assertFalse(manager.released)
            self.assertIsNone(manager.completion)
            self.assertEqual(source.calls, 2)
            self.assertEqual(len(manager.stopped), 1)
            self.safe(result)
            self.paths[-1].rename(self.runtime / str(index))

    def test_no_or_forged_receipt_never_receives_completion_ack(self):
        for index, changes in enumerate(({'batch_id': '9' * 32}, {'manifest_sha256': '9' * 64},
                {'control_request_id': '9' * 32}, {'destination_id': PRIVATE}, {'files_verified_now': 1},
                {'automatic_deletion': True}, {'extra': PRIVATE})):
            result, _, manager = self.dispatch(changes=changes)
            self.assertEqual(result['code'], 'google_upload_dispatcher_receipt_invalid')
            self.assertIsNone(manager.completion)
            self.assertNotIn('complete_ack', self.events)
            self.assertFalse((self.paths[-1] / 'receipt.json').exists())
            self.safe(result)
            self.paths[-1].rename(self.runtime / str(index))
            self.events.clear()

    def test_completion_failure_keeps_private_receipt_but_does_not_ack(self):
        self.proof_failure = 4
        result, _, manager = self.dispatch(state='pending')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue((self.paths[-1] / 'receipt.json').exists())
        self.assertIsNone(manager.completion)
        self.assertEqual(len(manager.stopped), 1)
        self.safe(result)

    def test_cleanup_failure_overrides_a_valid_completed_receipt(self):
        result, _, manager = self.dispatch(fail='stop')
        self.assertEqual(result['code'], 'google_upload_dispatcher_cleanup_unconfirmed')
        self.assertIsNotNone(manager.completion)
        self.assertTrue((self.paths[-1] / 'receipt.json').exists())
        self.safe(result)

    def test_root_config_and_batch_changes_stop_before_release_or_completion(self):
        for phase, number in (('config_failure', 1), ('batch_failure', 1), ('config_failure', 2), ('batch_failure', 2)):
            setattr(self, phase, number)
            result, _, manager = self.dispatch()
            self.assertEqual(result['status'], 'failed')
            self.assertIsNone(manager.completion)
            self.assertEqual(manager.released, number == 2)
            self.assertEqual(len(manager.stopped), 1)
            self.paths[-1].rename(self.runtime / (phase + str(number)))
            self.config_checks = self.batch_checks = 0
            self.config_failure = self.batch_failure = None

    def test_unit_is_upload_specific_with_readonly_staging_and_longer_bounds(self):
        result, _, manager = self.dispatch()
        self.assertEqual(result['status'], 'completed')
        self.assertIn('raspi_cloud_sync_pro.google_upload_executor', manager.arguments)
        self.assertNotIn('raspi_cloud_sync_pro.google_test_executor', manager.arguments)
        self.assertTrue(manager.description.startswith('Private Google selected-batch upload '))
        self.assertEqual(manager.properties['ReadOnlyPaths'], str(self.settings.backup_state) + ' ' + str(self.staging))
        self.assertEqual(manager.properties['ReadWritePaths'], str(self.settings.operational_state))
        self.assertEqual(manager.properties['RuntimeMaxSec'], '390')
        self.assertEqual(manager.arguments[-2:], ['--timeout', '300'])
        self.assertEqual(len(manager.properties['OpenFile']), 4)
        self.assertEqual(manager.properties['CapabilityBoundingSet'], '')
        self.assertNotIn('SuccessExitStatus', manager.properties)

    def test_denied_first_admission_does_not_start_network_or_runtime(self):
        denied = {**admitted(), 'admission_authorized': False, 'control_request_id': None, 'preparation': None, 'test': None}
        source = SimpleNamespace(admission=lambda deadline: denied)
        with mock.patch.object(trusted, '_network') as network, mock.patch.object(trusted, '_runtime') as runtime:
            result = dispatcher._dispatch(self.settings, self.backup, self.metadata, self.batch,
                                          time.monotonic() + 5, mock.Mock(), source)
        self.assertEqual(result['status'], 'denied')
        network.assert_not_called()
        runtime.assert_not_called()
        self.safe(result)

    def test_disabled_and_wrong_settings_never_touch_state_or_commands(self):
        with mock.patch.object(dispatcher, '_gates') as gates, mock.patch('os.open') as opened, \
                mock.patch.object(trusted, '_command') as command:
            self.assertEqual(dispatcher.run_google_upload_dispatcher(dispatcher.GoogleUploadDispatcherSettings(), PRIVATE, PRIVATE)['status'], 'disabled')
            self.assertEqual(dispatcher.run_google_upload_dispatcher(trusted.GoogleTestBrokerSettings(), BATCH, '3' * 64)['status'], 'failed')
        gates.assert_not_called()
        opened.assert_not_called()
        command.assert_not_called()

    def test_no_public_native_proof_or_clock_bypass(self):
        self.assertEqual(list(inspect.signature(dispatcher.run_google_upload_dispatcher).parameters), ['settings', 'batch_id', 'expected_manifest_sha256'])
        ast.parse(FILE.read_text(), feature_version=(3, 9))

    def configuration(self):
        result = {'schema_version': 1, 'enabled': True}
        for name in dispatcher._FIELDS - set(result):
            value = getattr(self.settings, name)
            result[name] = str(value) if isinstance(value, Path) else value
        return result

    def test_config_loader_rejects_unsafe_or_ambiguous_settings_and_has_no_root_flag(self):
        valid = self.configuration()
        with mock.patch.object(preparation, '_root_json', return_value=(valid, ('private-config',))):
            parsed = dispatcher.GoogleUploadDispatcherSettings.load(self.settings.config_path)
        self.assertEqual(parsed.staging, self.staging)
        self.assertIs(parsed.enabled, True)
        self.assertNotIn(str(self.root), repr(parsed))
        cases = [{'enabled': 1}, {'allow_metered': 'yes'}, {'dns_ipv4': '127.0.0.1'},
                 {'staging': str(self.settings.operational_state / 'nested')},
                 {'staging': str(self.settings.backup_state)}, {'staging': '/unsafe/../alias'},
                 {'backup_user': 'root'}, {'dashboard_user': 'raspi-backup'}, {'rclone_sha256': PRIVATE},
                 {'native_proof_override': True}]
        for change in cases:
            with self.subTest(change=change), mock.patch.object(preparation, '_root_json', return_value=({**valid, **change}, ())):
                with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                    dispatcher.GoogleUploadDispatcherSettings.load(self.settings.config_path)
        with mock.patch.object(preparation, '_root_json', return_value=({'schema_version': 1}, ())):
            self.assertIs(dispatcher.GoogleUploadDispatcherSettings.load(self.settings.config_path).enabled, False)
        path = self.root / 'ordinary-owned-config.json'
        path.write_text(json.dumps(valid))
        path.chmod(0o600)
        if os.geteuid() != 0:
            with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                dispatcher.GoogleUploadDispatcherSettings.load(path)
        self.assertEqual(list(inspect.signature(dispatcher.GoogleUploadDispatcherSettings.load).parameters), ['root_config_path'])

    def test_separate_upload_marker_cannot_reuse_test_approval_and_precedes_commands(self):
        with mock.patch.object(preparation, '_platform_gate'), \
                mock.patch.object(dispatcher.os, 'readlink', return_value='net:[same]'), \
                mock.patch.object(dispatcher.GoogleUploadDispatcherSettings, 'load', return_value=self.settings), \
                mock.patch.object(preparation, '_root_json', return_value=(trusted._APPROVAL, ())), \
                mock.patch.object(trusted, '_python_environment') as imports, \
                mock.patch.object(preparation, '_installed_package_gate') as installed, \
                mock.patch.object(trusted, '_command') as command:
            with self.assertRaisesRegex(dispatcher.GoogleUploadDispatcherError, '^google_upload_dispatcher_approval_required$'):
                dispatcher._gates(self.settings)
        imports.assert_not_called()
        installed.assert_not_called()
        command.assert_not_called()
        with mock.patch.object(dispatcher.GoogleUploadDispatcherSettings, 'load', return_value=self.settings), \
                mock.patch.object(preparation, '_root_json', return_value=(dispatcher._APPROVAL, ())):
            dispatcher._configuration_current(self.settings)

    @contextmanager
    def local_anchor(self, path, owner):
        self.assertEqual(owner, os.geteuid())
        with destination._private_directory(path) as fd:
            yield fd

    def selected(self):
        batch = self.staging / (BATCH + '.ready')
        (batch / 'files').mkdir(parents=True, mode=0o700)
        batch.chmod(0o700)
        (batch / 'files' / 'public.bin').write_bytes(b'public-test')
        (batch / 'files' / 'public.bin').chmod(0o600)
        manifest = {'schema_version': 1, 'files': [{'relative': 'public.bin', 'size': 11,
                    'sha256': hashlib.sha256(b'public-test').hexdigest()}]}
        path = batch / 'manifest.json'
        path.write_bytes(destination._canonical(manifest))
        path.chmod(0o600)
        digest = hashlib.sha256(destination._canonical(manifest)).hexdigest()
        return batch, path, manifest, digest

    def test_actual_selected_manifest_metadata_is_readonly_and_replacement_is_rejected(self):
        batch, path, _, digest = self.selected()
        original = path.read_bytes(), destination._file_identity(path.stat())
        fd = os.open(self.staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(preparation, '_state_directory', side_effect=self.local_anchor):
                with dispatcher._selected_batch(self.settings, BATCH, digest, fd, os.geteuid()) as (metadata, recheck):
                    self.assertEqual(metadata['file_count'], 1)
                    self.assertEqual(metadata['total_bytes'], 11)
                    recheck()
                    self.assertEqual((path.read_bytes(), destination._file_identity(path.stat())), original)
                    old = self.root / 'old-staging'
                    self.staging.rename(old)
                    self.staging.mkdir(mode=0o700)
                    with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                        recheck()
            self.assertEqual((old / batch.name / 'manifest.json').read_bytes(), original[0])
        finally:
            os.close(fd)

    def test_selected_manifest_requires_hash_nonempty_private_files_and_no_aliases(self):
        batch, path, manifest, digest = self.selected()
        original = path.read_bytes()
        fd = os.open(self.staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(preparation, '_state_directory', side_effect=self.local_anchor):
                for malformed in ({'schema_version': 1, 'files': []},
                        {**manifest, 'files': [{**manifest['files'][0], 'relative': '../outside'}]}):
                    path.write_bytes(destination._canonical(malformed))
                    with self.assertRaises(BackupError):
                        with dispatcher._selected_batch(self.settings, BATCH, digest, fd, os.geteuid()):
                            self.fail('unsafe selected metadata accepted')
                path.write_bytes(original)
                path.chmod(0o644)
                with self.assertRaises(BackupError):
                    with dispatcher._selected_batch(self.settings, BATCH, digest, fd, os.geteuid()):
                        self.fail('insecure manifest accepted')
                path.chmod(0o600)
                alias = self.root / 'manifest-alias'
                os.link(path, alias)
                with self.assertRaises(BackupError):
                    with dispatcher._selected_batch(self.settings, BATCH, digest, fd, os.geteuid()):
                        self.fail('hardlinked manifest accepted')
                alias.unlink()
                original_path = batch / 'saved-manifest'
                path.rename(original_path)
                path.symlink_to(original_path)
                with self.assertRaises(OSError):
                    with dispatcher._selected_batch(self.settings, BATCH, digest, fd, os.geteuid()):
                        self.fail('symlinked manifest accepted')
                path.unlink()
                original_path.rename(path)
                moved = self.staging / 'retained-ready'
                batch.rename(moved)
                batch.symlink_to(moved, target_is_directory=True)
                with self.assertRaises(OSError):
                    with dispatcher._selected_batch(self.settings, BATCH, digest, fd, os.geteuid()):
                        self.fail('symlinked batch accepted')
        finally:
            os.close(fd)

    def test_manifest_mutation_during_dispatch_is_rejected_without_overwrite(self):
        _, path, manifest, digest = self.selected()
        fd = os.open(self.staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(preparation, '_state_directory', side_effect=self.local_anchor):
                with dispatcher._selected_batch(self.settings, BATCH, digest, fd, os.geteuid()) as (_, recheck):
                    changed = destination._canonical({**manifest, 'files': [{**manifest['files'][0], 'size': 12}]})
                    path.write_bytes(changed)
                    with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                        recheck()
                    self.assertEqual(path.read_bytes(), changed)
        finally:
            os.close(fd)

    def test_receipt_exact_manifest_count_types_and_time_are_mandatory(self):
        value = admitted()
        valid = completed(value, self.metadata)
        self.assertEqual(dispatcher._receipt(valid, value, self.metadata, 0)[1], 0)
        for change in ({'files_verified_now': True}, {'files_verified_now': 1}, {'completed_at': float('nan')},
                       {'completed_at': time.time() + 60}, {'schema_version': True}, {'test': None},
                       {'preparation': {}}, {'automatic_deletion': True}, {'batch_state': 'enabled'}):
            with self.subTest(change=change), self.assertRaises((dispatcher.GoogleUploadDispatcherError, TypeError)):
                dispatcher._receipt({**valid, **change}, value, self.metadata, 0)
        with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
            dispatcher._receipt(valid, value, self.metadata, valid['completed_at'] + 1)

    def test_admission_uses_actual_30_second_window_and_copies_nested_authority(self):
        value = admitted()
        snapshot = dispatcher._admission(value)
        value['test']['folder_id'] = PRIVATE
        self.assertNotEqual(value['test'], snapshot['test'])
        for age in (30, 31, -1):
            changed = {**snapshot, 'checked_at': time.time() - age}
            with self.subTest(age=age), self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                dispatcher._admission(changed)
        near = copy.deepcopy(snapshot)
        near['checked_at'] = time.time() - 29
        near['test']['tested_at'] = near['checked_at'] - 1
        self.assertIsNotNone(dispatcher._admission(near))

    def test_source_transport_requires_independent_zero_exit_and_eof_and_consumes_fds(self):
        value = admitted()
        child = SimpleNamespace(wait=lambda **kwargs: 0, poll=lambda: 0)
        captured = []
        packet = destination._canonical(value)
        def spawn(settings, account, arguments, passed):
            self.assertEqual(arguments[0], 'google-control-admission')
            self.assertNotIn('google-control-export', arguments)
            self.assertEqual(passed, (int(arguments[-1]),))
            captured.extend(passed)
            os.write(passed[0], len(packet).to_bytes(4, 'big') + packet)
            return child
        source = dispatcher._Source(self.settings, self.backup)
        with mock.patch.object(preparation, '_spawn', side_effect=spawn):
            self.assertEqual(source.admission(time.monotonic() + 2), value)
            child.wait = lambda **kwargs: 2
            with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                source.admission(time.monotonic() + 2)
            child.wait = lambda **kwargs: 0
            packet += b'x'
            with self.assertRaises(BackupError):
                source.admission(time.monotonic() + 2)
        for fd in captured:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_worker_staging_requires_actual_readonly_matching_mount(self):
        info = self.staging.stat()
        identity = info.st_dev, info.st_ino
        manager = dispatcher._Manager(self.settings, self.backup, time.monotonic() + 2, identity)
        expected = '/proc/12345/root' + str(self.staging)
        with mock.patch.object(dispatcher.os, 'stat', return_value=info) as observed, \
                mock.patch.object(dispatcher.os, 'statvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            manager._staging(12345)
            observed.assert_called_once_with(expected, follow_symlinks=False)
        with mock.patch.object(dispatcher.os, 'stat', return_value=info), \
                mock.patch.object(dispatcher.os, 'statvfs', return_value=SimpleNamespace(f_flag=0)):
            with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                manager._staging(12345)
        manager.staging_identity = info.st_dev, info.st_ino + 1
        with mock.patch.object(dispatcher.os, 'stat', return_value=info):
            with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                manager._staging(12345)
        worker = SimpleNamespace(pid=12345, close=mock.Mock())
        with mock.patch.object(trusted._Systemd, 'authenticate', return_value=worker), \
                mock.patch.object(manager, '_staging', side_effect=OSError(PRIVATE)):
            with self.assertRaises(OSError):
                manager.authenticate('unit', 'description', 'invocation', ['fixed-argv'])
        worker.close.assert_called_once_with()

    def test_wait_requires_receipt_specific_exit_code_systemd_result_and_empty_cgroup(self):
        manager = dispatcher._Manager(self.settings, self.backup, time.monotonic() + 2, (1, 2))
        worker = SimpleNamespace(pidfd=44, control_group='/system.slice/raspi-cloud-google-test-' + '4' * 32 + '.service')
        selector = mock.MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [object()]
        value = {'MainPID': '0', 'ExecMainCode': '1', 'ExecMainStatus': '0', 'Result': 'success'}
        with mock.patch.object(dispatcher.selectors, 'DefaultSelector', return_value=selector), \
                mock.patch.object(manager, 'show', return_value=value), \
                mock.patch.object(manager, 'capture', return_value='same'), \
                mock.patch.object(trusted, '_cgroup_pids', return_value=()) as members:
            manager.wait_exit('unit', 'description', 'same', worker, time.monotonic() + 2, 0)
            value.update(ExecMainStatus='2', Result='exit-code')
            manager.wait_exit('unit', 'description', 'same', worker, time.monotonic() + 2, 2)
            for code, changes in ((0, {}), (2, {'Result': 'success'}), (2, {'ExecMainCode': '2'})):
                with mock.patch.dict(value, changes), self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                    manager.wait_exit('unit', 'description', 'same', worker, time.monotonic() + 2, code)
            members.return_value = (12345,)
            with self.assertRaises(dispatcher.GoogleUploadDispatcherError):
                manager.wait_exit('unit', 'description', 'same', worker, time.monotonic() + 2, 2)

    def test_start_identity_and_exit_failures_cleanup_only_the_owned_unit(self):
        for index, failure in enumerate(('start', 'authenticate', 'recheck', 'completion', 'exit')):
            result, _, manager = self.dispatch(fail=failure)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(len(manager.stopped), 1)
            unit, description, invocation = manager.stopped[0]
            self.assertEqual(unit, manager.unit)
            self.assertEqual(description, manager.description)
            self.assertEqual(invocation, None if failure == 'start' else manager.invocation)
            if failure in ('start', 'authenticate', 'recheck'):
                self.assertFalse(manager.released)
            self.safe(result)
            self.paths[-1].rename(self.runtime / str(index))
            self.events.clear()

    def test_missing_held_proof_never_releases_credentials(self):
        self.proof_failure = 1
        result, _, manager = self.dispatch()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(manager.released)
        self.assertNotIn('canary_go', self.events)
        self.assertEqual(len(manager.stopped), 1)
        self.safe(result)

    def test_source_frame_bound_and_expired_deadline_fail_before_any_authority_use(self):
        source = dispatcher._Source(self.settings, self.backup)
        child = SimpleNamespace(wait=lambda **kwargs: 0, poll=lambda: 0)
        def oversize(settings, account, arguments, passed):
            os.write(passed[0], (16385).to_bytes(4, 'big'))
            return child
        with mock.patch.object(preparation, '_spawn', side_effect=oversize):
            with self.assertRaises(BackupError):
                source.admission(time.monotonic() + 2)
        with mock.patch.object(preparation, '_spawn') as spawn, mock.patch.object(preparation, '_private_pipe') as pipe:
            with self.assertRaises(BackupError):
                source.admission(time.monotonic() - 1)
        spawn.assert_not_called()
        pipe.assert_not_called()

    def test_coordinator_namespace_mismatch_refuses_before_source_state_or_command(self):
        def namespace(path):
            return 'net:[1]' if path == '/proc/self/ns/net' else 'net:[2]'
        with mock.patch.object(preparation, '_platform_gate'), \
                mock.patch.object(dispatcher.os, 'readlink', side_effect=namespace), \
                mock.patch.object(dispatcher, '_configuration_current') as configuration, \
                mock.patch.object(dispatcher, '_Source') as source, \
                mock.patch.object(preparation, '_service_identity') as identities, \
                mock.patch.object(preparation, '_state_directory') as state, \
                mock.patch.object(trusted, '_command') as command:
            result = dispatcher.run_google_upload_dispatcher(self.settings, BATCH, '3' * 64)
        self.assertEqual(result['code'], 'google_upload_dispatcher_namespace_invalid')
        for operation in (configuration, source, identities, state, command):
            operation.assert_not_called()
        self.safe(result)
