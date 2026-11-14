"""Local private framing only; no root, BPF release, real canary or provider."""
import contextlib
import errno
import fcntl
import io
import os
import socket
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import google_destination as destination, google_upload
from raspi_cloud_sync_pro import google_test_executor as trusted

from raspi_cloud_sync_pro import cli, google_upload_executor as executor

BATCH, CONTROL, NONCE, GENERATION = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32
MANIFEST, PIN = 'e' * 64, 'f' * 64
PRIVATE = 'PRIVATE-upload-executor-client-token-folder-exception'
ERROR = 'Private Google upload executor unavailable; no private details were printed.\n'


def binding(phase):
    return {'schema_version': 1, 'provider': 'google_drive', 'phase': phase, 'worker_pid': os.getpid(),
            'request_id': CONTROL, 'nonce': NONCE, 'dns_ipv4': '192.168.4.1', 'loopback_port': 54321}


def complete(**changes):
    return {'schema_version': 1, 'provider': 'google_drive', 'phase': 'upload_complete', 'worker_pid': os.getpid(),
            'control_request_id': CONTROL, 'batch_id': BATCH, 'manifest_sha256': MANIFEST, 'nonce': NONCE, **changes}


def admission():
    return {'schema_version': 1, 'provider': 'google_drive', 'admission_authorized': True, 'single_admission': True,
            'checked_at': time.time(), 'control_request_id': CONTROL, 'preparation': {'private': PRIVATE},
            'test': {'generation_id': GENERATION, 'private': PRIVATE}}


def receipt(state):
    source = admission()
    counts = {'verified': (1, 1, 0, 0), 'historical': (0, 2, 0, 0),
              'pending': (1, 0, 1, 0), 'failed': (1, 0, 0, 1)}[state]
    return {'schema_version': 1, 'provider': 'google_drive', 'batch_state': state, 'batch_id': BATCH,
            'manifest_sha256': MANIFEST, 'control_request_id': CONTROL, 'preparation': source['preparation'],
            'test': source['test'], 'destination_id': PRIVATE, 'completed_at': time.time(), 'automatic_deletion': False,
            **dict(zip(executor._COUNTS, counts))}


class UploadExecutorTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.peers, self.channels, self.accepted = {}, {}, {}
        self.originals = []
        self.canary_calls = 0
        self.sandbox_calls = 0
        self.state = 'verified'
        self.worker_error = None
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for peer in self.peers.values():
            peer.close()
        for fd in self.originals:
            try:
                os.close(fd)
            except OSError:
                pass

    @contextlib.contextmanager
    def activation(self):
        self.events.append('activation')
        for name in ('gate', 'request', 'receipt', 'canary'):
            parent, child = socket.socketpair()
            self.peers[name] = parent
            fd = child.detach()
            self.originals.append(fd)
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            self.accepted[fd] = destination._descriptor_identity(fd), flags
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self.channels[name] = fd
            if name == 'canary':
                self.canary_fd = fd
        self.prepare_frames()
        try:
            yield self.channels, self.accepted
        finally:
            for fd, (identity, flags) in self.accepted.items():
                trusted._safe_close(fd, identity, flags)

    def prepare_frames(self):
        self.send(self.peers['canary'], getattr(self, 'go', binding('go')))
        self.send(self.peers['canary'], getattr(self, 'ack', binding('ack')))
        if not getattr(self, 'canary_eof_missing', False):
            self.peers['canary'].shutdown(socket.SHUT_WR)
        self.send(self.peers['request'], getattr(self, 'source_admission', admission()))
        self.peers['request'].shutdown(socket.SHUT_WR)
        self.send(self.peers['gate'], {'synthetic_private_gate': PRIVATE})
        self.peers['gate'].shutdown(socket.SHUT_WR)
        if not getattr(self, 'completion_missing', False):
            self.send(self.peers['receipt'], getattr(self, 'completion', complete()))
        if not getattr(self, 'completion_eof_missing', False):
            self.peers['receipt'].shutdown(socket.SHUT_WR)

    @staticmethod
    def send(peer, value):
        trusted.canary_write_frame(peer.fileno(), value, time.monotonic() + 1)

    def sandbox(self):
        self.events.append('sandbox')
        self.sandbox_calls += 1
        if getattr(self, 'post_sandbox_failure', False) and self.sandbox_calls == 2:
            raise RuntimeError(PRIVATE)

    def inherited(self, allowed):
        self.events.append(('inherited', len(allowed)))
        if len(allowed) == 1:
            fd = next(iter(allowed))
            self.assertEqual(fd, self.canary_fd)  # Completion safely reuses the closed canary number.
            self.assertTrue(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
            self.assertEqual(destination._descriptor_identity(fd), self.receipt_identity)

    def runner(self, executable, digest, deadline, config):
        self.events.append('public_pin')
        self.assertEqual((executable, digest, config), ('/protected/rclone', PIN, Path('/dev/null')))
        return object()

    def canaries(self, value, runner, deadline):
        self.events.append('public_canaries')
        self.canary_calls += 1
        return {**value, 'phase': 'report', **dict.fromkeys(trusted._REPORT_FLAGS, True), 'official_https_bytes': 64}

    def upload(self, prepared, operational, staging, batch_id, digest, source, **options):
        self.events.append('upload_api_stub')
        self.assertEqual((prepared, operational, staging), (Path('/private/prepared'), Path('/private/operational'), Path('/private/staging')))
        self.assertEqual((batch_id, digest, source['control_request_id']),
                         (BATCH, MANIFEST, getattr(self, 'source_admission', admission())['control_request_id']))
        self.assertEqual(options['timeout'], 300)
        self.assertEqual(options['executable_sha256'], PIN)
        self.assertEqual(dict(os.environ), {})
        self.receipt_identity = self.accepted_receipt_identity
        with destination._channel(options['root_gate_fd'], writing=False) as fd:
            self.assertEqual(destination._read_frame(fd, destination._deadline(1)), {'synthetic_private_gate': PRIVATE})
        if self.worker_error:
            raise self.worker_error
        return google_upload.GoogleUploadOutcome({**receipt(self.state),
            'control_request_id': source['control_request_id'], 'preparation': source['preparation'], 'test': source['test']})

    def execute(self, **changes):
        # This is a protocol-only fake activation, never an accepted root peer,
        # native sandbox or BPF proof. The actual private CLI uses real streams.
        @contextlib.contextmanager
        def active():
            with self.activation() as value:
                self.accepted_receipt_identity = destination._descriptor_identity(self.channels['receipt'])
                yield value
        arguments = dict(prepared_state='/private/prepared', operational_state='/private/operational',
            staging='/private/staging', batch_id=BATCH, manifest_sha256=MANIFEST, control_request_id=CONTROL,
            executable='/protected/rclone', executable_sha256=PIN, timeout=300, canary_timeout=.1)
        arguments.update(changes)
        with mock.patch.dict(os.environ, {'PRIVATE_PROXY_OVERRIDE': PRIVATE}, clear=True), \
                mock.patch.object(executor, 'cli', cli), \
                mock.patch.object(trusted, '_activation', active), mock.patch.object(trusted, '_sandbox', self.sandbox), \
                mock.patch.object(trusted, '_inherited', self.inherited), mock.patch.object(trusted, '_canaries', self.canaries), \
                mock.patch.object(executor.google_test, '_Runner', self.runner), \
                mock.patch.object(google_upload, 'run_google_batch_upload', side_effect=self.upload), \
                mock.patch.object(destination, '_private_directory', side_effect=AssertionError('No actual credentials/state')):
            return executor.run_google_upload_executor(**arguments)

    def closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def read_receipt(self):
        return destination._read_frame(self.peers['receipt'].fileno(), destination._deadline(1))

    def test_all_valid_receipt_states_hold_completion_then_return_cli_semantics(self):
        for state, expected in (('verified', 0), ('historical', 0), ('pending', 2), ('failed', 2)):
            with self.subTest(state=state):
                self.state = state
                self.sandbox_calls = 0
                self.events.clear()
                result = self.execute()
                self.assertEqual(result, expected)
                self.assertEqual(self.read_receipt()['batch_state'], state)
                self.assertEqual(self.sandbox_calls, 2)
                self.assertLess(self.events.index('public_canaries'), self.events.index('upload_api_stub'))
                self.assertLess(self.events.index('upload_api_stub'), self.events.index(('inherited', 1)))
                self.assertEqual([event for event in self.events if isinstance(event, tuple)],
                                 [('inherited', 4), ('inherited', 3), ('inherited', 1)])
                canary = self.peers['canary'].fileno()
                self.assertEqual(trusted.canary_read_frame(canary, time.monotonic() + 1)['phase'], 'ready')
                self.assertEqual(trusted.canary_read_frame(canary, time.monotonic() + 1)['phase'], 'report')
                trusted._eof(canary, time.monotonic() + 1)
                for fd in self.originals:
                    self.closed(fd)
                self.cleanup()
                self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []

    def test_missing_activation_fails_before_args_sandbox_public_pin_or_state(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(trusted, '_sandbox') as sandbox, \
                mock.patch.object(executor.google_test, '_Runner') as runner, \
                mock.patch.object(destination, '_private_directory', side_effect=AssertionError('No state')):
            with self.assertRaises(executor.GoogleUploadExecutorError):
                executor.run_google_upload_executor('PRIVATE', 'PRIVATE', 'PRIVATE', BATCH, MANIFEST, CONTROL, 'PRIVATE', PIN)
        sandbox.assert_not_called()
        runner.assert_not_called()

    def test_invalid_metadata_never_reaches_canary_or_private_cli(self):
        for changes in ({'batch_id': '../PRIVATE'}, {'control_request_id': 'x' * 32}, {'manifest_sha256': 'x'},
                {'prepared_state': 'relative'}, {'staging': '/private/../unsafe'}, {'executable_sha256': 'x'},
                {'timeout': True}, {'timeout': float('nan')}, {'timeout': 301}, {'canary_timeout': 0}):
            with self.subTest(changes=changes), mock.patch.object(cli, '_run_private_google_upload') as private:
                with self.assertRaises(executor.GoogleUploadExecutorError):
                    self.execute(**changes)
                private.assert_not_called()
            self.assertEqual(self.canary_calls, 0)
            self.cleanup()
            self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []

    def test_wrong_canary_control_id_pid_or_ack_prevents_private_cli(self):
        for field, value in (('request_id', '9' * 32), ('worker_pid', os.getpid() + 1), ('schema_version', True)):
            self.go = {**binding('go'), field: value}
            with self.subTest(field=field), self.assertRaises(executor.GoogleUploadExecutorError):
                self.execute()
            self.assertNotIn('upload_api_stub', self.events)
            self.cleanup(); self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []
        del self.go
        self.ack = {**binding('ack'), 'nonce': '9' * 32}
        with self.assertRaises(executor.GoogleUploadExecutorError):
            self.execute()
        self.assertNotIn('upload_api_stub', self.events)

    def test_wrong_upload_completion_or_old_test_ack_never_reports_success(self):
        cases = (complete(phase='complete'), complete(control_request_id='9' * 32), complete(batch_id='9' * 32),
                 complete(manifest_sha256='9' * 64), complete(nonce='9' * 32), complete(worker_pid=True),
                 complete(schema_version=True), {**complete(), 'extra': PRIVATE},
                 {key: value for key, value in complete().items() if key != 'batch_id'})
        for value in cases:
            self.completion = value
            with self.subTest(fields=set(value)), self.assertRaises(executor.GoogleUploadExecutorError):
                self.execute()
            self.assertEqual(self.read_receipt()['batch_state'], 'verified')
            for fd in self.originals:
                self.closed(fd)
            self.cleanup(); self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []

    def test_pending_and_failed_cannot_skip_live_completion_ack(self):
        self.completion_missing = True
        self.completion_eof_missing = True
        for state in ('verified', 'pending', 'failed'):
            self.state = state
            with self.subTest(state=state), mock.patch.object(executor, 'COMPLETION_TIMEOUT', .01):
                with self.assertRaises(executor.GoogleUploadExecutorError):
                    self.execute()
            self.assertEqual(self.read_receipt()['batch_state'], state)
            self.cleanup(); self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []

    def test_completion_requires_eof_under_same_deadline(self):
        self.completion_eof_missing = True
        with mock.patch.object(executor, 'COMPLETION_TIMEOUT', .01), self.assertRaises(executor.GoogleUploadExecutorError):
            self.execute()
        self.assertEqual(self.read_receipt()['batch_state'], 'verified')

    def test_post_cli_sandbox_failure_cannot_accept_written_receipt(self):
        self.post_sandbox_failure = True
        with self.assertRaises(executor.GoogleUploadExecutorError):
            self.execute()
        with self.assertRaises(destination.GoogleDestinationError):
            self.read_receipt()  # No orderly completion EOF; an unaccepted frame may be retained.

    def test_api_exception_or_receipt_write_failure_has_no_success_marker_or_ack_wait(self):
        original_write = destination._write_frame
        def write(fd, raw, deadline):
            if fd == self.channels.get('receipt'):
                raise OSError(PRIVATE)
            return original_write(fd, raw, deadline)
        for failure in ('api', 'receipt_write'):
            self.worker_error = RuntimeError(PRIVATE) if failure == 'api' else None
            self.events.clear()
            self.sandbox_calls = 0
            patch = (mock.patch.object(destination, '_write_frame', side_effect=write)
                     if failure == 'receipt_write' else contextlib.nullcontext())
            with self.subTest(failure=failure), patch, self.assertRaises(executor.GoogleUploadExecutorError):
                self.execute()
            with self.assertRaises(destination.GoogleDestinationError):
                self.read_receipt()
            self.assertNotIn(('inherited', 1), self.events)
            self.assertEqual(self.sandbox_calls, 1)
            self.cleanup(); self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []

    def test_invalid_summary_from_replacement_cli_does_not_enter_completion(self):
        safe = google_upload.GoogleUploadOutcome(receipt('verified')).projection()
        cases = (None, {**safe, 'private': PRIVATE}, {**safe, 'batch_state': PRIVATE},
                 {**safe, 'current_worker_state_checked': True}, {**safe, 'files_verified_now': True},
                 {**safe, 'files_pending': 1}, {**safe, 'batch_state': 'historical'})
        for value in cases:
            self.events.clear()
            with self.subTest(kind=type(value).__name__), mock.patch.object(cli, '_run_private_google_upload', return_value=value):
                with self.assertRaises(executor.GoogleUploadExecutorError):
                    self.execute()
            self.assertNotIn(('inherited', 1), self.events)
            for fd in self.originals:
                self.closed(fd)
            self.cleanup(); self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []

    def test_parser_and_default_refusal_never_echo_private_values(self):
        values = (['--batch-id', PRIVATE], ['--root-gate-fd', PRIVATE], ['--enable'], ['--now', PRIVATE])
        for arguments in values:
            out, err = io.StringIO(), io.StringIO()
            with self.subTest(arguments=arguments), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(executor.main(arguments), 2)
            self.assertEqual((out.getvalue(), err.getvalue()), ('', ERROR))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(trusted, '_activation') as active:
            self.assertEqual(executor.main(['--help']), 0)
        active.assert_not_called()
        self.assertIn('--control-request-id', out.getvalue())
        self.assertNotIn('--root-gate-fd', out.getvalue())
        self.assertEqual(err.getvalue(), '')

    def test_valid_parser_args_without_activation_refuse_before_native_checks(self):
        arguments = ['--prepared-state', '/private/prepared', '--operational-state', '/private/operational',
            '--staging', '/private/staging', '--batch-id', BATCH, '--manifest-sha256', MANIFEST,
            '--control-request-id', CONTROL, '--rclone', '/protected/rclone', '--rclone-sha256', PIN]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(trusted, '_sandbox') as sandbox, \
                mock.patch.object(executor.google_test, '_Runner') as runner, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(executor.main(arguments), 2)
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ERROR))
        sandbox.assert_not_called()
        runner.assert_not_called()

    def test_main_runtime_exception_suppresses_private_context(self):
        arguments = ['--prepared-state', '/private/prepared', '--operational-state', '/private/operational',
            '--staging', '/private/staging', '--batch-id', BATCH, '--manifest-sha256', MANIFEST,
            '--control-request-id', CONTROL, '--rclone', '/protected/rclone', '--rclone-sha256', PIN]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(executor, 'run_google_upload_executor', side_effect=RuntimeError(PRIVATE)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(executor.main(arguments), 2)
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ERROR))

    def test_actual_cli_rejects_different_admission_control_before_any_upload_api_or_gate_read(self):
        # This was the reported cross-phase mismatch: argv/GO/completion remain
        # CONTROL while a self-consistent admission/receipt would use another ID.
        self.source_admission = {**admission(), 'control_request_id': '9' * 32}
        read = destination._read_frame
        frame_reads = []
        def observed(fd, deadline):
            frame_reads.append(fd)
            return read(fd, deadline)
        with mock.patch.object(destination, '_read_frame', side_effect=observed), \
                self.assertRaises(executor.GoogleUploadExecutorError):
            self.execute()
        self.assertNotIn('upload_api_stub', self.events)
        self.assertEqual(frame_reads, [self.channels['request']])
        self.assertNotIn(('inherited', 1), self.events)
        for fd in self.originals:
            self.closed(fd)

    def test_cli_default_keeps_standalone_operator_semantics_without_expected_control(self):
        self.source_admission = {**admission(), 'control_request_id': '9' * 32}
        with self.activation():
            self.accepted_receipt_identity = destination._descriptor_identity(self.channels['receipt'])
            args = types.SimpleNamespace(prepared_state=Path('/private/prepared'), operational_state=Path('/private/operational'),
                staging=Path('/private/staging'), batch_id=BATCH, manifest_sha256=MANIFEST,
                admission_fd=self.channels['request'], root_gate_fd=self.channels['gate'], receipt_fd=self.channels['receipt'],
                rclone=Path('/protected/rclone'), rclone_sha256=PIN, timeout=300)
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(google_upload, 'run_google_batch_upload', side_effect=self.upload):
                report = cli._run_private_google_upload(args)
            self.assertEqual(report['batch_state'], 'verified')
            self.assertIn('upload_api_stub', self.events)
            # The held canary was intentionally unused in this standalone CLI test.
            self.peers['receipt'].recv(65536)

    def test_private_expected_control_argument_is_validated_before_admission_or_api(self):
        for expected in (True, '', '../bad', 'a' * 31):
            with self.subTest(expected=expected), self.activation():
                args = types.SimpleNamespace(prepared_state=Path('/private/prepared'), operational_state=Path('/private/operational'),
                    staging=Path('/private/staging'), batch_id=BATCH, manifest_sha256=MANIFEST,
                    admission_fd=self.channels['request'], root_gate_fd=self.channels['gate'], receipt_fd=self.channels['receipt'],
                    rclone=Path('/protected/rclone'), rclone_sha256=PIN, timeout=300)
                with mock.patch.object(destination, '_read_frame', side_effect=AssertionError('No input read')), \
                        mock.patch.object(google_upload, 'run_google_batch_upload') as api:
                    with self.assertRaises(executor.BackupError):
                        cli._run_private_google_upload(args, expected_control_request_id=expected)
                api.assert_not_called()
            self.cleanup(); self.peers, self.channels, self.accepted, self.originals = {}, {}, {}, []



if __name__ == '__main__':
    unittest.main()
