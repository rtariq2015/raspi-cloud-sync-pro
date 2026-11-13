import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from raspi_cloud_sync_pro.dashboard import create_app, initialize_dashboard


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
