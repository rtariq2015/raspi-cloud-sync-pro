"""Ordinary local protocol/CLI tests. Root UIDs/gates/provider are fake only here."""
import copy
import fcntl
import inspect
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import batch_transfer as batches
from raspi_cloud_sync_pro import google_destination as private
from raspi_cloud_sync_pro import google_upload_dispatcher as dispatcher
from raspi_cloud_sync_pro.web_ingest import stage_upload

from raspi_cloud_sync_pro import google_web_upload_pipeline as pipeline

SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'src'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.web = self.source / 'web-inbox'
        self.target = self.root / 'staging'
        self.runtime = self.root / 'runtime'
        for path in (self.source, self.web, self.target, self.runtime):
            path.mkdir(mode=0o700)
        self.trap = self.source / 'auth.json'
        self.trap.write_bytes(b'PRIVATE_AUTH_MUST_NOT_BE_READ')
        self.trap.chmod(0o600)
        self.batch = stage_upload(io.BytesIO(b'synthetic completed bytes\0' * 8192), 'private-name.txt', self.web)
        self.batch_id = self.batch.root.stem
        self.settings = dispatcher.GoogleUploadDispatcherSettings(enabled=True, interpreter=Path(sys.executable),
            source_state=self.source, staging=self.target, dashboard_user='source-actor', backup_user='raspi-backup',
            runtime_directory=self.runtime, lock_file=self.runtime / 'broker.lock')
        self.calls, self.children, self.trace = [], [], []
        self.export_override = None
        self.describe_override = None
        self.force_export_exit = None
        self.force_describe_exit = None
        self.next_run = 0
        self.next_outcome = self.outcome('verified')

    def outcome(self, state):
        fields = {'status': 'completed', 'code': 'google_upload_dispatcher_batch_' + state,
                  'uploads_enabled': False, 'current_worker_state_checked': False, 'account_verified': False,
                  'automatic_deletion': False, **dict.fromkeys(pipeline._COUNTS, 0)}
        field = {'verified': 'files_verified_now', 'historical': 'files_previously_verified',
                 'pending': 'files_pending', 'failed': 'files_failed'}[state]
        fields[field] = 1
        return fields

    @contextmanager
    def directory(self, path, owner=None):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.directory_info(fd, owner, private=True)
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def directory_info(fd, owner, *, private=False):
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
            raise OSError('PRIVATE unsafe local fixture')
        return info

    @contextmanager
    def runtime_directory(self, settings):
        self.next_run += 1
        path = self.runtime / ('%032x' % self.next_run)
        path.mkdir(mode=0o700)
        with self.directory(path) as fd:
            yield path, fd, path.name

    @contextmanager
    def lock(self, path):
        self.trace.append(('lock', path.name))
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def spawn(self, settings, account, arguments, fds):
        self.calls.append((account.pw_uid, list(arguments), tuple(fds)))
        operation = arguments[0]
        script = ('import sys;sys.path.insert(0,' + repr(str(SOURCE_ROOT)) + ');'
                  'from raspi_cloud_sync_pro.cli import main;code=main(sys.argv[1:]);')
        if operation == 'web-batch-export' and self.export_override is not None:
            script = self.export_override(arguments)
        elif operation == 'web-batch-describe' and self.describe_override is not None:
            script = self.describe_override(arguments)
        elif operation == 'web-batch-export' and self.force_export_exit is not None:
            script += 'raise SystemExit(' + str(self.force_export_exit) + ')'
        elif operation == 'web-batch-describe' and self.force_describe_exit is not None:
            script += 'raise SystemExit(' + str(self.force_describe_exit) + ')'
        else:
            script += 'raise SystemExit(code)'
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', script] + arguments,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, pass_fds=fds, start_new_session=True, cwd='/',
            env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C', 'LC_ALL': 'C'})
        self.children.append(child)
        return child

    def dispatch(self, settings, batch_id, digest):
        self.trace.append(('dispatch', batch_id))
        self.assertTrue(all(child.poll() == 0 for child in self.children))
        self.assertTrue((self.target / (batch_id + '.ready')).is_dir())
        # A separate real lock can be acquired while the outer pipeline lock is
        # still held; reusing pipeline.lock would fail this local kernel check.
        with self.lock(settings.lock_file):
            pass
        self.assertEqual(digest, batches._manifest(json.loads((self.batch.root / 'manifest.json').read_bytes()))[2])
        return copy.deepcopy(self.next_outcome)

    @contextmanager
    def environment(self):
        source = SimpleNamespace(pw_uid=1001, pw_gid=2001)
        backup = SimpleNamespace(pw_uid=1002, pw_gid=2002)
        real_same = pipeline._same_web_anchor
        # Only OS root/UID ownership/PID1/provider boundaries are faked. Every
        # child invokes actual private CLI, streaming parser and publisher.
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(dispatcher, '_gates', side_effect=lambda settings: self.trace.append(('gates', None))))
            stack.enter_context(mock.patch.object(pipeline.preparation, '_service_identity', side_effect=lambda name: source if name == 'source-actor' else backup))
            stack.enter_context(mock.patch.object(pipeline.preparation, '_state_directory', side_effect=self.directory))
            stack.enter_context(mock.patch.object(pipeline.preparation, '_root_directory', side_effect=self.directory))
            stack.enter_context(mock.patch.object(pipeline.preparation, '_directory_info', side_effect=self.directory_info))
            stack.enter_context(mock.patch.object(pipeline, '_same_web_anchor', side_effect=lambda parent, fd, owner: real_same(parent, fd, os.getuid())))
            stack.enter_context(mock.patch.object(pipeline.preparation, '_broker_lock', side_effect=self.lock))
            stack.enter_context(mock.patch.object(dispatcher.trusted, '_runtime', side_effect=self.runtime_directory))
            stack.enter_context(mock.patch.object(pipeline.preparation, '_spawn', side_effect=self.spawn))
            operation = stack.enter_context(mock.patch.object(dispatcher, 'run_google_upload_dispatcher', side_effect=self.dispatch))
            yield operation
        for child in self.children:
            if child.poll() is None:
                os.killpg(child.pid, 9)
                child.wait(timeout=1)

    def invoke(self, batch_id=None):
        return pipeline.run_google_web_upload_pipeline(self.settings, self.batch_id if batch_id is None else batch_id)

    def assert_safe(self, report):
        self.assertEqual(set(report), {'status', 'code', 'uploads_enabled', 'current_worker_state_checked',
                                      'account_verified', 'automatic_deletion', *pipeline._COUNTS})
        for key in ('uploads_enabled', 'current_worker_state_checked', 'account_verified', 'automatic_deletion'):
            self.assertIs(report[key], False)
        raw = json.dumps(report)
        for value in (str(self.root), self.batch_id, 'manifest_sha256', 'private-name', 'PRIVATE', 'Traceback'):
            self.assertNotIn(value, raw)

    def run_records(self):
        return [path for path in self.runtime.iterdir() if path.is_dir()]

    def packet(self, *, trailing=False, complete=True):
        manifest = json.loads((self.batch.root / 'manifest.json').read_bytes())
        digest = batches._manifest(manifest)[2]
        header = private._canonical(batches._header(self.batch_id, manifest, digest))
        payload = (self.batch.root / 'files/private-name.txt').read_bytes()
        raw = len(header).to_bytes(4, 'big') + header + payload
        if complete:
            footer = private._canonical(batches._completion(self.batch_id, digest))
            raw += len(footer).to_bytes(4, 'big') + footer
        return raw + (b'TRAILING' if trailing else b'')

    def writer_script(self, data, option):
        def code(arguments):
            fd = int(arguments[arguments.index(option) + 1])
            # Literal synthetic test protocol only; the production parent never
            # reads payloads and never constructs a stream/header itself.
            return ('import os;data=' + repr(data) + ';fd=' + str(fd) + '\n'
                    'while data:\n n=os.write(fd,data);data=data[n:]\n'
                    'os.close(fd)\n')
        return code

    def test_disabled_and_bad_settings_or_id_never_touch_source_or_gates(self):
        with mock.patch.object(dispatcher, '_gates') as gates, mock.patch.object(pipeline.preparation, '_spawn') as spawn:
            for settings, batch in ((None, self.batch_id), (dispatcher.GoogleUploadDispatcherSettings(), self.batch_id),
                                    (self.settings, '../private-path')):
                self.assert_safe(pipeline.run_google_web_upload_pipeline(settings, batch))
            gates.assert_not_called()
            spawn.assert_not_called()
        self.assertEqual(self.run_records(), [])

    def test_real_cli_stream_publishes_then_dispatches_and_preserves_originals(self):
        original = {str(path): (path.stat().st_ino, path.read_bytes()) for path in self.batch.root.rglob('*') if path.is_file()}
        with self.environment() as dispatch:
            result = self.invoke()
            dispatch.assert_called_once()
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['files_verified_now'], 1)
        self.assert_safe(result)
        self.assertEqual([call[1][0] for call in self.calls], ['web-batch-describe', 'web-batch-import', 'web-batch-export'])
        self.assertEqual([call[0] for call in self.calls], [1001, 1002, 1001])
        self.assertEqual(self.trace[0], ('gates', None))
        for path, value in original.items():
            self.assertEqual((Path(path).stat().st_ino, Path(path).read_bytes()), value)
        record = self.run_records()[0]
        transfer = json.loads((record / 'transfer.json').read_bytes())
        self.assertTrue(transfer['final_frame_and_eof_verified_by_importer'])
        self.assertTrue(transfer['source_export_exit_zero'])
        for path in record.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.trap.read_bytes(), b'PRIVATE_AUTH_MUST_NOT_BE_READ')

    def test_existing_matching_destination_reverified_without_replacement(self):
        with self.environment():
            self.invoke()
            target = self.target / (self.batch_id + '.ready')
            before = {str(path): (path.stat().st_ino, path.read_bytes()) for path in target.rglob('*') if path.is_file()}
            self.next_outcome = self.outcome('historical')
            result = self.invoke()
        self.assertEqual(result['status'], 'historical')
        for path, value in before.items():
            self.assertEqual((Path(path).stat().st_ino, Path(path).read_bytes()), value)
        self.assertEqual(len(self.run_records()), 2)
        self.assertFalse(list(self.target.glob('*.incomplete')))

    def test_export_nonzero_after_actual_import_success_never_dispatches(self):
        self.force_export_exit = 2
        with self.environment() as dispatch:
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertEqual(result['code'], 'google_web_upload_transfer_failed')
        self.assertTrue((self.target / (self.batch_id + '.ready')).is_dir())
        self.assertEqual([child.returncode for child in self.children], [0, 0, 2])
        self.assert_safe(result)

    def test_description_frame_and_eof_do_not_substitute_for_describer_zero_exit(self):
        self.force_describe_exit = 2
        with self.environment() as dispatch:
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertEqual(result['code'], 'google_web_upload_describe_failed')
        self.assertEqual([call[1][0] for call in self.calls], ['web-batch-describe'])
        self.assertEqual(list(self.target.iterdir()), [])

    def test_partial_and_trailing_protocol_retain_incomplete_and_never_dispatch(self):
        self.batch = stage_upload(io.BytesIO(b'small synthetic partial stream'), 'private-name.txt', self.web)
        self.batch_id = self.batch.root.stem
        for suffix, packet in (('partial', self.packet(complete=False)), ('trailing', self.packet(trailing=True))):
            with self.subTest(suffix=suffix):
                self.export_override = self.writer_script(packet, '--output-fd')
                with self.environment() as dispatch:
                    result = self.invoke()
                    dispatch.assert_not_called()
                self.assertEqual(result['status'], 'failed')
                self.assert_safe(result)
                self.assertFalse((self.target / (self.batch_id + '.ready')).exists())
                self.assertTrue(list(self.target.glob('*.incomplete')))
        self.assertTrue((self.batch.root / 'files/private-name.txt').exists())

    def test_describe_duplicate_stale_and_nonzero_never_launch_transfer(self):
        metadata = {'schema_version': 1, 'batch_id': self.batch_id, 'manifest_sha256': 'a' * 64,
                    'file_count': 1, 'total_bytes': 0}
        for raw in (b'{"schema_version":1,"schema_version":2}', private._canonical({**metadata, 'batch_id': 'f' * 32})):
            self.describe_override = self.writer_script(len(raw).to_bytes(4, 'big') + raw, '--output-fd')
            before = len(self.calls)
            with self.environment() as dispatch:
                result = self.invoke()
                dispatch.assert_not_called()
            self.assertEqual(len(self.calls) - before, 1)
            self.assert_safe(result)
        self.describe_override = None
        with self.environment() as dispatch:
            result = self.invoke('e' * 32)
            dispatch.assert_not_called()
        self.assertEqual(result['status'], 'failed')

    def test_invalid_source_anchor_and_lock_conflict_fail_before_source(self):
        retained = self.source / 'retained-inbox'
        self.web.rename(retained)
        self.web.symlink_to(retained, target_is_directory=True)
        with self.environment() as dispatch:
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertFalse(self.calls)
        self.assert_safe(result)
        self.web.unlink()
        retained.rename(self.web)
        self.settings = dispatcher.GoogleUploadDispatcherSettings(**{**self.settings.__dict__, 'lock_file': self.runtime / 'pipeline.lock'})
        with self.environment() as dispatch:
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertEqual(result['code'], 'google_web_upload_lock_conflict')
        self.assertFalse(self.calls)

    def test_conflicting_existing_target_retained_never_replaced_or_dispatched(self):
        existing = self.target / (self.batch_id + '.ready')
        existing.mkdir(mode=0o700)
        (existing / 'files').mkdir(mode=0o700)
        (existing / 'manifest.json').write_bytes(b'{"schema_version":1,"files":[]}')
        (existing / 'manifest.json').chmod(0o600)
        before = (existing.stat().st_ino, (existing / 'manifest.json').read_bytes())
        with self.environment() as dispatch:
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertEqual((existing.stat().st_ino, (existing / 'manifest.json').read_bytes()), before)
        self.assertEqual(result['status'], 'failed')

    def test_cleanup_failure_after_both_zero_blocks_dispatch_and_keeps_ready(self):
        real_cleanup = pipeline._cleanup
        with self.environment() as dispatch, mock.patch.object(pipeline, '_cleanup', side_effect=lambda children: real_cleanup(children) and False):
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertEqual(result['code'], 'google_web_upload_cleanup_unconfirmed')
        self.assertTrue((self.target / (self.batch_id + '.ready')).exists())

    def test_pending_failed_denied_and_disabled_dispatch_results_are_truthful(self):
        for state in ('pending', 'failed'):
            self.next_outcome = self.outcome(state)
            with self.environment():
                result = self.invoke()
            self.assertEqual(result['status'], state)
            self.assertEqual(result['files_' + state], 1)
            self.assert_safe(result)
        for status, code, expected in (('denied', 'google_upload_dispatcher_admission_denied', 'google_web_upload_waiting_for_admission'),
                                      ('disabled', 'google_upload_dispatcher_disabled', 'google_web_upload_dispatch_disabled'),
                                      ('failed', 'PRIVATE-token-path', 'google_web_upload_dispatch_failed')):
            self.next_outcome = {**self.outcome('verified'), 'status': status, 'code': code, 'files_verified_now': 0}
            with self.environment():
                result = self.invoke()
            self.assertEqual(result['code'], expected)
            self.assert_safe(result)

    def test_unsafe_dispatch_result_never_leaks_private_fields(self):
        for change in ({'files_verified_now': True}, {'private_path': str(self.root)}, {'account_verified': True}, {'code': 'PRIVATE'}):
            self.next_outcome = {**self.outcome('verified'), **change}
            with self.environment():
                result = self.invoke()
            self.assertEqual(result['code'], 'google_web_upload_dispatch_result_invalid')
            self.assert_safe(result)

    def test_root_gates_refuse_before_any_source_or_directory_operation(self):
        with mock.patch.object(dispatcher, '_gates', side_effect=OSError('PRIVATE')) as gates, \
                mock.patch.object(pipeline.preparation, '_state_directory') as directories, \
                mock.patch.object(pipeline.preparation, '_spawn') as spawn:
            result = self.invoke()
        gates.assert_called_once_with(self.settings)
        directories.assert_not_called()
        spawn.assert_not_called()
        self.assert_safe(result)
        with mock.patch.object(dispatcher, '_gates', side_effect=pipeline.GoogleWebUploadPipelineError('PRIVATE arbitrary context')):
            self.assert_safe(self.invoke())

    def test_private_frame_eof_timeout_kills_and_reaps_tracked_child(self):
        def lingering(arguments):
            fd = int(arguments[arguments.index('--output-fd') + 1])
            value = {'schema_version': 1, 'batch_id': self.batch_id, 'manifest_sha256': 'a' * 64, 'file_count': 1, 'total_bytes': 0}
            raw = private._canonical(value)
            return 'import os,time;os.write(' + str(fd) + ',' + repr(len(raw).to_bytes(4, 'big') + raw) + ');time.sleep(30)'
        self.describe_override = lingering
        with self.environment() as dispatch, mock.patch.object(pipeline, '_DESCRIBE_SECONDS', .2):
            started = time.monotonic()
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(self.children), 1)
        self.assertIsNotNone(self.children[0].returncode)
        self.assert_safe(result)

    def test_start_after_deadline_never_spawns(self):
        with mock.patch.object(pipeline.preparation, '_spawn') as spawn, self.assertRaises(pipeline.GoogleWebUploadPipelineError):
            pipeline._start(self.settings, None, [], (), [], time.monotonic() - 1)
        spawn.assert_not_called()

    def test_reaped_helpers_are_not_signaled_by_stale_process_group_id(self):
        child = SimpleNamespace(pid=123456, poll=lambda: 0, wait=mock.Mock(return_value=0))
        with mock.patch.object(os, 'killpg') as kill:
            self.assertTrue(pipeline._cleanup([[child]]))
        kill.assert_not_called()

    def test_report_write_failure_retains_everything_and_does_not_claim_success(self):
        real_write = dispatcher.trusted._write_private
        def write(fd, name, data, **kwargs):
            if name == 'pipeline-result.json':
                raise OSError('PRIVATE result fsync')
            return real_write(fd, name, data, **kwargs)
        with self.environment() as dispatch, mock.patch.object(dispatcher.trusted, '_write_private', side_effect=write):
            result = self.invoke()
            dispatch.assert_called_once()
        self.assertEqual(result['status'], 'failed')
        self.assertTrue((self.target / (self.batch_id + '.ready')).exists())
        self.assertTrue((self.run_records()[0] / 'transfer.json').exists())
        self.assert_safe(result)

    def test_last_transfer_fsync_crossing_deadline_never_dispatches_retained_ready(self):
        expired = False
        real_write, real_remaining = dispatcher.trusted._write_private, pipeline._remaining
        def write(fd, name, data, **kwargs):
            nonlocal expired
            result = real_write(fd, name, data, **kwargs)
            if name == 'transfer.json':
                expired = True
            return result
        def remaining(deadline):
            if expired:
                raise pipeline.GoogleWebUploadPipelineError('google_web_upload_transfer_timeout')
            return real_remaining(deadline)
        with self.environment() as dispatch, mock.patch.object(dispatcher.trusted, '_write_private', side_effect=write), \
                mock.patch.object(pipeline, '_remaining', side_effect=remaining):
            result = self.invoke()
            dispatch.assert_not_called()
        self.assertEqual(result['code'], 'google_web_upload_transfer_timeout')
        self.assertTrue((self.target / (self.batch_id + '.ready')).is_dir())
        self.assertTrue((self.run_records()[0] / 'transfer.json').exists())

    def test_budget_and_api_have_no_clock_identity_or_proof_override(self):
        self.assertEqual(pipeline._TRANSFER_SECONDS, 300)
        self.assertEqual(pipeline._DISPATCH_SECONDS, 420)
        self.assertEqual(pipeline._ACCOUNTED_SECONDS, 725)
        self.assertEqual(list(inspect.signature(pipeline.run_google_web_upload_pipeline).parameters), ['settings', 'selected_batch_id'])
        metadata = {'schema_version': 1, 'batch_id': self.batch_id, 'manifest_sha256': 'a' * 64, 'file_count': 1, 'total_bytes': 0}
        for changes in ({'schema_version': True}, {'file_count': 0}, {'total_bytes': True}, {'manifest_sha256': 'PRIVATE'}, {'extra': 'private'}):
            with self.assertRaises(pipeline.GoogleWebUploadPipelineError):
                pipeline._metadata({**metadata, **changes}, self.batch_id)


if __name__ == '__main__':
    unittest.main()
