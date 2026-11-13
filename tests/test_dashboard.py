import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.dashboard import create_app, initialize_dashboard, worker_snapshot


class WorkerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "worker-status.json"
        self.report = {"jobs_added": 1, "rejected_batches": 0, "cloud_results": ["paused"],
                       "observed_uplink": "wifi", "metered": True, "network_policy_allows_upload": False,
                       "updated_at": 1000, "destinations": ["koofr_webdav/private:folder"],
                       "automatic_deletion": False, "pending_jobs": 2}

    def write_report(self, report=None):
        self.path.write_text(json.dumps(self.report if report is None else report))
        self.path.chmod(0o600)

    def test_report_is_projected_without_destination_names_or_cloud_claims(self):
        self.write_report()
        before = self.path.read_bytes()
        result = worker_snapshot(self.root, now=1001)
        self.assertEqual(result["state"], "observed")
        self.assertEqual(result["pending_jobs"], 2)
        self.assertEqual(result["destination_count"], 1)
        self.assertFalse(result["network_policy_allows_upload"])
        self.assertFalse(result["cloud_accounts_verified"])
        self.assertNotIn("private:folder", json.dumps(result))
        self.assertEqual(self.path.read_bytes(), before)

    def test_stale_report_is_distinguished_without_dropping_observed_queue(self):
        self.write_report({**self.report, "reconciliation_pending": True})
        result = worker_snapshot(self.root, now=1400)
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["age_seconds"], 400)
        self.assertEqual(result["pending_jobs"], 2)
        self.assertTrue(result["reconciliation_pending"])
        self.write_report({**self.report, "updated_at": 1400})
        self.assertEqual(worker_snapshot(self.root, now=1000)["state"], "invalid")

    def test_missing_unconfigured_error_and_unreadable_reports_are_distinct(self):
        self.assertEqual(worker_snapshot(None)["state"], "not_connected")
        self.assertEqual(worker_snapshot(self.root / "missing")["state"], "unavailable")
        self.assertFalse((self.root / "missing").exists())
        self.write_report({"error": "staged_worker_tick_failed", "updated_at": 1000, "automatic_deletion": False})
        result = worker_snapshot(self.root, now=1001)
        self.assertEqual(result["state"], "error")
        self.assertIsNone(result["pending_jobs"])
        self.path.unlink()
        self.path.symlink_to(self.root / "absent")
        self.assertEqual(worker_snapshot(self.root, now=1001)["state"], "unavailable")

    def test_malformed_oversized_secret_and_nonfinite_reports_are_not_exposed(self):
        for data in ({**self.report, "password": "private-test-value"},
                     {**self.report, "pending_jobs": True}, {**self.report, "updated_at": float("nan")},
                     {**self.report, "updated_at": 10 ** 400}, {**self.report, "automatic_deletion": True},
                     {**self.report, "cloud_results": ["arbitrary-provider-output"]},
                     {**self.report, "destinations": [{}]}, {**self.report, "reconciliation_pending": "yes"}):
            self.write_report(data)
            result = worker_snapshot(self.root, now=1001)
            self.assertEqual(result["state"], "invalid")
            self.assertIsNone(result["pending_jobs"])
            self.assertNotIn("private-test-value", json.dumps(result))
        self.path.write_text(" " * 65537)
        self.assertEqual(worker_snapshot(self.root, now=1001)["state"], "invalid")
        self.path.write_text('{"updated_at":1,"updated_at":2}')
        self.assertEqual(worker_snapshot(self.root, now=1001)["state"], "invalid")

    def test_symlinks_fifo_and_writable_status_are_refused_without_following(self):
        self.write_report()
        self.path.chmod(0o666)
        self.assertEqual(worker_snapshot(self.root, now=1001)["state"], "invalid")
        self.path.unlink()
        os.mkfifo(self.path)
        self.assertEqual(worker_snapshot(self.root, now=1001)["state"], "unavailable")
        self.path.unlink()
        self.write_report()
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(worker_snapshot(link, now=1001)["state"], "invalid")
        nested = self.root / "nested"
        nested.mkdir()
        self.assertEqual(worker_snapshot(link / "nested", now=1001)["state"], "invalid")


