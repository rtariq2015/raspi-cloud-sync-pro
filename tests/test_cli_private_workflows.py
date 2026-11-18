"""Private operator CLI boundaries with synthetic state and real inherited pipes."""

import errno
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import accounts
from raspi_cloud_sync_pro import batch_transfer as transfer
from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_setup as setup
from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.oauth import OwnerProof
from raspi_cloud_sync_pro.staging import ReadyBatch
from raspi_cloud_sync_pro.web_ingest import stage_upload

ORIGIN = 'https://private-operator.example'
REQUEST = 'a' * 32
GENERATION = 'b' * 32
FOLDER = 'Private Operator Folder'
FOLDER_ID = 'PRIVATE_operator_folder_123'
PRIVATE = 'PRIVATE-operator-client-token-password-context'


class PrivateWorkflowCliDispatchTests(unittest.TestCase):
    @staticmethod
    def invoke(arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def operations(self):
        source, staging = Path('/private/operator-dashboard'), Path('/private/operator-staging')
        summary = {'schema_version': 1, 'current_worker_state_checked': False, 'account_verified': False}
        transferred = {'schema_version': 1, 'transfer_state': 'imported', 'already_present': False,
                       'uploads_enabled': False, 'automatic_deletion': False}
        return [
            ('raspi_cloud_sync_pro.google_control.google_control_status',
             ['google-control-status', '--state', str(source), '--public-origin', ORIGIN],
             (source, ORIGIN), {}, {**summary, 'control_state': 'not_requested', 'admission_authorized': False}),
            ('raspi_cloud_sync_pro.google_control.export_requested_google_control',
             ['google-control-export', '--state', str(source), '--public-origin', ORIGIN,
              '--request-id', REQUEST, '--output-fd', '3'],
             (source, ORIGIN, REQUEST, 3), {}, {**summary, 'control_exported': True}),
            ('raspi_cloud_sync_pro.google_control.record_google_control_receipt',
             ['google-control-record', '--state', str(source), '--public-origin', ORIGIN,
              '--request-id', REQUEST, '--receipt-fd', '3'],
             (source, ORIGIN, REQUEST, 3), {}, {**summary, 'control_state': 'test_verified', 'admission_authorized': False}),
            ('raspi_cloud_sync_pro.google_control.export_google_control_admission',
             ['google-control-admission', '--state', str(source), '--public-origin', ORIGIN, '--output-fd', '3'],
             (source, ORIGIN, 3), {}, {**summary, 'admission_exported': True}),
            ('raspi_cloud_sync_pro.batch_transfer.export_web_batch',
             ['web-batch-export', '--state', str(source / 'web-inbox'), '--batch-id', REQUEST,
              '--root-fd', '3', '--output-fd', '4', '--expected-manifest-sha256', 'c' * 64],
             (source / 'web-inbox', REQUEST, 3, 4, 'c' * 64), {'timeout': 300.0},
             {**transferred, 'transfer_state': 'exported'}),
            ('raspi_cloud_sync_pro.batch_transfer.import_web_batch',
             ['web-batch-import', '--state', str(staging), '--batch-id', REQUEST,
              '--root-fd', '3', '--input-fd', '4', '--expected-manifest-sha256', 'c' * 64, '--timeout', '12.5'],
             (staging, REQUEST, 3, 4, 'c' * 64), {'timeout': 12.5}, transferred),
        ]

    def test_status_handoff_record_admission_and_batch_commands_dispatch_exact_args(self):
        for target, arguments, expected, keywords, report in self.operations():
            with self.subTest(command=arguments[0]), mock.patch(target, return_value=report) as operation:
                code, output, errors = self.invoke(arguments)
            operation.assert_called_once_with(*expected, **keywords)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output), report)
            self.assertEqual(errors, '')

    def test_control_status_invalid_or_unavailable_returns_safe_json_exit_two(self):
        for state in ('invalid', 'unavailable'):
            report = {'control_state': state, 'admission_authorized': False,
                      'current_worker_state_checked': False, 'account_verified': False}
            with mock.patch.object(control, 'google_control_status', return_value=report):
                code, output, errors = self.invoke(['google-control-status', '--state', '/private/state',
                                                  '--public-origin', ORIGIN])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output), report)
            self.assertEqual(errors, '')

    def test_operator_exceptions_never_expose_ids_paths_or_private_error_context(self):
        for target, arguments, _, _, _ in self.operations():
            for error in (BackupError, OSError, ValueError, TypeError):
                with self.subTest(command=arguments[0], error=error.__name__), \
                        mock.patch(target, side_effect=error(PRIVATE)):
                    code, output, errors = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(output, '')
                expected = ('Private Google control operation unavailable; no private details were printed.\n'
                            if arguments[0].startswith('google-control-') else
                            'Private batch transfer unavailable; no cloud operation was started '
                            'and no private details were printed.\n')
                self.assertEqual(errors, expected)
                for private in (PRIVATE, ORIGIN, REQUEST, '/private/', 'Traceback'):
                    self.assertNotIn(private, errors)

    def test_cli_never_mints_owner_proof_or_exposes_request_test_enable_disable_commands(self):
        with mock.patch.object(control, 'request_google_control', side_effect=AssertionError('No owner minting')) as request, \
                mock.patch('raspi_cloud_sync_pro.oauth.OwnerProof', side_effect=AssertionError('No proof minting')):
            for name in ('google-control-request', 'google-control-test', 'google-control-enable', 'google-control-disable'):
                with self.subTest(command=name), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        main([name])
                    self.assertEqual(error.exception.code, 2)
            request.assert_not_called()

    def test_missing_operator_arguments_and_mutation_options_fail_before_dispatch(self):
        for target, arguments, _, _, _ in self.operations():
            for option in ('--action', '--enable', '--rclone', '--delete', '--owner', '--session'):
                with self.subTest(command=arguments[0], option=option), mock.patch(target) as operation, \
                        redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        main(arguments + [option, PRIVATE])
                    self.assertEqual(error.exception.code, 2)
                    operation.assert_not_called()
            for index in range(1, len(arguments), 2):
                if arguments[index] == '--timeout':
                    continue
                changed = arguments[:index] + arguments[index + 2:]
                with self.subTest(command=arguments[0], missing=arguments[index]), mock.patch(target) as operation, \
                        redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        main(changed)
                    self.assertEqual(error.exception.code, 2)
                    operation.assert_not_called()


