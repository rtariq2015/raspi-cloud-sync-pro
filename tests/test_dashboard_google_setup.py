"""Owner preparation requests stay local and never grant upload authority."""

import hashlib
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.accounts import initialize_dashboard, reset_dashboard_password
from raspi_cloud_sync_pro.dashboard import create_app
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro.google_setup import record_google_setup_receipt


@unittest.skipUnless(importlib.util.find_spec("flask"), "optional dashboard dependencies not installed")
class DashboardGoogleSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        root = Path(cls.fixture.name)
        initialize_dashboard(root, password="test-owner-password")
        initialize_dashboard(root, username="viewer", role="viewer", password="test-viewer-password")
        cls.auth_bytes = (root / "auth.json").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.origin = "https://device.example"
        self.client_settings = {"client_id": "PRIVATE-client.apps.googleusercontent.com",
                                "client_secret": "PRIVATE-client-secret"}
        self.write_json("oauth-clients.json", {"schema_version": 1, "public_origin": self.origin,
                        "providers": {"google_drive": self.client_settings}})
        self.authorization = {"schema_version": 1, "provider": "google_drive",
            "client_id_sha256": hashlib.sha256(self.client_settings["client_id"].encode()).hexdigest(),
            "received_at": time.time() - 60, "expires_at": time.time() + 3540,
            "token_type": "Bearer", "access_token": "PRIVATE-access-token",
            "refresh_token": "PRIVATE-refresh-token", "scopes": [destination.GOOGLE_SCOPE]}
        self.write_json("oauth-google_drive.json", self.authorization)
        (self.root / "auth.json").write_bytes(self.auth_bytes)
        (self.root / "auth.json").chmod(0o600)
        self.app = self.make_app()
        self.client = self.app.test_client()

    def write_json(self, filename, value):
        path = self.root / filename
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def make_app(self, enabled=True):
        app = create_app(self.root, public_origin=self.origin, network_reader=lambda: {},
                         google_setup_requests=enabled)
        app.testing = True
        return app

    def request(self, client, method, path, **kwargs):
        return getattr(client, method)(path, base_url=self.origin, **kwargs)

    def login(self, client=None, username="owner", password="test-owner-password"):
        client = client or self.client
        self.request(client, "get", "/login")
        with client.session_transaction(base_url=self.origin) as session:
            csrf = session["csrf"]
        response = self.request(client, "post", "/login", data={"csrf": csrf, "username": username, "password": password})
        self.assertEqual(response.status_code, 302)
        with client.session_transaction(base_url=self.origin) as session:
            return session["csrf"]

    def prepare(self, csrf, *, client=None, data=None):
        return self.request(client or self.client, "post", "/api/destinations/google_drive/prepare",
                            json={"folder_name": "PRIVATE_FOLDER"} if data is None else data,
                            headers={"X-CSRF-Token": csrf})

    def google_status(self, client=None):
        response = self.request(client or self.client, "get", "/api/destinations")
        self.assertEqual(response.status_code, 200)
        return next(provider for provider in response.json["providers"] if provider["provider"] == "google_drive")

    def assert_private(self, response):
        text = response.get_data(as_text=True)
        for value in ("PRIVATE", self.origin, self.client_settings["client_id"], self.client_settings["client_secret"],
                      "authorization_sha256", "rclone_config_sha256", "owner_credential_tag", "request_id"):
            self.assertNotIn(value, text)

    def current_request(self):
        workflow = self.root / "google-setup"
        identifier = json.loads((workflow / "current.json").read_text())["request_id"]
        return json.loads((workflow / (identifier + ".request.json")).read_text())

    def record_receipt(self):
        request = self.current_request()
        with destination._private_directory(self.root) as directory:
            authorization = destination._source_authorization(directory, self.origin)
        record = {"schema_version": 1, "provider": "google_drive", "request_id": request["request_id"],
            "authorization_sha256": request["authorization_sha256"], "folder_name": request["folder_name"],
            "rclone_config_sha256": hashlib.sha256(destination._render_config(authorization)).hexdigest(),
            "configuration_matches": True, "prepared_at": time.time(), "enabled": False, "test_required": True,
            "account_verified": False, "folder_exists_verified": False, "automatic_deletion": False}
        payload = json.dumps(record).encode()
        reader, writer = os.pipe()
        os.write(writer, len(payload).to_bytes(4, "big") + payload)
        os.close(writer)
        return record_google_setup_receipt(self.root, self.origin, request["request_id"], reader)

    def test_owners_csrf_and_explicit_service_availability_are_required(self):
        self.assertEqual(self.request(self.client, "get", "/api/destinations").status_code, 401)
        csrf = self.login(username="viewer", password="test-viewer-password")
        page = self.request(self.client, "get", "/")
        self.assertNotIn(b'id="google-prepare-form"', page.data)
        self.assertEqual(self.prepare(csrf).status_code, 403)
        self.assertFalse((self.root / "google-setup").exists())
        csrf = self.login()
        self.assertEqual(self.request(self.client, "post", "/api/destinations/google_drive/prepare",
                                     json={"folder_name": "Backups"}).status_code, 403)
        disabled = self.make_app(enabled=False).test_client()
        disabled_csrf = self.login(disabled)
        response = self.prepare(disabled_csrf, client=disabled)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"], "google_setup_service_unavailable")
        self.assertFalse((self.root / "google-setup").exists())

    def test_owner_request_is_local_idempotent_private_and_keeps_uploads_off(self):
        csrf = self.login()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("No broker execution in web service")), \
                mock.patch("socket.create_connection", side_effect=AssertionError("No cloud connection")):
            response = self.prepare(csrf)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json["preparation"]["preparation_state"], "requested")
            self.assertFalse(response.json["uploads_enabled"])
            self.assert_private(response)
            first = self.current_request()["request_id"]
            again = self.prepare(csrf)
            self.assertEqual(again.status_code, 202)
            self.assertEqual(self.current_request()["request_id"], first)
            status = self.google_status()
        self.assertTrue(status["preparation_requests_available"])
        self.assertEqual(status["preparation"]["preparation_state"], "requested")
        self.assertFalse(status["uploads_enabled"])
        self.assertEqual(len(list((self.root / "google-setup").glob("*.request.json"))), 1)

    def test_bad_labels_extra_fields_and_client_tokens_are_never_echoed_or_queued(self):
        csrf = self.login()
        for value in ("../PRIVATE", "PRIVATE/child", "PRIVATE\nline", "PRIVATE" * 20, None, True):
            response = self.prepare(csrf, data={"folder_name": value})
            self.assertEqual(response.status_code, 409)
            self.assert_private(response)
            self.assertFalse((self.root / "google-setup").exists())
        response = self.prepare(csrf, data={"folder_name": "Backups", "access_token": "PRIVATE"})
        self.assertEqual(response.status_code, 400)
        self.assert_private(response)
        self.assertFalse((self.root / "google-setup").exists())

    def test_saved_receipt_is_historical_and_never_changes_file_or_cloud_authority(self):
        csrf = self.login()
        self.assertEqual(self.prepare(csrf).status_code, 202)
        self.record_receipt()
        response = self.request(self.client, "get", "/api/destinations")
        self.assert_private(response)
        state = self.google_status()["preparation"]
        self.assertEqual(state["preparation_state"], "prepared_untested")
        self.assertTrue(state["historical"])
        self.assertFalse(state["current_worker_state_checked"])
        for field in ("enabled", "account_verified", "folder_exists_verified", "automatic_deletion"):
            self.assertFalse(state[field])
        self.assertEqual(self.request(self.client, "get", "/api/status").json["cloud"], "not_configured")
        viewer = self.app.test_client()
        self.login(viewer, "viewer", "test-viewer-password")
        viewer_state = self.google_status(viewer)["preparation"]
        self.assertGreaterEqual(viewer_state["age_seconds"], state["age_seconds"])
        self.assertEqual({key: value for key, value in viewer_state.items() if key != "age_seconds"},
                         {key: value for key, value in state.items() if key != "age_seconds"})

    def test_changed_source_after_receipt_cannot_keep_prepared_status(self):
        csrf = self.login()
        self.assertEqual(self.prepare(csrf).status_code, 202)
        self.record_receipt()
        changed = {**self.client_settings, "client_secret": "PRIVATE-replaced-secret"}
        self.write_json("oauth-clients.json", {"schema_version": 1, "public_origin": self.origin,
                        "providers": {"google_drive": changed}})
        state = self.google_status()["preparation"]
        self.assertEqual(state["preparation_state"], "stale_receipt")
        self.assertFalse(state["enabled"])
        self.assertFalse(state["account_verified"])

    def test_password_revocation_during_request_is_rechecked_beyond_flask_cache(self):
        from raspi_cloud_sync_pro.google_setup import request_google_setup

        def revoke(*args, **kwargs):
            reset_dashboard_password(self.root, username="owner", password="changed-owner-password")
            return request_google_setup(*args, **kwargs)

        with mock.patch("raspi_cloud_sync_pro.google_setup.request_google_setup", side_effect=revoke):
            app = self.make_app()
        client = app.test_client()
        csrf = self.login(client)
        response = self.prepare(csrf, client=client)
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.root / "google-setup").exists())
        self.assertEqual(self.request(client, "get", "/api/destinations").status_code, 401)

    def test_unsafe_workflow_is_reported_without_following_it(self):
        csrf = self.login()
        foreign = self.root / "private-target"
        foreign.mkdir(mode=0o700)
        (self.root / "google-setup").symlink_to(foreign, target_is_directory=True)
        response = self.prepare(csrf)
        self.assertEqual(response.status_code, 409)
        self.assert_private(response)
        self.assertEqual(list(foreign.iterdir()), [])
        report = self.google_status()["preparation"]
        self.assertIn(report["preparation_state"], ("invalid", "unavailable"))
        self.assertFalse(report["enabled"])
        self.assertFalse(report["current_worker_state_checked"])

    def test_setup_availability_cannot_be_supplied_as_an_ambiguous_value(self):
        for value in ("yes", 1, None):
            with self.assertRaises(ValueError):
                self.make_app(value)
