"""Synthetic owner intent and private local channels; no provider or Pi access."""

import copy
import errno
import fcntl
import hashlib
import json
import math
import os
import stat
import tempfile
import threading
import time
import traceback
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import accounts
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_setup as setup
from raspi_cloud_sync_pro.oauth import OAuthError, OwnerProof

ORIGIN = "https://synthetic.example"
SECRET = "PRIVATE-synthetic-client-secret"
ACCESS = "PRIVATE-synthetic-access-token"
REFRESH = "PRIVATE-synthetic-refresh-token"
PASSWORD_HASH = "PRIVATE-synthetic-password-hash"
SESSION = "PRIVATE-synthetic-session-id"


@unittest.skipIf(os.geteuid() == 0, "Private preparation needs an ordinary UID")
class GoogleSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "dashboard"
        self.state.mkdir(mode=0o700)
        self.client = {"client_id": "synthetic.apps.googleusercontent.com", "client_secret": SECRET}
        self.clients = {"schema_version": 1, "public_origin": ORIGIN,
                        "providers": {"google_drive": self.client}}
        self.grant = {
            "schema_version": 1, "provider": "google_drive",
            "client_id_sha256": hashlib.sha256(self.client["client_id"].encode()).hexdigest(),
            "received_at": 100.0, "expires_at": 200.0, "token_type": "Bearer",
            "access_token": ACCESS, "refresh_token": REFRESH,
            "scopes": [destination.GOOGLE_SCOPE],
        }
        self.auth = {"secret": "A" * 64,
                     "users": {"owner": {"role": "owner", "hash": PASSWORD_HASH}}}
        self.proof = OwnerProof("owner", SESSION, hashlib.sha256(PASSWORD_HASH.encode()).hexdigest())
        self.write("auth.json", self.auth)
        self.write("oauth-clients.json", self.clients)
        self.write("oauth-google_drive.json", self.grant)
        self.guard_calls = []

    def write(self, filename, value):
        path = self.state / filename
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    @contextmanager
    def guard(self, proof):
        self.guard_calls.append(proof)
        with accounts._auth_lock(self.state):
            yield

    def request(self, *, now=1000.0, folder="Synthetic Folder", proof=None, guard=None):
        return setup.request_google_setup(self.state, ORIGIN, folder,
                                          self.proof if proof is None else proof,
                                          self.guard if guard is None else guard, now=now)

    @property
    def workflow(self):
        return self.state / setup.WORKFLOW_DIRECTORY

    def current_id(self):
        pointer = json.loads((self.workflow / "current.json").read_text())
        self.assertEqual(set(pointer), {"schema_version", "request_id"})
        self.assertRegex(pointer["request_id"], r"\A[0-9a-f]{32}\Z")
        return pointer["request_id"]

    def request_record(self):
        return json.loads((self.workflow / (self.current_id() + ".request.json")).read_text())

    def receipt(self, **changes):
        record = self.request_record()
        return {
            "schema_version": 1, "provider": "google_drive",
            "request_id": record["request_id"],
            "authorization_sha256": record["authorization_sha256"],
            "rclone_config_sha256": self.expected_config_digest(),
            "folder_name": record["folder_name"], "configuration_matches": True,
            "prepared_at": 1001.0, "enabled": False, "test_required": True,
            "account_verified": False, "folder_exists_verified": False,
            "automatic_deletion": False, **changes,
        }

    def expected_config_digest(self):
        with destination._private_directory(self.state) as root:
            authorization = destination._source_authorization(root, ORIGIN)
        return hashlib.sha256(destination._render_config(authorization)).hexdigest()

    def input_fd(self, value=None, *, raw=None, trailing=b"", length=None):
        if raw is None:
            raw = json.dumps(value).encode()
        packet = (len(raw) if length is None else length).to_bytes(4, "big") + raw + trailing
        self.assertLess(len(packet), 8192)
        read, write = os.pipe()
        os.write(write, packet)
        os.close(write)
        return read

    def record(self, value=None, *, now=1002.0):
        fd = self.input_fd(self.receipt() if value is None else value)
        result = setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), fd, now=now)
        self.assert_closed(fd)
        return result

    def export(self, *, now=1001.0, origin=ORIGIN, request_id=None):
        metadata_read, metadata_write = os.pipe()
        auth_read, auth_write = os.pipe()
        try:
            result = setup.export_requested_google_setup(
                self.state, origin, self.current_id() if request_id is None else request_id,
                metadata_write, auth_write, now=now)
            self.assert_closed(metadata_write)
            self.assert_closed(auth_write)
            metadata = destination._read_frame(metadata_read, time.monotonic() + 1)
            authorization = destination._read_frame(auth_read, time.monotonic() + 1)
            return result, metadata, authorization
        finally:
            os.close(metadata_read)
            os.close(auth_read)
            for fd in (metadata_write, auth_write):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def assert_private(self, public):
        value = json.dumps(public) if isinstance(public, dict) else str(public)
        for private in (ORIGIN, SECRET, ACCESS, REFRESH, PASSWORD_HASH, SESSION,
                        self.client["client_id"], "Synthetic Folder", str(self.state)):
            self.assertNotIn(private, value)
        if self.workflow.exists():
            for path in self.workflow.glob("*.request.json"):
                record = json.loads(path.read_text())
                self.assertNotIn(record["request_id"], value)
                self.assertNotIn(record["authorization_sha256"], value)
        return public

    def assert_status(self, public, state, *, historical=False):
        self.assertEqual(public["preparation_state"], state)
        self.assertEqual(public["historical"], historical)
        self.assertEqual(set(public), {"schema_version", "provider", "preparation_state",
                                     "age_seconds", "historical", "current_worker_state_checked",
                                     "enabled", "test_required", "account_verified",
                                     "folder_exists_verified", "automatic_deletion"})
        self.assertIs(public["current_worker_state_checked"], False)
        for field in ("enabled", "account_verified", "folder_exists_verified", "automatic_deletion"):
            self.assertIs(public[field], False)
        self.assertIs(public["test_required"], True)
        self.assert_private(public)

    def test_request_is_guarded_private_durable_and_live_request_is_idempotent(self):
        self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=999), "not_requested")
        self.assertFalse(self.workflow.exists())
        self.assert_status(self.request(), "requested")
        request_id = self.current_id()
        record = self.request_record()
        self.assertEqual(self.guard_calls, [self.proof])
        self.assertEqual(record["expires_at"], 1600.0)
        for forbidden in (SESSION, PASSWORD_HASH, SECRET, ACCESS, REFRESH, ORIGIN):
            self.assertNotIn(forbidden, json.dumps(record))
        self.assertEqual(stat.S_IMODE(self.workflow.stat().st_mode), 0o700)
        for path in self.workflow.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assert_status(self.request(now=1001), "requested")
        self.assertEqual(self.current_id(), request_id)
        self.assertEqual(len(list(self.workflow.glob("*.request.json"))), 1)

    def test_guard_revocation_is_checked_before_any_workflow_commit(self):
        @contextmanager
        def revoked(proof):
            raise OAuthError("oauth_owner_session_changed")
            yield
        with self.assertRaises(setup.GoogleSetupError) as error:
            self.request(guard=revoked)
        self.assertEqual(error.exception.code, "google_setup_owner_changed")
        self.assertFalse(self.workflow.exists())
        self.assert_private(error.exception)

    def test_current_owner_password_or_role_required_even_if_guard_yields(self):
        for change in ({"hash": "changed"}, {"role": "viewer"}):
            with self.subTest(change=change):
                auth = copy.deepcopy(self.auth)
                auth["users"]["owner"].update(change)
                self.write("auth.json", auth)
                with self.assertRaises(setup.GoogleSetupError) as error:
                    self.request()
                self.assertEqual(error.exception.code, "google_setup_owner_changed")
                self.assertFalse(self.workflow.exists())

    def test_metadata_precedes_and_binds_one_canonical_authorization_snapshot(self):
        self.request()
        result, metadata, authorization = self.export()
        self.assertEqual(set(metadata), {"schema_version", "provider", "request_id",
                                         "folder_name", "authorization_sha256", "rclone_config_sha256"})
        self.assertEqual(metadata["request_id"], self.current_id())
        self.assertEqual(metadata["folder_name"], "Synthetic Folder")
        self.assertEqual(metadata["authorization_sha256"],
                         hashlib.sha256(destination._canonical(authorization)).hexdigest())
        self.assertEqual(metadata["authorization_sha256"], self.request_record()["authorization_sha256"])
        self.assertEqual(metadata["rclone_config_sha256"], self.expected_config_digest())
        self.assertEqual(authorization["client"], self.client)
        self.assertEqual(authorization["authorization"], self.grant)
        self.assertEqual(authorization["source_origin"], ORIGIN)
        self.assert_private(result)

    def test_export_both_hashes_and_credential_frame_use_one_validated_snapshot(self):
        self.request()
        with mock.patch.object(destination, "_source_authorization",
                               wraps=destination._source_authorization) as source:
            _, metadata, bundle = self.export()
        self.assertEqual(source.call_count, 1)
        authorization = destination._validate_bundle(bundle)
        self.assertEqual(metadata["authorization_sha256"],
                         hashlib.sha256(destination._canonical(bundle)).hexdigest())
        self.assertEqual(metadata["rclone_config_sha256"],
                         hashlib.sha256(destination._render_config(authorization)).hexdigest())

    def test_forged_worker_config_and_recomputed_manifest_hash_fail_independent_source_check(self):
        self.request()
        _, metadata, bundle = self.export()
        worker = self.root / "worker"
        worker.mkdir(mode=0o700)
        destination.prepare_google_destination(worker, self.input_fd(bundle), "Synthetic Folder")
        prepared = worker / destination.PREPARATION_DIRECTORY
        config_path = prepared / "rclone.conf"
        manifest_path = prepared / "preparation.json"
        manifest = json.loads(manifest_path.read_text())
        original_authorization_digest = manifest["authorization_sha256"]
        forged_config = config_path.read_bytes().replace(SECRET.encode(), b"changed-valid-client-secret")
        self.assertNotEqual(forged_config, config_path.read_bytes())
        config_path.write_bytes(forged_config)
        forged_config_digest = hashlib.sha256(forged_config).hexdigest()
        manifest["rclone_config_sha256"] = forged_config_digest
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(manifest["authorization_sha256"], original_authorization_digest)
        self.assertEqual(original_authorization_digest, metadata["authorization_sha256"])
        self.assertNotEqual(forged_config_digest, metadata["rclone_config_sha256"])
        with self.assertRaises(setup.GoogleSetupError) as error:
            self.record(self.receipt(rclone_config_sha256=forged_config_digest))
        self.assertEqual(error.exception.code, "google_setup_receipt_invalid")
        self.assertFalse(list(self.workflow.glob("*.receipt.json")))
        self.assertEqual({path.name for path in prepared.iterdir()},
                         {"rclone.conf", "preparation.json"})
        self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1003), "requested")

    def test_historical_receipt_with_altered_config_binding_is_stale_and_sanitized(self):
        self.request()
        self.record()
        path = self.workflow / (self.current_id() + ".receipt.json")
        forged = json.loads(path.read_text())
        forged["rclone_config_sha256"] = "0" * 64
        path.write_text(json.dumps(forged))
        status = setup.google_setup_status(self.state, ORIGIN, now=1003)
        self.assert_status(status, "stale_receipt", historical=True)
        self.assertNotIn(forged["rclone_config_sha256"], json.dumps(status))

    def test_receipt_rechecks_live_wall_clock_after_reading_complete_private_frame(self):
        self.request()
        fd = self.input_fd(self.receipt())
        with mock.patch.object(setup.time, "time", side_effect=[1599.0, 1600.0]):
            with self.assertRaises(setup.GoogleSetupError) as error:
                setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), fd)
        self.assertEqual(error.exception.code, "google_setup_request_expired")
        self.assert_closed(fd)
        self.assertFalse(list(self.workflow.glob("*.receipt.json")))
        fd = self.input_fd(self.receipt(prepared_at=1001.5))
        with mock.patch.object(setup.time, "time", side_effect=[1001.0, 1002.0]):
            status = setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), fd)
        self.assert_status(status, "prepared_untested", historical=True)
        self.assert_closed(fd)

    def test_large_authorization_waits_for_metadata_eof_without_deadlock(self):
        grant = copy.deepcopy(self.grant)
        grant["access_token"] = "a" * 32768
        grant["refresh_token"] = "b" * 16384
        self.write("oauth-google_drive.json", grant)
        self.request()
        meta_read, meta_write = os.pipe()
        auth_read, auth_write = os.pipe()
        errors = []
        def produce():
            try:
                setup.export_requested_google_setup(self.state, ORIGIN, self.current_id(),
                                                    meta_write, auth_write, now=1001)
            except BaseException as error:
                errors.append(error)
        producer = threading.Thread(target=produce)
        producer.start()
        try:
            metadata = destination._read_frame(meta_read, time.monotonic() + 2)
            authorization = destination._read_frame(auth_read, time.monotonic() + 2)
            self.assertEqual(metadata["authorization_sha256"],
                             hashlib.sha256(destination._canonical(authorization)).hexdigest())
        finally:
            os.close(meta_read)
            os.close(auth_read)
            producer.join(3)
        self.assertFalse(producer.is_alive())
        self.assertEqual(errors, [])

    def test_password_change_during_pending_processing_prevents_export_and_receipt(self):
        self.request()
        receipt = self.receipt()
        self.auth["users"]["owner"]["hash"] = "new-password-hash"
        self.write("auth.json", self.auth)
        self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1001), "stale_source")
        with self.assertRaises(setup.GoogleSetupError) as error:
            self.export()
        self.assertEqual(error.exception.code, "google_setup_owner_changed")
        with self.assertRaises(setup.GoogleSetupError):
            self.record(receipt)
        self.assertFalse(list(self.workflow.glob("*.receipt.json")))

    def test_client_secret_origin_grant_and_scope_changes_invalidate_pending_binding(self):
        self.request()
        original = self.request_record()["authorization_sha256"]
        for change in ("client_secret", "origin", "access_token", "refresh_token", "scope"):
            with self.subTest(change=change):
                clients, grant = copy.deepcopy(self.clients), copy.deepcopy(self.grant)
                origin = ORIGIN
                if change == "client_secret":
                    clients["providers"]["google_drive"]["client_secret"] = "changed-secret"
                elif change == "origin":
                    origin = "https://different.example"
                    clients["public_origin"] = origin
                elif change == "scope":
                    grant["scopes"] = ["https://www.googleapis.com/auth/drive"]
                else:
                    grant[change] = "changed-token"
                self.write("oauth-clients.json", clients)
                self.write("oauth-google_drive.json", grant)
                self.assert_status(setup.google_setup_status(self.state, origin, now=1001), "stale_source")
                with self.assertRaises(setup.GoogleSetupError):
                    self.export(origin=origin)
                self.assertEqual(self.request_record()["authorization_sha256"], original)
        self.write("oauth-clients.json", self.clients)
        self.write("oauth-google_drive.json", self.grant)

    def test_expiration_and_explicit_replacement_retain_all_request_history(self):
        self.request()
        first = self.current_id()
        self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1600), "expired")
        with self.assertRaises(setup.GoogleSetupError) as error:
            self.export(now=1600)
        self.assertEqual(error.exception.code, "google_setup_request_expired")
        self.assert_status(self.request(now=1600), "requested")
        second = self.current_id()
        self.assertNotEqual(first, second)
        self.assertTrue((self.workflow / (first + ".request.json")).exists())
        with self.assertRaises(setup.GoogleSetupError) as error:
            self.export(now=1601, request_id=first)
        self.assertEqual(error.exception.code, "google_setup_request_not_current")
        self.request(now=1601, folder="Other Folder")
        self.assertEqual(len(list(self.workflow.glob("*.request.json"))), 3)

    def test_stale_source_can_only_be_replaced_by_a_fresh_guarded_owner_request(self):
        self.request()
        first = self.current_id()
        self.client["client_secret"] = "replacement-secret"
        self.write("oauth-clients.json", self.clients)
        self.assert_status(self.request(now=1002), "requested")
        self.assertNotEqual(self.current_id(), first)
        self.assertTrue((self.workflow / (first + ".request.json")).exists())

    def test_receipt_is_strict_durable_idempotent_and_always_historical(self):
        self.request()
        receipt = self.receipt()
        self.assert_status(self.record(receipt), "prepared_untested", historical=True)
        path = self.workflow / (self.current_id() + ".receipt.json")
        saved = path.read_bytes()
        inode = path.stat().st_ino
        self.assert_status(self.record(receipt, now=1003), "prepared_untested", historical=True)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(path.read_bytes(), saved)
        self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=99999),
                           "prepared_untested", historical=True)
        with self.assertRaises(setup.GoogleSetupError) as error:
            self.record(self.receipt(prepared_at=1002), now=1003)
        self.assertEqual(error.exception.code, "google_setup_receipt_exists")
        self.assertEqual(path.read_bytes(), saved)
        with self.assertRaises(setup.GoogleSetupError) as error:
            self.export()
        self.assertEqual(error.exception.code, "google_setup_already_prepared")

    def test_completed_receipt_becomes_historical_stale_after_owner_or_source_change(self):
        self.request()
        self.record()
        for name in ("owner", "source"):
            with self.subTest(name=name):
                if name == "owner":
                    self.auth["users"]["owner"]["hash"] = "new-password-hash"
                    self.write("auth.json", self.auth)
                else:
                    self.write("auth.json", {"secret": "A" * 64,
                                             "users": {"owner": {"role": "owner", "hash": PASSWORD_HASH}}})
                    self.grant["access_token"] = "replacement-token"
                    self.write("oauth-google_drive.json", self.grant)
                self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1003),
                                   "stale_receipt", historical=True)
                with self.assertRaises(setup.GoogleSetupError):
                    self.export(now=1003)

    def test_forged_receipt_flags_bindings_and_time_never_create_receipt(self):
        self.request()
        changes = [{"enabled": True}, {"account_verified": True}, {"test_required": False},
                   {"folder_exists_verified": True}, {"automatic_deletion": True},
                   {"configuration_matches": False}, {"configuration_matches": 1},
                   {"enabled": 0}, {"authorization_sha256": "0" * 64},
                   {"rclone_config_sha256": "0" * 64}, {"rclone_config_sha256": "F" * 64},
                   {"rclone_config_sha256": None},
                   {"folder_name": "Other Folder"}, {"request_id": "0" * 32},
                   {"provider": "dropbox"}, {"schema_version": True},
                   {"prepared_at": math.nan}, {"prepared_at": math.inf},
                   {"prepared_at": 999}, {"prepared_at": 1003}, {"extra": "PRIVATE-field"}]
        for change in changes:
            with self.subTest(change=change):
                fd = self.input_fd(self.receipt(**change))
                with self.assertRaises(setup.GoogleSetupError) as error:
                    setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), fd, now=1002)
                self.assertEqual(error.exception.code, "google_setup_receipt_invalid")
                self.assert_closed(fd)
                self.assertFalse(list(self.workflow.glob("*.receipt.json")))
                self.assert_private(error.exception)

    def test_receipt_channel_duplicate_deep_oversize_extra_bytes_are_bounded(self):
        self.request()
        cases = [(b'{"duplicate":1,"duplicate":2}', None, b""),
                 (b"[" * 1200 + b"0" + b"]" * 1200, None, b""),
                 (b"", 65537, b""), (b"{}", 500, b""),
                 (json.dumps(self.receipt()).encode(), None, b"x")]
        for raw, length, trailing in cases:
            with self.subTest(length=length, size=len(raw)):
                fd = self.input_fd(raw=raw, length=length, trailing=trailing)
                with self.assertRaises(setup.GoogleSetupError):
                    setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), fd, now=1002)
                self.assert_closed(fd)
        self.assertFalse(list(self.workflow.glob("*.receipt.json")))

    def test_unsafe_state_and_workflow_paths_symlinks_and_permissions_fail_closed(self):
        link = self.root / "dashboard-link"
        link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(setup.GoogleSetupError):
            setup.request_google_setup(link, ORIGIN, "Folder", self.proof, self.guard, now=1000)
        self.assert_status(setup.google_setup_status(link, ORIGIN, now=1000), "unavailable")
        self.workflow.mkdir(mode=0o755)
        with self.assertRaises(setup.GoogleSetupError):
            self.request()
        self.workflow.rmdir()
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        self.workflow.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(setup.GoogleSetupError):
            self.request()
        self.assertEqual(list(outside.iterdir()), [])

    def test_private_source_and_pointer_symlinks_permissions_and_malformed_json_refused(self):
        self.request()
        pointer = self.workflow / "current.json"
        old = pointer.read_bytes()
        for raw in (b'{"schema_version":1,"schema_version":1}', b"[" * 1200 + b"0" + b"]" * 1200):
            pointer.write_bytes(raw)
            self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1001), "invalid")
            with self.assertRaises(setup.GoogleSetupError):
                self.request(now=1001)
        pointer.write_bytes(old)
        pointer.chmod(0o644)
        self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1001), "invalid")
        pointer.chmod(0o600)
        pointer.unlink()
        target = self.root / "pointer-target"
        target.write_bytes(old)
        target.chmod(0o600)
        pointer.symlink_to(target)
        with self.assertRaises(setup.GoogleSetupError):
            self.request(now=1001)
        self.assertTrue(pointer.is_symlink())
        self.assertEqual(target.read_bytes(), old)

    def test_source_unsafe_files_are_not_read_or_repaired(self):
        self.request()
        source = self.state / "oauth-google_drive.json"
        source.chmod(0o644)
        with self.assertRaises(setup.GoogleSetupError):
            self.export()
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o644)
        source.chmod(0o600)
        source.rename(self.state / "grant-retained.json")
        source.symlink_to(self.state / "grant-retained.json")
        with self.assertRaises(setup.GoogleSetupError):
            self.export()
        self.assertTrue(source.is_symlink())

    def test_concurrent_owner_requests_are_serialized_and_do_not_duplicate_intent(self):
        mutex = threading.Lock()
        barrier = threading.Barrier(2)
        results, errors = [], []
        @contextmanager
        def guard(proof):
            with mutex, accounts._auth_lock(self.state):
                yield
        def worker():
            barrier.wait()
            try:
                results.append(self.request(guard=guard))
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(list(self.workflow.glob("*.request.json"))), 1)
        for result in results:
            self.assert_status(result, "requested")

    def test_auth_lock_precedes_workflow_and_busy_state_never_reads_owner_or_source(self):
        self.request()
        with accounts._auth_lock(self.state), mock.patch.object(setup, "_binding", side_effect=AssertionError):
            self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1001), "unavailable")
            with self.assertRaises(setup.GoogleSetupError) as error:
                self.export()
            self.assertEqual(error.exception.code, "google_setup_busy")

    def test_fsync_failure_retains_incomplete_artifacts_and_retry_keeps_them(self):
        original = destination._write_private
        def fail_write(root, filename, payload):
            original(root, filename, payload)
            raise OSError("PRIVATE-synthetic-write-context")
        with mock.patch.object(destination, "_write_private", side_effect=fail_write):
            with self.assertRaises(setup.GoogleSetupError) as error:
                self.request()
        self.assertEqual(error.exception.code, "google_setup_unavailable")
        self.assert_private(error.exception)
        partials = list(self.workflow.glob("*.incomplete"))
        self.assertEqual(len(partials), 1)
        retained = partials[0].read_bytes()
        self.assertFalse((self.workflow / "current.json").exists())
        self.assert_status(self.request(), "requested")
        self.assertEqual(partials[0].read_bytes(), retained)

    def test_post_publication_fsync_failure_reports_failure_and_retry_reuses_record(self):
        real_replace, real_fsync = os.replace, os.fsync
        replaced = []
        def replace(*args, **kwargs):
            result = real_replace(*args, **kwargs)
            replaced.append(True)
            return result
        def fsync(fd):
            if replaced:
                raise OSError("PRIVATE-fsync-context")
            return real_fsync(fd)
        with mock.patch.object(os, "replace", side_effect=replace), \
             mock.patch.object(os, "fsync", side_effect=fsync):
            with self.assertRaises(setup.GoogleSetupError) as error:
                self.request()
        self.assertEqual(error.exception.code, "google_setup_unavailable")
        first = self.current_id()
        self.assert_status(self.request(now=1001), "requested")
        self.assertEqual(self.current_id(), first)
        self.assertEqual(len(list(self.workflow.glob("*.request.json"))), 1)

    def test_failed_receipt_publication_retains_private_partial_without_claiming_prepared(self):
        self.request()
        with mock.patch.object(os, "link", side_effect=OSError("PRIVATE-link-failure")):
            with self.assertRaises(setup.GoogleSetupError):
                self.record()
        partials = list(self.workflow.glob("*.incomplete"))
        self.assertEqual(len(partials), 1)
        self.assertFalse(list(self.workflow.glob("*.receipt.json")))
        self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1002), "requested")
        self.assert_status(self.record(), "prepared_untested", historical=True)
        self.assertTrue(partials[0].exists())

    def test_invalid_partner_consumes_valid_output_but_stdio_and_aliases_remain_open(self):
        self.request()
        for invalid in (0, 1, 2):
            for first in (True, False):
                with self.subTest(invalid=invalid, first=first):
                    read, write = os.pipe()
                    args = (invalid, write) if first else (write, invalid)
                    try:
                        with self.assertRaises(setup.GoogleSetupError):
                            setup.export_requested_google_setup(self.state, ORIGIN, self.current_id(), *args, now=1001)
                        self.assert_closed(write)
                        os.fstat(invalid)
                    finally:
                        os.close(read)
        alias = os.dup(1)
        read, write = os.pipe()
        try:
            with self.assertRaises(setup.GoogleSetupError):
                setup.export_requested_google_setup(self.state, ORIGIN, self.current_id(), alias, write, now=1001)
            os.fstat(alias)
            self.assert_closed(write)
        finally:
            os.close(alias)
            os.close(read)

    def test_output_channels_cannot_alias_each_other_and_valid_aliases_are_consumed(self):
        self.request()
        for same_number in (True, False):
            read, write = os.pipe()
            alias = write if same_number else os.dup(write)
            try:
                with self.assertRaises(setup.GoogleSetupError) as error:
                    setup.export_requested_google_setup(self.state, ORIGIN, self.current_id(), write, alias, now=1001)
                self.assertEqual(error.exception.code, "google_setup_channel_invalid")
                self.assert_closed(write)
                self.assert_closed(alias)
            finally:
                os.close(read)

    def test_no_source_error_writes_any_metadata_or_credentials_and_both_channels_close(self):
        self.request()
        self.grant["refresh_token"] = "changed"
        self.write("oauth-google_drive.json", self.grant)
        reads, writes = zip(os.pipe(), os.pipe())
        try:
            with self.assertRaises(setup.GoogleSetupError):
                setup.export_requested_google_setup(self.state, ORIGIN, self.current_id(), *writes, now=1001)
            for read in reads:
                self.assertEqual(os.read(read, 1), b"")
            for write in writes:
                self.assert_closed(write)
        finally:
            for read in reads:
                os.close(read)

    def test_receipt_timeout_and_export_backpressure_restore_flags_on_accepted_alias(self):
        self.request()
        read, write = os.pipe()
        alias = os.dup(read)
        before = fcntl.fcntl(alias, fcntl.F_GETFL)
        started = time.monotonic()
        try:
            with mock.patch.object(setup, "DEFAULT_TIMEOUT", 0.03):
                with self.assertRaises(setup.GoogleSetupError) as error:
                    setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), read, now=1002)
            self.assertEqual(error.exception.code, "google_handoff_timeout")
            self.assertLess(time.monotonic() - started, 0.5)
            self.assert_closed(read)
            self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), before)
        finally:
            os.close(write)
            os.close(alias)
        meta_read, meta_write = os.pipe()
        auth_read, auth_write = os.pipe()
        alias = os.dup(meta_write)
        before = fcntl.fcntl(alias, fcntl.F_GETFL)
        os.set_blocking(alias, False)
        try:
            while True:
                os.write(alias, b"x" * 4096)
        except BlockingIOError:
            pass
        fcntl.fcntl(alias, fcntl.F_SETFL, before)
        try:
            with mock.patch.object(setup, "DEFAULT_TIMEOUT", 0.03):
                with self.assertRaises(setup.GoogleSetupError) as error:
                    setup.export_requested_google_setup(self.state, ORIGIN, self.current_id(), meta_write, auth_write, now=1001)
            self.assertEqual(error.exception.code, "google_handoff_timeout")
            self.assert_closed(meta_write)
            self.assert_closed(auth_write)
            self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), before)
        finally:
            os.close(alias)
            os.close(meta_read)
            os.close(auth_read)

    def test_receipt_requires_eof_and_private_channel_including_stdio_alias_refusal(self):
        self.request()
        read, write = os.pipe()
        raw = destination._canonical(self.receipt())
        os.write(write, len(raw).to_bytes(4, "big") + raw)
        try:
            with mock.patch.object(setup, "DEFAULT_TIMEOUT", 0.02):
                with self.assertRaises(setup.GoogleSetupError) as error:
                    setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), read, now=1002)
            self.assertEqual(error.exception.code, "google_handoff_timeout")
            self.assert_closed(read)
        finally:
            os.close(write)
        alias = os.dup(0)
        try:
            with self.assertRaises(setup.GoogleSetupError):
                setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), alias, now=1002)
            os.fstat(alias)
        finally:
            os.close(alias)

    def test_regular_channels_root_mixed_uid_invalid_ids_and_bad_folder_fail_safely(self):
        for folder in ("../Folder", "Folder\nsecret", "", "a" * 65):
            with self.assertRaises(setup.GoogleSetupError):
                self.request(folder=folder)
        self.request()
        with mock.patch.object(os, "geteuid", return_value=0):
            with self.assertRaises(setup.GoogleSetupError):
                self.request()
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(setup.GoogleSetupError):
                self.request()
        for request_id in ("../private", "A" * 32, "a" * 31, None):
            with self.assertRaises(setup.GoogleSetupError):
                self.export(request_id=request_id if request_id is not None else False)
        fd = os.open(self.root / "channel", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with self.assertRaises(setup.GoogleSetupError):
                setup.record_google_setup_receipt(self.state, ORIGIN, self.current_id(), fd, now=1002)
            os.fstat(fd)
        finally:
            os.close(fd)

    def test_no_network_subprocess_activation_or_private_public_fields(self):
        before = {name: (self.state / name).read_bytes()
                  for name in ("auth.json", "oauth-clients.json", "oauth-google_drive.json")}
        with mock.patch("subprocess.run", side_effect=AssertionError("process forbidden")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process forbidden")), \
             mock.patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
             mock.patch("os.system", side_effect=AssertionError("shell forbidden")):
            self.assert_status(self.request(), "requested")
            self.export()
            self.assert_status(self.record(), "prepared_untested", historical=True)
            self.assert_status(setup.google_setup_status(self.state, ORIGIN, now=1004),
                               "prepared_untested", historical=True)
        for name, value in before.items():
            self.assertEqual((self.state / name).read_bytes(), value)
        self.assertFalse((self.state / destination.PREPARATION_DIRECTORY).exists())
        private_origin = "PRIVATE-" + "invalid-origin"
        try:
            setup.request_google_setup(self.state, private_origin, "Folder", self.proof, self.guard, now=1000)
        except setup.GoogleSetupError as error:
            rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            self.assertNotIn(private_origin, rendered)
            self.assert_private(error)
        else:
            self.fail("Invalid origin accepted")


if __name__ == "__main__":
    unittest.main()