@unittest.skipIf(os.geteuid() == 0, 'Private workflows run as ordinary service identities')
class PrivateWorkflowCliFilesystemTests(unittest.TestCase):
    invoke = staticmethod(PrivateWorkflowCliDispatchTests.invoke)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'dashboard'
        self.state.mkdir(mode=0o700)
        self.web = self.state / 'web-inbox'
        self.web.mkdir(mode=0o700)
        self.staging = self.root / 'staging'
        self.staging.mkdir(mode=0o700)
        self.now = time.time()
        self.client = {'client_id': 'operator.apps.googleusercontent.com', 'client_secret': PRIVATE}
        self.write('auth.json', {'secret': 'x' * 64, 'users': {'owner': {'role': 'owner', 'hash': PRIVATE}}})
        self.write('oauth-clients.json', {'schema_version': 1, 'public_origin': ORIGIN,
                                        'providers': {'google_drive': self.client}})
        self.write('oauth-google_drive.json', {
            'schema_version': 1, 'provider': 'google_drive',
            'client_id_sha256': hashlib.sha256(self.client['client_id'].encode()).hexdigest(),
            'received_at': self.now - 3600, 'expires_at': self.now + 3600,
            'token_type': 'Bearer', 'access_token': PRIVATE + '-access', 'refresh_token': PRIVATE + '-refresh',
            'scopes': [destination.GOOGLE_SCOPE]})
        self.proof = OwnerProof('owner', 'PRIVATE-live-session', hashlib.sha256(PRIVATE.encode()).hexdigest())

    def write(self, name, value):
        path = self.state / name
        path.write_bytes(destination._canonical(value))
        path.chmod(0o600)

    @contextmanager
    def guard(self, proof):
        with accounts._auth_lock(self.state):
            user = accounts.read_auth(self.state)['users'][proof.username]
            if (proof != self.proof or user['role'] != 'owner'
                    or hashlib.sha256(user['hash'].encode()).hexdigest() != proof.credential_tag):
                raise BackupError('owner_revoked')
            yield

    @staticmethod
    def anchor(path):
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def input_fd(self, value):
        raw = destination._canonical(value)
        read, write = os.pipe()
        self.assertLess(len(raw) + 4, 8192)
        os.write(write, len(raw).to_bytes(4, 'big') + raw)
        os.close(write)
        return read

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def assert_safe(self, output):
        for private in (PRIVATE, ORIGIN, FOLDER, FOLDER_ID, GENERATION, str(self.root)):
            self.assertNotIn(private, output)
        directory = self.state / control.WORKFLOW_DIRECTORY
        if directory.exists():
            for path in directory.glob('*.request.json'):
                record = json.loads(path.read_bytes())
                self.assertNotIn(record['request_id'], output)
                if record['preparation']:
                    self.assertNotIn(record['preparation']['authorization_sha256'], output)
                    self.assertNotIn(record['preparation']['rclone_config_sha256'], output)

    def owner_test_intent(self):
        setup.request_google_setup(self.state, ORIGIN, FOLDER, self.proof, self.guard, now=self.now - 30)
        workflow = self.state / setup.WORKFLOW_DIRECTORY
        request_id = json.loads((workflow / 'current.json').read_bytes())['request_id']
        record = json.loads((workflow / (request_id + '.request.json')).read_bytes())
        with destination._private_directory(self.state) as root:
            _, _, config_digest = setup._authorization(root, ORIGIN)
        receipt = {'schema_version': 1, 'provider': 'google_drive', 'request_id': request_id,
                   'authorization_sha256': record['authorization_sha256'], 'rclone_config_sha256': config_digest,
                   'folder_name': FOLDER, 'configuration_matches': True, 'prepared_at': self.now - 29,
                   'enabled': False, 'test_required': True, 'account_verified': False,
                   'folder_exists_verified': False, 'automatic_deletion': False}
        setup.record_google_setup_receipt(self.state, ORIGIN, request_id, self.input_fd(receipt), now=self.now - 28)
        control.request_google_control(self.state, ORIGIN, 'test', self.proof, self.guard, now=self.now - 20)
        return self.control_record()

    def control_record(self):
        workflow = self.state / control.WORKFLOW_DIRECTORY
        request_id = json.loads((workflow / 'current.json').read_bytes())['request_id']
        return json.loads((workflow / (request_id + '.request.json')).read_bytes())

    def control_receipt(self, record):
        result = None
        if record['action'] == 'test':
            fixture = control.google_test_fixture(record['request_id'])
            result = {'generation_id': GENERATION, 'folder_id': FOLDER_ID,
                      'operational_config_sha256': 'c' * 64,
                      'operational_settings_sha256': control.google_operational_settings_sha256({
                          'remote_name': destination.REMOTE_NAME, 'type': 'drive', 'scope': 'drive.file',
                          **self.client, 'root_folder_id': FOLDER_ID}),
                      'fixture_sha256': hashlib.sha256(fixture).hexdigest(), 'fixture_size': len(fixture),
                      'fixture_uploaded': True, 'fixture_readback_verified': True, 'token_refresh_verified': True}
        return {'schema_version': 1, 'provider': 'google_drive', 'request_id': record['request_id'],
                'action': record['action'], 'preparation': record['preparation'], 'test': record['test'],
                'completed_at': record['requested_at'] + 1, 'outcome': 'verified' if result else 'applied',
                'result': result, 'enabled': record['action'] == 'enable', 'automatic_deletion': False}

    def control_cli(self, command, *extra):
        return self.invoke([command, '--state', str(self.state), '--public-origin', ORIGIN, *extra])

    def read_private_control(self, command, *, request_id=None):
        read, write = os.pipe()
        try:
            extra = ['--output-fd', str(write)]
            if request_id is not None:
                extra += ['--request-id', request_id]
            report = self.control_cli(command, *extra)
            self.assert_closed(write)
            metadata = destination._read_frame(read, time.monotonic() + 1)
            return report, metadata
        finally:
            os.close(read)
            try:
                os.close(write)
            except OSError:
                pass

    def describe_cli(self, batch_id):
        read, write = os.pipe()
        root = self.anchor(self.web)
        try:
            code, output, errors = self.invoke(['web-batch-describe', '--state', str(self.web), '--batch-id', batch_id,
                '--root-fd', str(root), '--output-fd', str(write), '--timeout', '3'])
            self.assert_closed(root)
            self.assert_closed(write)
            metadata = destination._read_frame(read, time.monotonic() + 1)
            self.assertEqual(code, 0)
            self.assertEqual(errors, '')
            self.assert_safe(output)
            self.assertNotIn(batch_id, output)
            self.assertNotIn(metadata['manifest_sha256'], output)
            self.assertFalse(json.loads(output)['uploads_enabled'])
            return metadata
        finally:
            os.close(read)
            for fd in (root, write):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_actual_status_and_admission_default_to_no_authority_without_minting_intent(self):
        before = {path.name: path.read_bytes() for path in self.state.iterdir() if path.is_file()}
        code, output, errors = self.control_cli('google-control-status')
        self.assertEqual(code, 0)
        self.assertEqual(errors, '')
        self.assertEqual(json.loads(output)['control_state'], 'not_requested')
        self.assertFalse(json.loads(output)['admission_authorized'])
        (code, output, errors), wire = self.read_private_control('google-control-admission')
        self.assertEqual(code, 0)
        self.assertFalse(wire['admission_authorized'])
        self.assertIsNone(wire['control_request_id'])
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())
        self.assert_safe(output + errors)
        for name, value in before.items():
            self.assertEqual((self.state / name).read_bytes(), value)

    def test_actual_pending_export_and_test_receipt_are_private_historical_and_disabled(self):
        record = self.owner_test_intent()
        (code, output, errors), wire = self.read_private_control('google-control-export', request_id=record['request_id'])
        self.assertEqual(code, 0)
        self.assertEqual(errors, '')
        self.assertEqual(wire['request_id'], record['request_id'])
        self.assertEqual(wire['action'], 'test')
        self.assert_safe(output)
        receipt_fd = self.input_fd(self.control_receipt(record))
        with mock.patch('subprocess.run', side_effect=AssertionError('No process')), \
                mock.patch('subprocess.Popen', side_effect=AssertionError('No process')), \
                mock.patch('socket.socket', side_effect=AssertionError('No network')):
            code, output, errors = self.control_cli('google-control-record', '--request-id', record['request_id'],
                                                   '--receipt-fd', str(receipt_fd))
        self.assert_closed(receipt_fd)
        self.assertEqual(code, 0)
        status = json.loads(output)
        self.assertEqual(status['control_state'], 'test_verified')
        self.assertTrue(status['historical'])
        self.assertFalse(status['admission_authorized'])
        self.assertFalse(status['current_worker_state_checked'])
        self.assertFalse(status['account_verified'])
        self.assert_safe(output + errors)

    def test_record_and_admission_only_follow_a_separately_guarded_enable_intent(self):
        record = self.owner_test_intent()
        control.record_google_control_receipt(self.state, ORIGIN, record['request_id'],
                                              self.input_fd(self.control_receipt(record)))
        control.request_google_control(self.state, ORIGIN, 'enable', self.proof, self.guard, now=self.now - 18)
        record = self.control_record()
        fd = self.input_fd(self.control_receipt(record))
        code, output, errors = self.control_cli('google-control-record', '--request-id', record['request_id'], '--receipt-fd', str(fd))
        self.assert_closed(fd)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)['admission_authorized'])
        self.assertFalse(json.loads(output)['current_worker_state_checked'])
        self.assert_safe(output + errors)
        (code, output, errors), wire = self.read_private_control('google-control-admission')
        self.assertEqual(code, 0)
        self.assertTrue(wire['admission_authorized'])
        self.assertTrue(wire['single_admission'])
        self.assertEqual(wire['test']['generation_id'], GENERATION)
        self.assertEqual(wire['test']['folder_id'], FOLDER_ID)
        self.assert_safe(output + errors)
        control.request_google_control(self.state, ORIGIN, 'disable', self.proof, self.guard, now=self.now - 16)
        (code, output, errors), wire = self.read_private_control('google-control-admission')
        self.assertEqual(code, 0)
        self.assertFalse(wire['admission_authorized'])
        self.assert_safe(output + errors)

    def test_describe_private_metadata_drives_real_cli_export_and_api_import(self):
        source = stage_upload(io.BytesIO(b'private completed bytes' * 8192), 'report.txt', self.web)
        batch_id = source.root.stem
        original = {path: (path.stat().st_ino, path.read_bytes()) for path in source.root.rglob('*') if path.is_file()}
        metadata = self.describe_cli(batch_id)
        root_source, root_target = self.anchor(self.web), self.anchor(self.staging)
        read, write = os.pipe()
        calls = []
        def export():
            calls.append(self.invoke(['web-batch-export', '--state', str(self.web), '--batch-id', batch_id,
                '--root-fd', str(root_source), '--output-fd', str(write),
                '--expected-manifest-sha256', metadata['manifest_sha256'], '--timeout', '3']))
        producer = threading.Thread(target=export)
        producer.start()
        try:
            report = transfer.import_web_batch(self.staging, batch_id, root_target, read,
                                               metadata['manifest_sha256'], timeout=3)
        finally:
            producer.join(5)
        self.assertFalse(producer.is_alive())
        self.assertFalse(report['uploads_enabled'])
        self.assertEqual(calls[0][0], 0)
        self.assert_safe(calls[0][1] + calls[0][2])
        for fd in (root_source, root_target, read, write):
            self.assert_closed(fd)
        ReadyBatch.load(self.staging / (batch_id + '.ready'))
        for path, before in original.items():
            self.assertEqual((path.stat().st_ino, path.read_bytes()), before)

    def test_real_cli_import_verifies_bounded_stream_and_reuses_without_replacement(self):
        source = stage_upload(io.BytesIO(b'private source remains intact' * 4096), 'data.bin', self.web)
        batch_id = source.root.stem
        expected = self.describe_cli(batch_id)['manifest_sha256']
        for already in (False, True):
            source_root, target_root = self.anchor(self.web), self.anchor(self.staging)
            read, write = os.pipe()
            failures = []
            def export():
                try:transfer.export_web_batch(self.web, batch_id, source_root, write, expected, timeout=3)
                except BaseException as error:failures.append(error)
            producer = threading.Thread(target=export)
            producer.start()
            try:
                code, output, errors = self.invoke(['web-batch-import', '--state', str(self.staging), '--batch-id', batch_id,
                    '--root-fd', str(target_root), '--input-fd', str(read), '--expected-manifest-sha256', expected, '--timeout', '3'])
            finally:producer.join(5)
            self.assertFalse(producer.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)['already_present'], already)
            self.assertFalse(json.loads(output)['uploads_enabled'])
            self.assert_safe(output + errors)
            self.assertNotIn(batch_id, output)
            self.assertNotIn(expected, output)
            for fd in (source_root, target_root, read, write):
                self.assert_closed(fd)
        ReadyBatch.load(self.staging / (batch_id + '.ready'))

    def test_valid_private_control_endpoints_close_on_failure_without_payload_or_traceback(self):
        for command in ('google-control-export', 'google-control-admission'):
            read, write = os.pipe()
            args = ['--output-fd', str(write)]
            if command == 'google-control-export':args += ['--request-id', REQUEST]
            try:
                code, output, errors = self.invoke([command, '--state', str(self.root / 'missing'),
                    '--public-origin', ORIGIN, *args])
                self.assertEqual(code, 2)
                self.assertEqual(output, '')
                self.assert_safe(errors)
                self.assert_closed(write)
                self.assertEqual(os.read(read, 1), b'')
            finally:
                os.close(read)

    def test_invalid_receipt_payload_stays_private_and_consumes_accepted_endpoint(self):
        record = self.owner_test_intent()
        fd = self.input_fd({'PRIVATE-invalid-receipt': PRIVATE})
        code, output, errors = self.control_cli('google-control-record', '--request-id', record['request_id'],
                                               '--receipt-fd', str(fd))
        self.assertEqual(code, 2)
        self.assertEqual(output, '')
        self.assert_safe(errors)
        self.assertNotIn('Traceback', errors)
        self.assert_closed(fd)
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY / (record['request_id'] + '.receipt.json')).exists())

    def test_describe_write_failure_closes_both_accepted_descriptors_without_metadata_stdout(self):
        source = stage_upload(io.BytesIO(b'original stays private'), 'data.bin', self.web)
        batch_id = source.root.stem
        read, write = os.pipe()
        os.close(read)
        root = self.anchor(self.web)
        code, output, errors = self.invoke(['web-batch-describe', '--state', str(self.web), '--batch-id', batch_id,
            '--root-fd', str(root), '--output-fd', str(write), '--timeout', '3'])
        self.assertEqual(code, 2)
        self.assertEqual(output, '')
        self.assert_safe(errors)
        self.assertNotIn(batch_id, errors)
        self.assert_closed(root)
        self.assert_closed(write)
        self.assertEqual((source.root / 'files/data.bin').read_bytes(), b'original stays private')

    def test_describe_consumes_accepted_output_on_bad_anchor_but_refuses_stdio_before_anchor_acceptance(self):
        read, write = os.pipe()
        try:
            code, output, errors = self.invoke(['web-batch-describe', '--state', str(self.web), '--batch-id', REQUEST,
                '--root-fd', '-3', '--output-fd', str(write)])
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assert_safe(errors)
            self.assert_closed(write)
            self.assertEqual(os.read(read, 1), b'')
        finally:os.close(read)
        root = self.anchor(self.web)
        try:
            code, output, errors = self.invoke(['web-batch-describe', '--state', str(self.web), '--batch-id', REQUEST,
                '--root-fd', str(root), '--output-fd', '1'])
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assert_safe(errors)
            os.fstat(root)  # Output was invalid; the anchor API was never entered.
            os.fstat(1)
        finally:os.close(root)

    def test_wrong_manifest_binding_fails_import_with_consumed_fds_and_no_ready_target(self):
        source = stage_upload(io.BytesIO(b'original retained'), 'data.bin', self.web)
        batch_id = source.root.stem
        manifest = json.loads((source.root / 'manifest.json').read_bytes())
        digest = hashlib.sha256(destination._canonical(manifest)).hexdigest()
        header = transfer._header(batch_id, manifest, digest)
        fd = self.input_fd(header)
        root = self.anchor(self.staging)
        code, output, errors = self.invoke(['web-batch-import', '--state', str(self.staging), '--batch-id', batch_id,
            '--root-fd', str(root), '--input-fd', str(fd), '--expected-manifest-sha256', 'f' * 64])
        self.assertEqual(code, 2)
        self.assertEqual(output, '')
        self.assert_safe(errors)
        self.assert_closed(root)
        self.assert_closed(fd)
        self.assertEqual(list(self.staging.iterdir()), [])
        self.assertEqual((source.root / 'files/data.bin').read_bytes(), b'original retained')


if __name__ == '__main__':
    unittest.main()