@unittest.skipUnless(importlib.util.find_spec("flask"), "optional dashboard dependencies not installed")
class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_directory = tempfile.TemporaryDirectory()
        root = Path(cls.auth_directory.name)
        initialize_dashboard(root, password="test-owner-password")
        initialize_dashboard(root, username="viewer", role="viewer", password="test-viewer-password")
        cls.auth = (root / "auth.json").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.auth_directory.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        auth = self.root / "auth.json"
        auth.write_bytes(self.auth)
        auth.chmod(0o600)
        self.app = create_app(self.root, network_reader=lambda: {"interfaces": {}, "cached_global_connectivity": "unknown"})
        self.app.testing = True
        self.client = self.app.test_client()

    def csrf(self):
        with self.client.session_transaction() as session:
            return session["csrf"]

    def login(self, username="owner", password="test-owner-password"):
        self.client.get("/login")
        return self.client.post("/login", data={"csrf": self.csrf(), "username": username, "password": password})

    def upload(self, name="QA.txt", data=b"hello"):
        return self.client.post("/api/files", data={"file": (io.BytesIO(data), name)},
                                headers={"X-CSRF-Token": self.csrf()})

    def test_anonymous_access_is_denied_and_post_requires_csrf(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        for path in ("/api/status", "/api/files", "/api/history", "/api/download", "/api/preview"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/login", data={"username": "owner", "password": "test-owner-password"}).status_code, 403)

    def test_owner_upload_preview_download_and_no_false_cloud_verification(self):
        self.assertEqual(self.login().status_code, 302)
        response = self.upload(data=b"<script>unsafe()</script>\n42")
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json["cloud_verified"])
        listing = self.client.get("/api/files").json["files"]
        self.assertEqual(len(listing), 1)
        item = listing[0]
        self.assertEqual(item["status"], "staged")
        query = {"batch": item["batch"], "name": item["name"]}
        self.assertIn("<script>", self.client.get("/api/preview", query_string=query).json["text"])
        response = self.client.get("/api/download", query_string=query)
        self.assertEqual(response.data, b"<script>unsafe()</script>\n42")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.mimetype, "application/octet-stream")
        response.close()
        self.assertEqual(self.client.get("/api/status").json["cloud"], "not_configured")

    def test_viewer_can_read_but_cannot_upload_and_no_delete_api_exists(self):
        self.login()
        self.upload()
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.login("viewer", "test-viewer-password")
        self.assertEqual(self.client.get("/api/files").status_code, 200)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.delete("/api/files").status_code, 405)
        self.assertEqual(self.client.post("/api/reboot", headers={"X-CSRF-Token": self.csrf()}).status_code, 404)

    def test_logout_revokes_a_copied_session_cookie(self):
        self.login()
        old = self.client.get_cookie("session").value
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.client.set_cookie("session", old)
        self.assertEqual(self.client.get("/api/files").status_code, 401)

    def test_traversal_symlinks_and_unpublished_uploads_are_not_downloadable(self):
        self.login()
        self.assertEqual(self.upload("../secret").status_code, 400)
        self.upload()
        item = self.client.get("/api/files").json["files"][0]
        payload = self.root / "web-inbox" / item["batch"] / "files" / item["name"]
        payload.unlink()
        secret = self.root / "outside.txt"
        secret.write_text("must not be exposed")
        payload.symlink_to(secret)
        response = self.client.get("/api/download", query_string={"batch": item["batch"], "name": item["name"]})
        self.assertEqual(response.status_code, 404)
        for batch, name in (("../auth.json", "secret"), (item["batch"], "../../auth.json"),
                            ("a" * 32 + ".incomplete", "QA.txt")):
            self.assertEqual(self.client.get("/api/download", query_string={"batch": batch, "name": name}).status_code, 404)

    def test_host_protection_security_headers_manifest_and_no_private_offline_cache(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
        self.assertEqual(self.client.get("/login", headers={"Host": "attacker.example"}).status_code, 400)
        self.assertEqual(self.client.get("/manifest.webmanifest").json["display"], "standalone")
        with self.client.get("/sw.js") as response:
            worker = response.data.decode()
        self.assertNotIn("caches.open", worker)
        self.assertNotIn("cache.put", worker)

    def test_account_backups_permissions_and_passwords_never_stored_in_plaintext(self):
        with self.assertRaises(ValueError):
            initialize_dashboard(self.root, password="short")
        with self.assertRaises(ValueError):
            initialize_dashboard(self.root, password="test-owner-password")
        data = json.loads((self.root / "auth.json").read_text())
        self.assertNotIn("test-owner-password", json.dumps(data))
        (self.root / "auth.json").chmod(0o644)
        with self.assertRaises(ValueError):
            create_app(self.root)

    def test_failed_login_is_rate_limited_without_a_password_or_username_echo(self):
        for _ in range(10):
            response = self.login("private-username", "private-password")
            self.assertNotIn(b"private-username", response.data)
            self.assertNotIn(b"private-password", response.data)
        self.assertEqual(self.login().status_code, 429)

    def test_public_origin_requires_https_and_sets_secure_cookies(self):
        for origin in ("http://public.example", "https://u:p@public.example", "https://public.example/path"):
            with self.assertRaises(ValueError):
                create_app(self.root, public_origin=origin)
        client = create_app(self.root, public_origin="https://public.example").test_client()
        response = client.get("/login", base_url="https://public.example")
        self.assertIn("Secure", response.headers["Set-Cookie"])
        self.assertIn("max-age", response.headers["Strict-Transport-Security"])

    def test_optional_worker_report_does_not_enable_cloud_or_create_history(self):
        worker = self.root / "worker"
        worker.mkdir(mode=0o700)
        status = worker / "worker-status.json"
        import time
        status.write_text(json.dumps({"jobs_added": 0, "rejected_batches": 0, "cloud_results": ["verified"],
                          "observed_uplink": "wifi", "metered": False, "network_policy_allows_upload": True,
                          "updated_at": time.time(), "destinations": ["google_drive_rclone/test:folder"],
                          "automatic_deletion": False, "pending_jobs": 0}))
        status.chmod(0o600)
        self.client = create_app(self.root, worker_state=worker, network_reader=lambda: {}).test_client()
        self.login("viewer", "test-viewer-password")
        response = self.client.get("/api/status")
        self.assertEqual(response.json["worker"]["state"], "observed")
        self.assertEqual(response.json["worker"]["pending_jobs"], 0)
        self.assertEqual(response.json["cloud"], "not_configured")
        self.assertFalse(response.json["worker"]["cloud_accounts_verified"])
        self.assertEqual(self.client.get("/api/history").json, {"available": False, "jobs": []})
        self.assertFalse((worker / "history.sqlite3").exists())
        self.assertEqual(list(worker.iterdir()), [status])

    def change_password(self, current="test-owner-password", password="new-owner-password", confirmation=None, **extra):
        return self.client.post("/account", data={"csrf": self.csrf(), "current_password": current,
                                "password": password, "confirmation": password if confirmation is None else confirmation,
                                **extra})

    def test_password_change_revokes_account_sessions_and_preserves_other_roles(self):
        self.assertEqual(self.client.get("/account").status_code, 302)
        self.login()
        other_owner = self.app.test_client()
        viewer = self.app.test_client()
        for client, username, password in ((other_owner, "owner", "test-owner-password"),
                                            (viewer, "viewer", "test-viewer-password")):
            client.get("/login")
            with client.session_transaction() as session:
                token = session["csrf"]
            self.assertEqual(client.post("/login", data={"csrf": token, "username": username,
                                                       "password": password}).status_code, 302)
        old_cookie = self.client.get_cookie("session").value
        response = self.change_password()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))
        self.assertEqual(other_owner.get("/api/status").status_code, 401)
        self.assertEqual(viewer.get("/api/status").json["role"], "viewer")
        self.client.set_cookie("session", old_cookie)
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.login(password="new-owner-password").status_code, 302)
        self.assertEqual(self.client.get("/api/status").json["role"], "owner")

    def test_viewer_password_change_cannot_target_another_account_or_change_role(self):
        before = json.loads((self.root / "auth.json").read_text())
        self.login("viewer", "test-viewer-password")
        response = self.change_password(current="test-viewer-password", password="new-viewer-password", username="owner")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads((self.root / "auth.json").read_text()), before)
        self.assertEqual(self.change_password(current="test-viewer-password", password="new-viewer-password").status_code, 302)
        after = json.loads((self.root / "auth.json").read_text())
        self.assertEqual(after["users"]["owner"], before["users"]["owner"])
        self.assertEqual(after["users"]["viewer"]["role"], "viewer")
        self.assertEqual(self.login("viewer", "new-viewer-password").status_code, 302)
        self.assertEqual(self.upload().status_code, 403)

    def test_password_form_requires_csrf_and_rejects_bad_input_without_secret_echo(self):
        self.login()
        original = (self.root / "auth.json").read_bytes()
        self.assertEqual(self.client.post("/account", data={"current_password": "private-wrong-password"}).status_code, 403)
        self.assertEqual(self.client.post("/account", data={"csrf": "\u2603"}).status_code, 403)
        for arguments in ({"current": "private-wrong-password"}, {"confirmation": "private-confirmation"},
                          {"password": "short"}, {"password": "x" * 257}):
            response = self.change_password(**arguments)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(b"private-wrong-password", response.data)
            self.assertNotIn(b"private-confirmation", response.data)
            self.assertNotIn(b"new-owner-password", response.data)
            self.assertEqual((self.root / "auth.json").read_bytes(), original)
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.assertIn(b'autocomplete="new-password"', self.client.get("/account").data)

    def test_password_changes_are_throttled_and_cli_reset_revokes_without_restart(self):
        self.login()
        for _ in range(6):
            self.assertEqual(self.change_password(current="wrong-current-password").status_code, 400)
        self.assertEqual(self.change_password().status_code, 429)
        from raspi_cloud_sync_pro.accounts import reset_dashboard_password
        reset_dashboard_password(self.root, username="owner", password="recovered-owner-password")
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        self.assertEqual(self.login(password="recovered-owner-password").status_code, 302)
        self.assertEqual(self.client.get("/api/status").json["role"], "owner")

    def test_account_storage_failure_does_not_claim_success_or_revoke_valid_password(self):
        self.login()
        with mock.patch("raspi_cloud_sync_pro.accounts.os.replace", side_effect=OSError("private-storage-error")):
            response = self.change_password()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"private-storage-error", response.data)
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.assertEqual((self.root / "auth.json").read_bytes(), self.auth)
