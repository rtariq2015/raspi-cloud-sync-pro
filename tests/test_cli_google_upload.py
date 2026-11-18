"""Private upload CLI channels; success uses a stub and never releases network."""
import errno
import fcntl
import io
import json
import os
import socket
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import cli
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_upload as upload

BATCH, CONTROL, GENERATION = 'a' * 32, 'b' * 32, 'c' * 32
MANIFEST = 'd' * 64
PRIVATE = 'PRIVATE-UPLOAD-client-token-folder-identifier-exception'
ERROR = 'Private Google upload unavailable; no private details were printed.\n'


def encode(value):
    raw = destination._canonical(value)
    return len(raw).to_bytes(4, 'big') + raw


def admission():
    return {'schema_version': 1, 'provider': 'google_drive', 'admission_authorized': True,
            'control_request_id': CONTROL, 'preparation': {'private': PRIVATE},
            'test': {'generation_id': GENERATION, 'private': PRIVATE},
            'checked_at': time.time(), 'single_admission': True}


def receipt(state='verified', **changes):
    counts = {'verified': (1, 1, 0, 0), 'historical': (0, 2, 0, 0),
              'pending': (0, 1, 1, 0), 'failed': (0, 1, 0, 1)}[state]
    source = admission()
    return {'schema_version': 1, 'provider': 'google_drive', 'batch_state': state,
            'batch_id': BATCH, 'manifest_sha256': MANIFEST, 'control_request_id': CONTROL,
            'preparation': source['preparation'], 'test': source['test'],
            'destination_id': PRIVATE, 'completed_at': time.time(), 'automatic_deletion': False,
            **dict(zip(('files_verified_now', 'files_previously_verified', 'files_pending', 'files_failed'), counts)), **changes}


