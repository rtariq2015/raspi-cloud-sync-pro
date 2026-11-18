"""Real owner sessions/CSRF guard publish only disabled-by-default control intents."""

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import accounts
from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_setup as setup
from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.dashboard import create_app
from raspi_cloud_sync_pro.errors import BackupError

ORIGIN = 'https://control.example'
FOLDER = 'PRIVATE_Control_Folder'
FOLDER_ID = 'PRIVATE_control_folder_123'
GENERATION = 'b' * 32
CLIENT_SECRET = 'PRIVATE-control-client-secret'


@unittest.skipUnless(importlib.util.find_spec('flask'), 'optional dashboard dependencies not installed')
class DashboardGoogleControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        source = Path(cls.fixture.name)
        accounts.initialize_dashboard(source, password='test-owner-password')
        accounts.initialize_dashboard(source, username='viewer', role='viewer', password='test-viewer-password')
        cls.auth_bytes = (source / 'auth.json').read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        (self.state / 'auth.json').write_bytes(self.auth_bytes)
        (self.state / 'auth.json').chmod(0o600)
        self.client_settings = {'client_id': 'PRIVATE-control.apps.googleusercontent.com', 'client_secret': CLIENT_SECRET}
        self.write('oauth-clients.json', {'schema_version': 1, 'public_origin': ORIGIN,
                                        'providers': {'google_drive': self.client_settings}})
        self.authorization = {'schema_version': 1, 'provider': 'google_drive',
            'client_id_sha256': hashlib.sha256(self.client_settings['client_id'].encode()).hexdigest(),
            'received_at': time.time() - 60, 'expires_at': time.time() + 3540,
            'token_type': 'Bearer', 'access_token': 'PRIVATE-control-access',
            'refresh_token': 'PRIVATE-control-refresh', 'scopes': [destination.GOOGLE_SCOPE]}
        self.write('oauth-google_drive.json', self.authorization)
        self.app = self.make_app(True)
        self.client = self.app.test_client()

    def write(self, name, value):
        path = self.state / name
        path.write_bytes(destination._canonical(value))
        path.chmod(0o600)

    def make_app(self, enabled=False, *, setup_enabled=True):
        app = create_app(self.state, public_origin=ORIGIN, network_reader=lambda: {},
                         google_setup_requests=setup_enabled, enable_google_control_requests=enabled)
        app.testing = True
        return app

    def request(self, client, method, path, **kwargs):
        return getattr(client, method)(path, base_url=ORIGIN, **kwargs)

    def login(self, client=None, username='owner', password='test-owner-password'):
        client = client or self.client
        self.request(client, 'get', '/login')
        with client.session_transaction(base_url=ORIGIN) as session:
            csrf = session['csrf']
        response = self.request(client, 'post', '/login', data={'csrf': csrf, 'username': username, 'password': password})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction(base_url=ORIGIN) as session:
            return session['csrf']

    def act(self, csrf, action='test', *, client=None, data=None):
        return self.request(client or self.client, 'post', '/api/destinations/google_drive/control',
                            json={'action': action} if data is None else data, headers={'X-CSRF-Token': csrf})

    def google(self, client=None):
        response = self.request(client or self.client, 'get', '/api/destinations')
        self.assertEqual(response.status_code, 200)
        self.assert_private(response)
        return next(provider for provider in response.json['providers'] if provider['provider'] == 'google_drive')

    def current(self, workflow=control.WORKFLOW_DIRECTORY):
        root = self.state / workflow
        request_id = json.loads((root / 'current.json').read_bytes())['request_id']
        return json.loads((root / (request_id + '.request.json')).read_bytes())

    @staticmethod
    def input_fd(value):
        raw = destination._canonical(value)
        reader, writer = os.pipe()
        os.write(writer, len(raw).to_bytes(4, 'big') + raw)
        os.close(writer)
        return reader

    def prepare(self, csrf):
        response = self.request(self.client, 'post', '/api/destinations/google_drive/prepare',
                                json={'folder_name': FOLDER}, headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 202)
        request = self.current(setup.WORKFLOW_DIRECTORY)
        with destination._private_directory(self.state) as root:
            _, _, config_digest = setup._authorization(root, ORIGIN)
        receipt = {'schema_version': 1, 'provider': 'google_drive', 'request_id': request['request_id'],
            'authorization_sha256': request['authorization_sha256'], 'rclone_config_sha256': config_digest,
            'folder_name': FOLDER, 'configuration_matches': True, 'prepared_at': time.time(),
            'enabled': False, 'test_required': True, 'account_verified': False,
            'folder_exists_verified': False, 'automatic_deletion': False}
        setup.record_google_setup_receipt(self.state, ORIGIN, request['request_id'], self.input_fd(receipt))

    def record(self, *, verified=True):
        request = self.current()
        result = None
        if request['action'] == 'test':
            fixture = control.google_test_fixture(request['request_id'])
            result = {'generation_id': GENERATION, 'folder_id': FOLDER_ID,
                'operational_config_sha256': 'c' * 64,
                'operational_settings_sha256': control.google_operational_settings_sha256({
                    'remote_name': destination.REMOTE_NAME, 'type': 'drive', 'scope': 'drive.file',
                    **self.client_settings, 'root_folder_id': FOLDER_ID}),
                'fixture_sha256': hashlib.sha256(fixture).hexdigest(), 'fixture_size': len(fixture),
                'fixture_uploaded': verified, 'fixture_readback_verified': verified, 'token_refresh_verified': verified}
        receipt = {'schema_version': 1, 'provider': 'google_drive', 'request_id': request['request_id'],
            'action': request['action'], 'preparation': request['preparation'], 'test': request['test'],
            'completed_at': time.time(), 'outcome': ('verified' if verified else 'failed') if result else 'applied',
            'result': result, 'enabled': request['action'] == 'enable', 'automatic_deletion': False}
        return control.record_google_control_receipt(self.state, ORIGIN, request['request_id'], self.input_fd(receipt))

    def assert_private(self, response):
        text = response.get_data(as_text=True)
        for private in ('PRIVATE', FOLDER, FOLDER_ID, GENERATION, ORIGIN, str(self.state),
                        'request_id', 'generation_id', 'folder_id', 'authorization_sha256',
                        'rclone_config_sha256', 'operational_config_sha256', 'owner_credential_tag'):
            self.assertNotIn(private, text)

    def assert_status(self, response, expected, *, admitted=False):
        self.assertEqual(response.status_code, 202)
        self.assert_private(response)
        report = response.json['control']
        self.assertEqual(report['control_state'], expected)
        self.assertIs(report['admission_authorized'], admitted)
        self.assertIs(report['current_worker_state_checked'], False)
        self.assertIs(report['account_verified'], False)

    def test_default_gate_hides_owner_form_and_refuses_all_intents_before_api_call(self):
        app = create_app(self.state, public_origin=ORIGIN, network_reader=lambda: {}, google_setup_requests=True)
        client = app.test_client()
        csrf = self.login(client)
        self.assertNotIn(b'id="google-control-form"', self.request(client, 'get', '/').data)
        provider = self.google(client)
        self.assertFalse(provider['control_requests_available'])
        self.assertFalse(provider['control']['admission_authorized'])
        for action in ('test', 'enable', 'disable'):
            response = self.act(csrf, action, client=client)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json['error'], 'google_control_service_unavailable')
            self.assert_private(response)
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())

    def test_flag_is_explicit_boolean_and_independent_of_preparation_flag(self):
        for value in (1, 'yes', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.make_app(value)
        app = self.make_app(True, setup_enabled=False)
        client = app.test_client()
        csrf = self.login(client)
        provider = self.google(client)
        self.assertTrue(provider['control_requests_available'])
        self.assertFalse(provider['preparation_requests_available'])
        self.assert_status(self.act(csrf, 'disable', client=client), 'disable_requested')

    def test_viewer_and_anonymous_users_cannot_submit_or_see_owner_form(self):
        self.request(self.client, 'get', '/login')
        with self.client.session_transaction(base_url=ORIGIN) as session:
            csrf = session['csrf']
        self.assertEqual(self.act(csrf).status_code, 401)
        csrf = self.login(username='viewer', password='test-viewer-password')
        self.assertNotIn(b'id="google-control-form"', self.request(self.client, 'get', '/').data)
        for action in ('test', 'enable', 'disable'):
            self.assertEqual(self.act(csrf, action).status_code, 403)
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())
        self.assertFalse(self.google()['control']['admission_authorized'])

    def test_owner_form_only_sends_fixed_actions_and_csrf_precedes_commit(self):
        csrf = self.login()
        page = self.request(self.client, 'get', '/')
        self.assertIn(b'id="google-control-form"', page.data)
        for action in ('test', 'enable', 'disable'):
            self.assertIn(('name="action" value="' + action + '"').encode(), page.data)
            response = self.request(self.client, 'post', '/api/destinations/google_drive/control', json={'action': action})
            self.assertEqual(response.status_code, 403)
            response = self.act('wrong-csrf', action)
            self.assertEqual(response.status_code, 403)
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())
        self.assert_status(self.act(csrf, 'disable'), 'disable_requested')

    def test_exact_small_json_action_schema_rejects_ids_paths_flags_and_duplicates(self):
        csrf = self.login()
        for data in ({}, {'action': 'upload'}, {'action': True}, {'action': None}, ['disable'],
                     {'action': 'test', 'request_id': 'PRIVATE-id'},
                     {'action': 'enable', 'folder_id': FOLDER_ID},
                     {'action': 'test', 'path': '/PRIVATE/path'},
                     {'action': 'disable', 'enabled': True}):
            with self.subTest(data=data):
                response = self.act(csrf, data=data)
                self.assertEqual(response.status_code, 400)
                self.assert_private(response)
        for raw in (b'{"action":"disable","action":"enable"}',
                    b'{"action":"disable","PRIVATE":"' + b'x' * 2048 + b'"}'):
            response = self.request(self.client, 'post', '/api/destinations/google_drive/control',
                                    data=raw, content_type='application/json', headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 400)
            self.assert_private(response)
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())

    def test_test_request_is_pending_private_idempotent_and_does_not_execute_cloud(self):
        csrf = self.login()
        self.prepare(csrf)
        originals = {name: (self.state / name).read_bytes() for name in ('auth.json', 'oauth-clients.json', 'oauth-google_drive.json')}
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No executor in HTTP')), \
                mock.patch('subprocess.run', side_effect=AssertionError('No executor in HTTP')), \
                mock.patch('socket.create_connection', side_effect=AssertionError('No cloud request')):
            self.assert_status(self.act(csrf), 'test_requested')
            request_id = self.current()['request_id']
            self.assert_status(self.act(csrf), 'test_requested')
            self.assertEqual(self.current()['request_id'], request_id)
        report = self.google()['control']
        self.assertEqual(report['control_state'], 'test_requested')
        self.assertFalse(report['historical'])
        self.assertFalse(report['admission_authorized'])
        for name, value in originals.items():
            self.assertEqual((self.state / name).read_bytes(), value)

    def test_enable_requires_fresh_success_and_historical_permission_is_not_worker_status(self):
        csrf = self.login()
        self.prepare(csrf)
        self.assert_status(self.act(csrf), 'test_requested')
        response = self.act(csrf, 'enable')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['error'], 'google_control_test_required')
        self.assertEqual(self.request(self.client, 'get', '/api/destinations').status_code, 200)
        self.record()
        self.assertEqual(self.google()['control']['control_state'], 'test_verified')
        self.assert_status(self.act(csrf, 'enable'), 'enable_requested')
        self.assertFalse(self.google()['control']['admission_authorized'])
        self.record()
        report = self.google()['control']
        self.assertEqual(report['control_state'], 'enable_recorded')
        self.assertTrue(report['historical'])
        self.assertTrue(report['admission_authorized'])
        self.assertFalse(report['current_worker_state_checked'])
        self.assertFalse(report['account_verified'])
        self.assertEqual(self.request(self.client, 'get', '/api/status').json['worker']['state'], 'not_connected')
        viewer = self.app.test_client()
        self.login(viewer, 'viewer', 'test-viewer-password')
        self.assertTrue(self.google(viewer)['control']['historical'])
        self.assertNotIn(b'id="google-control-form"', self.request(viewer, 'get', '/').data)

    def test_failed_or_old_test_cannot_enable(self):
        csrf = self.login()
        self.prepare(csrf)
        self.act(csrf)
        self.record(verified=False)
        self.assertEqual(self.google()['control']['control_state'], 'test_failed')
        self.assertEqual(self.act(csrf, 'enable').status_code, 409)
        self.assert_status(self.act(csrf), 'test_requested')
        self.record()
        later = time.time() + control.TEST_FRESHNESS + 1
        with mock.patch.object(setup.time, 'time', return_value=later):
            self.assertEqual(self.google()['control']['control_state'], 'test_expired')
            response = self.act(csrf, 'enable')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['error'], 'google_control_test_required')

    def test_disable_supersedes_pending_test_and_needs_no_cloud_readiness(self):
        csrf = self.login()
        self.prepare(csrf)
        self.act(csrf)
        test_id = self.current()['request_id']
        (self.state / 'oauth-clients.json').unlink()
        (self.state / 'oauth-google_drive.json').unlink()
        self.assert_status(self.act(csrf, 'disable'), 'disable_requested')
        self.assertNotEqual(self.current()['request_id'], test_id)
        report = self.google()['control']
        self.assertFalse(report['admission_authorized'])
        self.assertFalse(report['current_worker_state_checked'])
        self.assertTrue((self.state / control.WORKFLOW_DIRECTORY / (test_id + '.request.json')).exists())

    def test_source_change_invalidates_historical_test_and_blocks_enable(self):
        csrf = self.login()
        self.prepare(csrf)
        self.act(csrf)
        self.record()
        self.write('oauth-google_drive.json', {**self.authorization, 'refresh_token': 'PRIVATE-replaced-refresh'})
        self.assertEqual(self.google()['control']['control_state'], 'stale_binding')
        response = self.act(csrf, 'enable')
        self.assertEqual(response.status_code, 409)
        self.assert_private(response)
        self.assertFalse(self.google()['control']['admission_authorized'])

    def test_password_revocation_during_commit_is_rechecked_beyond_cached_user(self):
        actual = control.request_google_control
        def revoke(*args, **kwargs):
            accounts.reset_dashboard_password(self.state, username='owner', password='changed-owner-password')
            return actual(*args, **kwargs)
        with mock.patch.object(control, 'request_google_control', side_effect=revoke):
            app = self.make_app(True)
        client = app.test_client()
        csrf = self.login(client)
        response = self.act(csrf, 'disable', client=client)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['error'], 'google_control_owner_changed')
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())
        self.assertEqual(self.request(client, 'get', '/api/destinations').status_code, 401)

    def test_logout_from_another_tab_during_commit_revokes_actual_session(self):
        actual = control.request_google_control
        other = None
        csrf = None
        def revoke(*args, **kwargs):
            response = self.request(other, 'post', '/logout', headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 302)
            return actual(*args, **kwargs)
        with mock.patch.object(control, 'request_google_control', side_effect=revoke):
            app = self.make_app(True)
        client = app.test_client()
        csrf = self.login(client)
        other = app.test_client()
        other.set_cookie('session', client.get_cookie('session', domain='control.example').value, domain='control.example')
        response = self.act(csrf, 'disable', client=client)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['error'], 'google_control_owner_changed')
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())
        self.assertEqual(self.request(client, 'get', '/api/destinations').status_code, 401)

    def test_role_revocation_during_commit_is_rechecked(self):
        actual = control.request_google_control
        def revoke(*args, **kwargs):
            auth = accounts.read_auth(self.state)
            auth['users']['owner']['role'] = 'viewer'
            self.write('auth.json', auth)
            return actual(*args, **kwargs)
        with mock.patch.object(control, 'request_google_control', side_effect=revoke):
            app = self.make_app(True)
        client = app.test_client()
        csrf = self.login(client)
        self.assertEqual(self.act(csrf, 'disable', client=client).status_code, 409)
        self.assertFalse((self.state / control.WORKFLOW_DIRECTORY).exists())

    def test_source_mutation_during_commit_and_private_io_errors_are_fixed_public_failures(self):
        csrf = self.login()
        self.prepare(csrf)
        actual = control.request_google_control
        def mutate(*args, **kwargs):
            self.write('oauth-google_drive.json', {**self.authorization, 'access_token': 'PRIVATE-mutated-access'})
            return actual(*args, **kwargs)
        with mock.patch.object(control, 'request_google_control', side_effect=mutate):
            app = self.make_app(True)
        client = app.test_client()
        csrf = self.login(client)
        response = self.act(csrf, client=client)
        self.assertEqual(response.status_code, 409)
        self.assert_private(response)
        self.assertFalse(list((self.state / control.WORKFLOW_DIRECTORY).glob('*.request.json')))
        for error in (BackupError, OSError, ValueError, TypeError):
            with mock.patch.object(control, 'request_google_control', side_effect=error('PRIVATE-path-token')):
                app = self.make_app(True)
            client = app.test_client()
            csrf = self.login(client)
            response = self.act(csrf, 'disable', client=client)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json['error'], 'google_control_unavailable')
            self.assert_private(response)


class DashboardControlCliFlagTests(unittest.TestCase):
    def test_dashboard_control_flag_defaults_false_and_requires_separate_opt_in(self):
        for flags, enabled, preparation in (([], False, False), (['--google-setup-requests'], False, True),
                                            (['--google-control-requests'], True, False)):
            with self.subTest(flags=flags), mock.patch('raspi_cloud_sync_pro.dashboard.serve_dashboard') as serve, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['dashboard', '--state', '/private/dashboard', *flags]), 0)
            serve.assert_called_once_with(Path('/private/dashboard'), history=None, worker_state=None, port=8080,
                                          public_origin=None, google_setup_requests=preparation,
                                          enable_google_control_requests=enabled)


if __name__ == '__main__':
    unittest.main()
