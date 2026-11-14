import base64
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from raspi_cloud_sync_pro.oauth import OAuthError, OAuthManager, OwnerProof, PROVIDERS, _NoRedirect, exchange_code


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.origin = "https://device.example"
        self.clients = {"schema_version": 1, "public_origin": self.origin,
                        "providers": {name: {"client_id": name + "-client-id", "client_secret": "private-test-client-secret"}
                                      for name in PROVIDERS}}
        self.config = self.root / "oauth-clients.json"
        self.config.write_text(json.dumps(self.clients))
        self.config.chmod(0o600)
        self.clock = [0]
        self.live = True
        self.calls = []
        self.proof = OwnerProof("owner", "session-identifier", "a" * 64)
        self.manager = OAuthManager(self.root, self.origin, transport=self.transport, clock=lambda: self.clock[0])

    def tokens(self, provider):
        return {"access_token": "private-test-access-token", "refresh_token": "private-test-refresh-token",
                "token_type": "Bearer", "expires_in": 3600, "scope": PROVIDERS[provider]["scope"]}

    def transport(self, provider, fields):
        self.calls.append((provider, fields))
        return self.tokens(provider)

    @contextmanager
    def guard(self, proof):
        if not self.live or proof != self.proof:
            raise OAuthError("oauth_owner_session_changed")
        yield

    def start(self, provider="google_drive"):
        report = self.manager.start(provider, self.proof)
        url = urlsplit(report["authorization_url"])
        return report, url, parse_qs(url.query)

    def handoff(self, provider="google_drive"):
        _, _, query = self.start(provider)
        return self.manager.callback(provider, query["state"][0], code="private-test-authorization-code")

    def test_both_providers_use_fixed_code_pkce_offline_scope_without_client_secret_in_url(self):
        for provider in PROVIDERS:
            report, url, query = self.start(provider)
            self.assertEqual(url.scheme + "://" + url.netloc + url.path, PROVIDERS[provider]["authorize"])
            self.assertEqual(query["response_type"], ["code"])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertEqual(query["scope"], [PROVIDERS[provider]["scope"]])
            self.assertEqual(query["redirect_uri"], [self.origin + "/oauth/callback/" + provider])
            self.assertNotIn("private-test-client-secret", report["authorization_url"])
            self.assertFalse(report["uploads_enabled"])
            ticket = self.manager.callback(provider, query["state"][0], code="private-test-authorization-code")
            completion = self.manager.complete(ticket, self.proof, self.guard)
            sent = self.calls[-1][1]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).decode().rstrip("=")
            self.assertEqual(challenge, query["code_challenge"][0])
            self.assertEqual(sent["grant_type"], "authorization_code")
            self.assertEqual(completion["authorization"], "authorization_saved_untested")
            self.assertFalse(completion["test_verified"])
            self.assertFalse(completion["folder_selected"])
            self.assertFalse(completion["uploads_enabled"])
            token = self.root / ("oauth-" + provider + ".json")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)
            self.assertEqual(token.stat().st_nlink, 1)
            self.assertNotIn("private-test-access-token", json.dumps(completion))
        readiness = self.manager.readiness()
        self.assertTrue(all(item["authorization"] == "authorization_saved_untested" for item in readiness))
        self.assertNotIn("private-test-", json.dumps(readiness))

    def test_missing_invalid_or_wrong_origin_client_settings_fail_without_state_or_network(self):
        for data in ({**self.clients, "public_origin": "https://other.example"},
                     {**self.clients, "providers": {}}, {**self.clients, "authorize_url": "https://attacker.example"}):
            self.config.write_text(json.dumps(data))
            with self.assertRaisesRegex(OAuthError, "oauth_client_setup_required"):
                self.manager.start("google_drive", self.proof)
            self.assertEqual(self.manager.flows, {})
        self.config.unlink()
        self.assertTrue(all(not row["client_ready"] for row in self.manager.readiness()))
        self.assertEqual(self.calls, [])
        for origin in (None, "http://device.example", "https://u:p@device.example", "https://device.example/path"):
            with self.assertRaisesRegex(OAuthError, "oauth_https_setup_required"):
                OAuthManager(self.root, origin).start("google_drive", self.proof)

    def test_state_callback_and_handoff_are_expiring_one_time_and_provider_bound(self):
        _, _, query = self.start()
        state = query["state"][0]
        with self.assertRaises(OAuthError):
            self.manager.callback("dropbox", state, code="code")
        with self.assertRaises(OAuthError):
            self.manager.callback("google_drive", state, code="code")
        ticket = self.handoff()
        self.manager.complete(ticket, self.proof, self.guard)
        with self.assertRaises(OAuthError):
            self.manager.complete(ticket, self.proof, self.guard)
        _, _, query = self.start("dropbox")
        self.clock[0] += 301
        with self.assertRaises(OAuthError):
            self.manager.callback("dropbox", query["state"][0], code="code")
        ticket = self.handoff("dropbox")
        self.clock[0] += 91
        with self.assertRaises(OAuthError):
            self.manager.complete(ticket, self.proof, self.guard)

    def test_stolen_handoff_or_changed_owner_proof_cannot_exchange(self):
        ticket = self.handoff()
        wrong = OwnerProof("viewer", "different-session", "b" * 64)
        with self.assertRaises(OAuthError):
            self.manager.complete(ticket, wrong, self.guard)
        self.assertEqual(self.calls, [])
        ticket = self.handoff()
        self.live = False
        with self.assertRaisesRegex(OAuthError, "oauth_owner_session_changed"):
            self.manager.complete(ticket, self.proof, self.guard)
        self.assertEqual(self.calls, [])

    def test_session_revocation_during_exchange_prevents_private_token_publication(self):
        ticket = self.handoff()

        def exchange(provider, fields):
            self.live = False
            return self.tokens(provider)

        self.manager.transport = exchange
        with self.assertRaisesRegex(OAuthError, "oauth_owner_session_changed"):
            self.manager.complete(ticket, self.proof, self.guard)
        self.assertFalse((self.root / "oauth-google_drive.json").exists())

    def test_decline_or_transport_failure_is_sanitized_and_never_saved(self):
        _, _, query = self.start()
        ticket = self.manager.callback("google_drive", query["state"][0], error="access_denied")
        with self.assertRaisesRegex(OAuthError, "oauth_authorization_declined"):
            self.manager.complete(ticket, self.proof, self.guard)
        self.assertEqual(self.calls, [])
        self.manager.transport = mock.Mock(side_effect=RuntimeError("private-test-provider-response"))
        with self.assertRaises(OAuthError) as raised:
            self.manager.complete(self.handoff(), self.proof, self.guard)
        self.assertEqual(str(raised.exception), "oauth_exchange_failed")
        self.assertFalse((self.root / "oauth-google_drive.json").exists())

    def test_offline_tokens_scopes_expiry_and_private_file_permissions_are_required(self):
        base = self.tokens("google_drive")
        for changed in ({**base, "refresh_token": ""}, {**base, "scope": "unrequested"},
                        {**base, "expires_in": float("nan")}, {**base, "expires_in": True},
                        {**base, "expires_in": 10 ** 400}, {**base, "access_token": "a\nsecret"}):
            self.manager.transport = lambda provider, fields: changed
            with self.assertRaises(OAuthError):
                self.manager.complete(self.handoff(), self.proof, self.guard)
            self.assertFalse((self.root / "oauth-google_drive.json").exists())
        self.config.chmod(0o644)
        self.assertTrue(all(not row["client_ready"] for row in self.manager.readiness()))

    def test_duplicate_authorization_is_not_overwritten_and_state_memory_is_bounded(self):
        second_ticket = self.handoff()
        self.manager.complete(self.handoff(), self.proof, self.guard)
        token = self.root / "oauth-google_drive.json"
        original = token.read_bytes()
        with self.assertRaisesRegex(OAuthError, "oauth_authorization_already_saved"):
            self.manager.complete(second_ticket, self.proof, self.guard)
        with self.assertRaisesRegex(OAuthError, "oauth_authorization_already_saved"):
            self.start()
        self.assertEqual(token.read_bytes(), original)
        for _ in range(16):
            self.start("dropbox")
        with self.assertRaisesRegex(OAuthError, "oauth_try_later"):
            self.start("dropbox")
        self.assertLessEqual(len(self.manager.flows), 16)
        self.clock[0] += 301
        self.start("dropbox")
        self.assertEqual(len(self.manager.flows), 1)

    def test_exchange_concurrency_is_bounded(self):
        tickets = [self.handoff("google_drive"), self.handoff("dropbox"), self.handoff("google_drive")]
        started = threading.Barrier(3)
        release = threading.Event()

        def exchange(provider, fields):
            self.calls.append(provider)
            started.wait(timeout=3)
            if not release.wait(timeout=3):
                raise RuntimeError("fixture timed out")
            return self.tokens(provider)

        self.manager.transport = exchange
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = [executor.submit(self.manager.complete, ticket, self.proof, self.guard) for ticket in tickets[:2]]
            started.wait(timeout=3)
            try:
                with self.assertRaisesRegex(OAuthError, "oauth_try_later"):
                    self.manager.complete(tickets[2], self.proof, self.guard)
            finally:
                release.set()
            for future in pending:
                self.assertFalse(future.result()["uploads_enabled"])
        self.assertEqual(len(self.calls), 2)

    def test_timed_out_exchange_never_saves_late_tokens_and_retains_bounded_slots(self):
        release = threading.Event()
        done = threading.Event()

        def exchange(provider, fields):
            release.wait(timeout=3)
            done.set()
            return self.tokens(provider)

        self.manager.transport = exchange
        self.manager.exchange_timeout = 0.01
        try:
            for _ in range(2):
                with self.assertRaisesRegex(OAuthError, "oauth_exchange_failed"):
                    self.manager.complete(self.handoff(), self.proof, self.guard)
            with self.assertRaisesRegex(OAuthError, "oauth_try_later"):
                self.manager.complete(self.handoff(), self.proof, self.guard)
        finally:
            release.set()
            self.assertTrue(done.wait(timeout=3))
        self.assertFalse((self.root / "oauth-google_drive.json").exists())

    def test_private_token_publication_failure_is_sanitized_without_active_authorization(self):
        with mock.patch("raspi_cloud_sync_pro.oauth.os.link", side_effect=OSError("private-filesystem-error")):
            with self.assertRaises(OAuthError) as raised:
                self.manager.complete(self.handoff(), self.proof, self.guard)
        self.assertEqual(str(raised.exception), "oauth_private_storage_unavailable")
        self.assertFalse((self.root / "oauth-google_drive.json").exists())
        self.assertEqual(list(self.root.glob("oauth-*.tmp")), [])

    def test_real_transport_is_fixed_post_bounded_and_redirect_refusing(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                pass

            def read(self, limit):
                self.limit = limit
                return b'{"access_token":"fake"}'

        response = Response()
        opener = mock.Mock()
        opener.open.return_value = response
        fields = {"code": "private-code", "code_verifier": "private-proof", "client_secret": "private-secret"}
        with mock.patch("urllib.request.build_opener", return_value=opener) as factory:
            self.assertEqual(exchange_code("google_drive", fields), {"access_token": "fake"})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, PROVIDERS["google_drive"]["token"])
        self.assertEqual(request.method, "POST")
        self.assertNotIn("private-", request.full_url)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
        self.assertEqual(response.limit, 65537)
        self.assertTrue(any(isinstance(handler, _NoRedirect) for handler in factory.call_args.args))
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.example"))
        response.status = 302
        with mock.patch("urllib.request.build_opener", return_value=opener), self.assertRaises(OAuthError):
            exchange_code("google_drive", fields)