class GoogleUploadCliTests(unittest.TestCase):
    def setUp(self):
        self.descriptors = []
        self.gates = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for fd in self.descriptors:
            try:
                os.close(fd)
            except OSError:
                pass

    def own(self, fd):
        self.descriptors.append(fd)
        return fd

    def pipe(self):
        return tuple(self.own(fd) for fd in os.pipe())

    def channels(self, raw=None, *, eof=True, sockets=False):
        self.gates = []
        if sockets:
            sender, receiver = socket.socketpair()
            incoming, outgoing = self.own(receiver.detach()), self.own(sender.detach())
        else:
            incoming, outgoing = self.pipe()
        if raw is None:
            raw = encode(admission())
        if raw:
            os.write(outgoing, raw)
        if eof:
            if sockets:
                with socket.socket(fileno=os.dup(outgoing)) as peer:
                    peer.shutdown(socket.SHUT_WR)
            else:
                os.close(outgoing)
                self.descriptors.remove(outgoing)
        parent, child = socket.socketpair()
        gate, peer = self.own(child.detach()), self.own(parent.detach())
        os.write(peer, encode({'stub_release': PRIVATE}))
        with socket.socket(fileno=os.dup(peer)) as channel:
            channel.shutdown(socket.SHUT_WR)
        reader, writer = self.pipe()
        return {'admission': incoming, 'admission_writer': outgoing, 'gate': gate, 'gate_peer': peer,
                'receipt': writer, 'receipt_reader': reader}

    @staticmethod
    def arguments(channels, **changes):
        values = {'prepared-state': '/private/prepared', 'operational-state': '/private/operational',
                  'staging': '/private/staging', 'batch-id': BATCH, 'manifest-sha256': MANIFEST,
                  'admission-fd': channels['admission'], 'root-gate-fd': channels['gate'],
                  'receipt-fd': channels['receipt'], 'rclone': '/protected/rclone', 'rclone-sha256': 'e' * 64, **changes}
        return ['google-upload', *(item for name, value in values.items() if value is not None
                                   for item in ('--' + name, str(value)))]

    @staticmethod
    def invoke(args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(args)
        return code, output.getvalue(), errors.getvalue()

    def closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def consumed(self, channels):
        for name in ('admission', 'gate', 'receipt'):
            self.closed(channels[name])
        for fd in self.gates:
            self.closed(fd)

    def read_receipt(self, channels):
        with destination._channel(channels['receipt_reader'], writing=False) as fd:
            result = destination._read_frame(fd, destination._deadline(1))
        self.descriptors.remove(channels['receipt_reader'])
        return result

    def worker(self, prepared, operational, staging, batch_id, digest, value, **options):
        self.assertEqual((prepared, operational, staging),
                         (Path('/private/prepared'), Path('/private/operational'), Path('/private/staging')))
        self.assertEqual((batch_id, digest, value['control_request_id']), (BATCH, MANIFEST, CONTROL))
        self.assertEqual(options['executable'], Path('/protected/rclone'))
        self.assertEqual(options['executable_sha256'], 'e' * 64)
        self.assertEqual(options['timeout'], 300.0)
        self.gates.append(options['root_gate_fd'])
        with destination._channel(options['root_gate_fd'], writing=False) as channel:
            self.assertEqual(destination._read_frame(channel, destination._deadline(1)), {'stub_release': PRIVATE})
        return upload.GoogleUploadOutcome(receipt())

    def test_success_uses_exact_private_frames_and_only_fixed_public_counts(self):
        for sockets in (False, True):
            with self.subTest(sockets=sockets):
                channels = self.channels(sockets=sockets)
                with mock.patch.object(upload, 'run_google_batch_upload', side_effect=self.worker) as worker, \
                        mock.patch('subprocess.Popen', side_effect=AssertionError('No process')), \
                        mock.patch.object(upload.control, 'request_google_control', side_effect=AssertionError('No intent')):
                    code, output, errors = self.invoke(self.arguments(channels))
                self.assertEqual((code, errors), (0, ''))
                self.assertNotEqual(worker.call_args.kwargs['root_gate_fd'], channels['gate'])
                expected = upload.GoogleUploadOutcome(receipt()).projection()
                self.assertEqual(json.loads(output), expected)
                for private in (PRIVATE, BATCH, CONTROL, GENERATION, MANIFEST, '/private/', '/protected/', 'destination_id', 'Traceback'):
                    self.assertNotIn(private, output)
                self.assertEqual(self.read_receipt(channels)['destination_id'], PRIVATE)
                self.consumed(channels)

    def test_verified_historical_exit_zero_pending_failed_exit_two(self):
        for state, expected in (('verified', 0), ('historical', 0), ('pending', 2), ('failed', 2)):
            channels = self.channels()
            with mock.patch.object(upload, 'run_google_batch_upload', return_value=upload.GoogleUploadOutcome(receipt(state))):
                code, output, errors = self.invoke(self.arguments(channels))
            self.assertEqual((code, json.loads(output)['batch_state'], errors), (expected, state, ''))
            self.assertEqual(self.read_receipt(channels)['batch_state'], state)
            self.consumed(channels)

    def test_absent_root_gate_refuses_before_admission_read_state_or_worker(self):
        channels = self.channels(raw=b'', eof=False)
        with mock.patch.object(upload, 'run_google_batch_upload') as worker, \
                mock.patch.object(destination, '_read_frame', side_effect=AssertionError('No admission read')), \
                mock.patch.object(destination, '_private_directory', side_effect=AssertionError('No state')):
            self.assertEqual(self.invoke(self.arguments(channels, **{'root-gate-fd': None})), (2, '', ERROR))
        worker.assert_not_called()
        self.closed(channels['admission'])
        self.closed(channels['receipt'])
        os.fstat(channels['gate'])

    def test_output_regular_fd_is_unaccepted_and_refuses_before_worker_or_read(self):
        channels = self.channels()
        with tempfile.TemporaryFile() as file, mock.patch.object(upload, 'run_google_batch_upload') as worker, \
                mock.patch.object(destination, '_read_frame', side_effect=AssertionError('No input read')):
            self.assertEqual(self.invoke(self.arguments(channels, **{'receipt-fd': file.fileno()})), (2, '', ERROR))
            worker.assert_not_called()
            os.fstat(file.fileno())
            self.assertEqual(file.tell(), 0)
        self.closed(channels['admission'])
        self.closed(channels['gate'])

    def test_input_and_gate_regular_or_wrong_direction_fds_are_not_accepted(self):
        for key in ('admission-fd', 'root-gate-fd'):
            for regular in (False, True):
                with self.subTest(key=key, regular=regular), tempfile.TemporaryFile() as file:
                    channels = self.channels()
                    invalid = file.fileno() if regular else channels['receipt']
                    with mock.patch.object(upload, 'run_google_batch_upload') as worker:
                        self.assertEqual(self.invoke(self.arguments(channels, **{key: invalid})), (2, '', ERROR))
                    worker.assert_not_called()
                    os.fstat(file.fileno())

    def test_duplicate_descriptors_and_distinct_aliases_fail_before_worker(self):
        for first, second in (('admission', 'gate'), ('receipt', 'admission'), ('receipt', 'gate')):
            for duplicate in (False, True):
                channels = self.channels(sockets=True)
                fd = channels[first]
                alias = self.own(os.dup(fd)) if duplicate else fd
                original_flags = fcntl.fcntl(alias, fcntl.F_GETFL)
                with self.subTest(first=first, second=second, alias=duplicate), \
                        mock.patch.object(upload, 'run_google_batch_upload') as worker:
                    self.assertEqual(self.invoke(self.arguments(channels, **{second + '-fd' if second != 'gate' else 'root-gate-fd': alias})),
                                     (2, '', ERROR))
                worker.assert_not_called()
                self.closed(fd)
                if duplicate and first == 'receipt':
                    # A write-only receipt alias is invalid as an input, so it
                    # stays caller-owned and the accepted partner restores flags.
                    self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), original_flags)
                else:
                    self.closed(alias)

    def test_stdio_and_its_alias_are_rejected_without_closing_unaccepted_fd(self):
        for standard in (0, 1, 2):
            try:
                alias = self.own(os.dup(standard))
            except OSError:
                continue
            for invalid in (standard, alias):
                channels = self.channels()
                with self.subTest(standard=standard, alias=invalid != standard), mock.patch.object(upload, 'run_google_batch_upload') as worker:
                    self.assertEqual(self.invoke(self.arguments(channels, **{'receipt-fd': invalid})), (2, '', ERROR))
                worker.assert_not_called()
                os.fstat(standard)
                os.fstat(alias)
                self.closed(channels['admission'])
                self.closed(channels['gate'])

    def test_malformed_oversize_duplicate_json_trailing_and_missing_eof_fail_before_worker(self):
        raw_cases = (b'\0\0\0\0', (65537).to_bytes(4, 'big'), b'\0\0\0\x02{}x',
                     len(b'{"a":1,"a":2}').to_bytes(4, 'big') + b'{"a":1,"a":2}', encode([]),
                     encode({'large': 'x' * 17000}))
        for raw in raw_cases:
            channels = self.channels(raw=raw, sockets=len(raw) > 8192)
            with self.subTest(size=len(raw)), mock.patch.object(upload, 'run_google_batch_upload') as worker:
                self.assertEqual(self.invoke(self.arguments(channels)), (2, '', ERROR))
            worker.assert_not_called()
            self.consumed(channels)
        channels = self.channels(raw=encode(admission()), eof=False)
        with mock.patch.object(destination, '_deadline', side_effect=lambda timeout: time.monotonic() + .01), \
                mock.patch.object(upload, 'run_google_batch_upload') as worker:
            self.assertEqual(self.invoke(self.arguments(channels)), (2, '', ERROR))
        worker.assert_not_called()
        self.consumed(channels)

    def test_private_arguments_are_bounded_before_worker(self):
        for changes in ({'batch-id': '../PRIVATE'}, {'manifest-sha256': 'x' * 64}, {'rclone-sha256': PRIVATE},
                        {'prepared-state': 'relative'}, {'staging': '/private/../unsafe'}, {'rclone': 'rclone'},
                        {'timeout': 'nan'}, {'timeout': 'inf'}, {'timeout': '0'}, {'timeout': '301'}):
            channels = self.channels()
            with self.subTest(changes=changes), mock.patch.object(upload, 'run_google_batch_upload') as worker:
                self.assertEqual(self.invoke(self.arguments(channels, **changes)), (2, '', ERROR))
            worker.assert_not_called()
            self.consumed(channels)

    def test_worker_raw_failures_and_bogus_public_results_never_escape(self):
        outcomes = (RuntimeError(PRIVATE), ValueError(PRIVATE), MemoryError(PRIVATE), None,
                    upload.GoogleUploadOutcome(receipt(automatic_deletion=True)),
                    upload.GoogleUploadOutcome(receipt(batch_id='9' * 32)),
                    upload.GoogleUploadOutcome(receipt(files_verified_now=True)),
                    upload.GoogleUploadOutcome(receipt(files_pending=1)),
                    upload.GoogleUploadOutcome(receipt(control_request_id='9' * 32)))
        for outcome in outcomes:
            channels = self.channels()
            with self.subTest(kind=type(outcome).__name__), mock.patch.object(upload, 'run_google_batch_upload') as worker:
                if isinstance(outcome, Exception):
                    worker.side_effect = outcome
                else:
                    worker.return_value = outcome
                self.assertEqual(self.invoke(self.arguments(channels)), (2, '', ERROR))
            self.consumed(channels)

    def test_unconsumed_gate_duplicate_is_closed_and_reused_number_is_not_closed(self):
        channels = self.channels()
        def worker(*args, **options):
            self.gates.append(options['root_gate_fd'])
            return upload.GoogleUploadOutcome(receipt())
        with mock.patch.object(upload, 'run_google_batch_upload', side_effect=worker):
            self.assertEqual(self.invoke(self.arguments(channels))[0], 0)
        self.consumed(channels)
        channels = self.channels()
        reused = []
        def reuse(*args, **options):
            fd = options['root_gate_fd']
            os.close(fd)
            read, write = os.pipe()
            self.own(read); self.own(write)
            if read != fd:
                os.dup2(read, fd)
                self.own(fd)
            reused.append(fd)
            return upload.GoogleUploadOutcome(receipt())
        with mock.patch.object(upload, 'run_google_batch_upload', side_effect=reuse):
            self.assertEqual(self.invoke(self.arguments(channels))[0], 0)
        os.fstat(reused[0])

    def test_actual_upload_api_denies_ordinary_peer_before_state_or_runner(self):
        if os.geteuid() == 0:
            self.skipTest('Requires actual ordinary peer')
        channels = self.channels()
        with mock.patch.object(upload.operational, '_ordinary_identity', side_effect=AssertionError('No identity/state')), \
                mock.patch.object(upload.tester, '_Runner', side_effect=AssertionError('No process')):
            self.assertEqual(self.invoke(self.arguments(channels)), (2, '', ERROR))
        self.consumed(channels)

    def test_actual_broken_receipt_pipe_cannot_report_success(self):
        channels = self.channels()
        os.close(channels['receipt_reader'])
        self.descriptors.remove(channels['receipt_reader'])
        with mock.patch.object(upload, 'run_google_batch_upload', return_value=upload.GoogleUploadOutcome(receipt())):
            self.assertEqual(self.invoke(self.arguments(channels)), (2, '', ERROR))
        self.consumed(channels)

    def test_parser_errors_and_help_never_echo_private_args_or_mint_intent(self):
        for arguments in ([], ['--admission-fd', PRIVATE], ['--prepared-state', PRIVATE, '--enable'], [PRIVATE]):
            with mock.patch.object(upload, 'run_google_batch_upload') as worker:
                self.assertEqual(self.invoke(['google-upload', *arguments]), (2, '', ERROR))
            worker.assert_not_called()
        with mock.patch.object(upload.control, 'request_google_control') as intent, mock.patch.object(upload, '_gate') as gate:
            code, output, errors = self.invoke(['google-upload', '--help'])
        self.assertEqual((code, errors), (0, ''))
        self.assertIn('--admission-fd', output)
        self.assertNotIn('--now', output)
        intent.assert_not_called()
        gate.assert_not_called()

    def test_sys_argv_dispatch_and_private_receipt_write_failure_are_sanitized(self):
        channels = self.channels()
        with mock.patch.object(cli.sys, 'argv', ['program', *self.arguments(channels)]), \
                mock.patch.object(upload, 'run_google_batch_upload', side_effect=self.worker):
            code, output, errors = self.invoke(None)
        self.assertEqual((code, errors), (0, ''))
        self.assertEqual(json.loads(output)['batch_state'], 'verified')
        self.consumed(channels)
        channels = self.channels()
        with mock.patch.object(upload, 'run_google_batch_upload', return_value=upload.GoogleUploadOutcome(receipt())), \
                mock.patch.object(destination, '_write_frame', side_effect=OSError(PRIVATE)):
            self.assertEqual(self.invoke(self.arguments(channels)), (2, '', ERROR))
        self.consumed(channels)


if __name__ == '__main__':
    unittest.main()
