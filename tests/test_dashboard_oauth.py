import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from raspi_cloud_sync_pro.accounts import initialize_dashboard, reset_dashboard_password
from raspi_cloud_sync_pro.dashboard import create_app
from raspi_cloud_sync_pro.oauth import PROVIDERS


@unittest.skipUnless(importlib.util.find_spec("flask"), "optional dashboard dependencies not installed")
class DashboardOAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        root = Path(cls.fixture.name)
        initialize_dashboard(root, password="test-owner-password")
        initialize_dashboard(root, username="viewer", role="viewer", password="test-viewer-password")
        cls.auth = (root / "auth.json").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        path = self.root / "auth.json"
        path.write_bytes(self.auth)
        path.chmod(0o600)
        self.origin = "https://device.example"
        config = self.root / "oauth-clients.json"
        config.write_text(json.dumps({"schema_version": 1, "public_origin": self.origin,
                                      "providers": {provider: {"client_id": provider + "-client", "client_secret": "private-client-secret"}
                                                    for provider in PROVIDERS}}))
        config.chmod(0o600)
        self.calls = []
        self.during_exchange = None
        self.app = create_app(self.root, public_origin=self.origin, oauth_transport=self.exchange, network_reader=lambda: {})
        self.app.testing = True
        self.client = self.app.test_client()

    def request(self, client, method, path, **kwargs):
        return getattr(client, method)(path, base_url=self.origin, **kwargs)

    def login(self, client=None, username="owner", password="test-owner-password"):
        client = self.client if client is None else client
        self.request(client, "get", "/login")
        with client.session_transaction(base_url=self.origin) as session:
            token = session["csrf"]
        self.assertEqual(self.request(client, "post", "/login", data={"csrf": token, "username": username,
                                                                        "password": password}).status_code, 302)
        with client.session_transaction(base_url=self.origin) as session:
            return session["csrf"]

    def exchange(self, provider, fields):
        self.calls.append(provider)
        if self.during_exchange:
            self.during_exchange()
        return {"access_token": "private-access-token", "refresh_token": "private-refresh-token", "token_type": "Bearer",
                "expires_in": 3600, "scope": PROVIDERS[provider]["scope"]}

    def start(self, token, provider="google_drive"):
        response = self.request(self.client, "post", "/api/destinations/" + provider + "/connect",
                                headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        query = parse_qs(urlsplit(response.json["authorization_url"]).query)
        return query["state"][0]

    def return_without_strict_session_cookie(self, state, provider="google_drive"):
        callback = self.app.test_client()
        response = self.request(callback, "get", "/oauth/callback/" + provider,
                                query_string={"state": state, "code": "private-authorization-code"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/oauth/return")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("private-authorization-code", response.get_data(as_text=True))
        cookie = callback.get_cookie("raspi_oauth_handoff", domain="device.example", path="/api/oauth/complete")
        self.assertTrue(cookie.secure)
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, "Strict")
        self.assertEqual(self.calls, [])
        return cookie.value

    def complete(self, client, ticket, token):
        client.set_cookie("raspi_oauth_handoff", ticket, domain="device.example", path="/api/oauth/complete", secure=True)
        return self.request(client, "post", "/api/oauth/complete", headers={"X-CSRF-Token": token})

    def test_owner_csrf_and_private_client_setup_are_required_before_redirect(self):
        self.assertEqual(self.request(self.client, "get", "/api/destinations").status_code, 401)
        token = self.login(username="viewer", password="test-viewer-password")
        self.assertEqual(self.request(self.client, "post", "/api/destinations/google_drive/connect",
                                     headers={"X-CSRF-Token": token}).status_code, 403)
        self.assertEqual(self.request(self.client, "get", "/api/oauth/context").status_code, 403)
        token = self.login()
        self.assertEqual(self.request(self.client, "post", "/api/destinations/google_drive/connect").status_code, 403)
        (self.root / "oauth-clients.json").unlink()
        response = self.request(self.client, "post", "/api/destinations/google_drive/connect", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"], "oauth_client_setup_required")
        self.assertNotIn("authorization_url", response.json)
        self.assertFalse(response.json["uploads_enabled"])

    def test_callback_does_not_need_strict_session_cookie_and_completion_requires_same_session(self):
        token = self.login()
        state = self.start(token)
        ticket = self.return_without_strict_session_cookie(state)
        landing = self.request(self.client, "get", "/oauth/return")
        self.assertNotIn(b"private-authorization-code", landing.data)
        self.assertNotIn(b"private-client-secret", landing.data)
        context = self.request(self.client, "get", "/api/oauth/context")
        self.assertEqual(context.json, {"csrf": token})
        response = self.complete(self.client, ticket, context.json["csrf"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["authorization"], "authorization_saved_untested")
        self.assertFalse(response.json["uploads_enabled"])
        self.assertFalse(response.json["test_verified"])
        self.assertNotIn("private-", json.dumps(response.json))
        self.assertEqual(self.calls, ["google_drive"])
        self.assertEqual(self.request(self.client, "get", "/api/status").json["cloud"], "not_configured")
        self.assertIsNone(self.client.get_cookie("raspi_oauth_handoff", domain="device.example", path="/api/oauth/complete"))
        self.assertEqual(self.complete(self.client, ticket, token).status_code, 409)
        self.assertEqual(self.calls, ["google_drive"])
        readiness = self.request(self.client, "get", "/api/destinations").json
        self.assertNotIn("private-", json.dumps(readiness))

    def test_head_callback_cannot_consume_state(self):
        token = self.login()
        state = self.start(token, "dropbox")
        response = self.request(self.client, "head", "/oauth/callback/dropbox",
                                query_string={"state": state, "code": "private-authorization-code"})
        self.assertEqual(response.status_code, 405)
        ticket = self.return_without_strict_session_cookie(state, "dropbox")
        self.assertEqual(self.complete(self.client, ticket, token).status_code, 200)

    def test_other_owner_session_and_invalid_csrf_cannot_finish_authorization(self):
        token = self.login()
        ticket = self.return_without_strict_session_cookie(self.start(token))
        self.assertEqual(self.complete(self.client, ticket, "wrong-csrf").status_code, 403)
        self.assertEqual(self.calls, [])
        other = self.app.test_client()
        other_token = self.login(other)
        self.assertEqual(self.complete(other, ticket, other_token).status_code, 409)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "oauth-google_drive.json").exists())

    def test_password_revocation_during_exchange_is_rechecked_beyond_flask_g_cache(self):
        token = self.login()
        ticket = self.return_without_strict_session_cookie(self.start(token))
        self.during_exchange = lambda: reset_dashboard_password(self.root, username="owner", password="changed-owner-password")
        response = self.complete(self.client, ticket, token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"], "oauth_owner_session_changed")
        self.assertFalse((self.root / "oauth-google_drive.json").exists())
        self.assertEqual(self.request(self.client, "get", "/api/status").status_code, 401)

    def test_invalid_callback_query_is_cleaned_without_code_or_provider_error_echo(self):
        response = self.request(self.client, "get", "/oauth/callback/google_drive",
                                query_string={"state": "invalid", "error": "private-provider-error", "error_description": "private-description"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/oauth/return")
        self.assertNotIn("private-", response.get_data(as_text=True))
        self.assertNotIn("private-", str(response.headers))
        self.assertEqual(self.calls, [])
