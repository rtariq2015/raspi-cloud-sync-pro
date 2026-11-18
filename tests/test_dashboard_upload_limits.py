"""Actual authenticated multipart uploads, spooled files and retained limits."""

import hashlib
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

from raspi_cloud_sync_pro.dashboard import create_app, initialize_dashboard

MIB = 1024 * 1024
PASSWORD = "temporary-owner-upload-password"
VIEWER_PASSWORD = "temporary-viewer-upload-password"


@unittest.skipUnless(importlib.util.find_spec("flask"), "optional dashboard dependencies not installed")
class DashboardUploadLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_directory = tempfile.TemporaryDirectory()
        state = Path(cls.auth_directory.name)
        initialize_dashboard(state, password=PASSWORD)
        initialize_dashboard(state, username="viewer", role="viewer", password=VIEWER_PASSWORD)
        cls.auth = (state / "auth.json").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.auth_directory.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "private-state"
        self.state.mkdir(mode=0o700)
        auth = self.state / "auth.json"
        auth.write_bytes(self.auth)
        auth.chmod(0o600)
        self.app = create_app(self.state, network_reader=lambda: {"interfaces": {}})
        self.app.testing = True
        self.client = self.app.test_client()
        self.login()

    def csrf(self):
        with self.client.session_transaction() as session:
            return session["csrf"]

    def login(self, username="owner", password=PASSWORD):
        self.client.get("/login")
        response = self.client.post("/login", data={"csrf": self.csrf(), "username": username, "password": password})
        self.assertEqual(response.status_code, 302)

    def payload(self, size, *, sparse=False):
        path = self.root / "source.bin"
        with path.open("wb") as stream:
            if sparse:
                stream.truncate(size)
            else:
                block = bytes(range(256)) * 256
                remaining = size
                while remaining:
                    written = stream.write(block[:min(remaining, len(block))])
                    remaining -= written
        return path

    def upload(self, path, *, name="upload.bin", fields=None, csrf=True):
        headers = {"X-CSRF-Token": self.csrf()} if csrf else {}
        with path.open("rb") as stream:
            response = self.client.post("/api/files", data={**(fields or {}), "file": (stream, name)},
                                        content_type="multipart/form-data", headers=headers)
        # The test client's encoded multipart body is itself a temporary file
        # for large requests; an early 413 never parses/owns that input stream.
        response.request.environ["wsgi.input"].close()
        return response

    def batches(self, suffix="ready"):
        return list((self.state / "web-inbox").glob("*." + suffix))

    def test_real_file_chunks_reported_size_and_spooled_megabyte_roundtrip(self):
        # These cross the old 32 KiB ceiling, Werkzeug's 64 KiB read boundary,
        # both readings of the reported 122.7 KB, and its 500 KiB spool threshold.
        for size in (32 * 1024, 64 * 1024, 122700, 125645, MIB):
            with self.subTest(file_bytes=size):
                path = self.payload(size)
                expected = path.read_bytes()  # At most 1 MiB; large-limit tests stay on disk.
                digest = hashlib.sha256(expected).hexdigest()
                before = len(self.batches())
                response = self.upload(path)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json["status"], "staged")
                self.assertIs(response.json["cloud_verified"], False)
                self.assertEqual(len(self.batches()), before + 1)
                ready = self.state / "web-inbox" / response.json["batch"]
                manifest = json.loads((ready / "manifest.json").read_text())
                self.assertEqual(manifest["files"][0]["size"], size)
                self.assertEqual(manifest["files"][0]["sha256"], digest)
                for private in (ready / "manifest.json", ready / "files/upload.bin"):
                    self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
                downloaded = self.client.get("/api/download", query_string={"batch": response.json["batch"], "name": "upload.bin"})
                try:
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.data, expected)
                    self.assertEqual(hashlib.sha256(downloaded.data).hexdigest(), digest)
                    self.assertEqual(downloaded.content_length, size)
                finally:
                    downloaded.close()
        self.assertEqual(self.batches("incomplete"), [])
        self.assertTrue(all(item["status"] == "staged" for item in self.client.get("/api/files").json["files"]))

    def test_genuine_file_over_100_mib_is_refused_below_total_request_cap_and_retained(self):
        path = self.payload(100 * MIB + 1, sparse=True)
        response = self.upload(path, name="over-limit.bin")
        self.assertLess(response.request.content_length, 102 * MIB)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json, {"error": "upload_too_large"})
        self.assertEqual(self.batches(), [])
        incomplete = self.batches("incomplete")
        self.assertEqual(len(incomplete), 1)
        retained = incomplete[0] / "files/over-limit.bin"
        self.assertEqual(retained.stat().st_size, 100 * MIB)
        self.assertEqual(stat.S_IMODE(retained.stat().st_mode), 0o600)
        self.assertFalse((incomplete[0] / "manifest.json").exists())

    def test_exact_100_mib_file_is_accepted_and_download_stream_matches_sha256(self):
        path = self.payload(100 * MIB, sparse=True)
        expected = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(MIB), b""):
                expected.update(block)
        response = self.upload(path, name="at-limit.bin")
        self.assertEqual(response.status_code, 201)
        self.assertIs(response.json["cloud_verified"], False)
        downloaded = self.client.get("/api/download", query_string={"batch": response.json["batch"], "name": "at-limit.bin"},
                                     buffered=False)
        actual, size = hashlib.sha256(), 0
        try:
            self.assertEqual(downloaded.status_code, 200)
            for block in downloaded.response:
                actual.update(block)
                size += len(block)
        finally:
            downloaded.close()
        self.assertEqual(size, 100 * MIB)
        self.assertEqual(actual.hexdigest(), expected.hexdigest())
        self.assertEqual(self.batches("incomplete"), [])

    def test_genuine_request_over_102_mib_is_refused_before_staging(self):
        path = self.payload(102 * MIB + 1, sparse=True)
        response = self.upload(path, name="request-too-large.bin")
        self.assertGreater(response.request.content_length, 102 * MIB)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json, {"error": "upload_too_large"})
        self.assertFalse((self.state / "web-inbox").exists())

    def test_oversized_nonfile_field_remains_bounded_before_staging(self):
        path = self.payload(1024)
        response = self.upload(path, fields={"extra": "x" * (128 * 1024 + 1)})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json, {"error": "upload_too_large"})
        self.assertFalse((self.state / "web-inbox").exists())

    def test_eight_parts_work_and_ninth_part_is_rejected(self):
        path = self.payload(122700)
        response = self.upload(path, fields={"field" + str(index): "small" for index in range(7)})
        self.assertEqual(response.status_code, 201)
        before = [batch.name for batch in self.batches()]
        response = self.upload(path, fields={"field" + str(index): "small" for index in range(8)})
        self.assertEqual(response.status_code, 413)
        self.assertEqual([batch.name for batch in self.batches()], before)
        self.assertEqual(self.batches("incomplete"), [])

    def test_medium_upload_still_requires_csrf_and_owner(self):
        path = self.payload(122700)
        self.assertEqual(self.upload(path, csrf=False).status_code, 403)
        self.client.post("/logout", data={"csrf": self.csrf()})
        self.login("viewer", VIEWER_PASSWORD)
        self.assertEqual(self.upload(path).status_code, 403)
        self.assertFalse((self.state / "web-inbox").exists())
