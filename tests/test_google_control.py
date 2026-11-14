"""Real auth locks/private records; synthetic historical reports; no cloud actions."""

import copy
import errno
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import accounts
from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_setup as setup
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.oauth import OAuthError, OwnerProof

ORIGIN = "https://private-control.example"
FOLDER = "Private Control Folder"
FOLDER_ID = "PRIVATE_folder_id_123"
GENERATION = "b" * 32
SECRET = "PRIVATE-control-client-secret"
ACCESS = "PRIVATE-control-access"
REFRESH = "PRIVATE-control-refresh"
PASSWORD = "PRIVATE-control-password-hash"
SESSION = "PRIVATE-control-session"


@unittest.skipIf(os.geteuid() == 0, "Control state belongs to an ordinary service UID")
class GoogleControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "dashboard"
        self.state.mkdir(mode=0o700)
        self.client = {"client_id": "control.apps.googleusercontent.com", "client_secret": SECRET}
        self.auth = {"secret": "x" * 64, "users": {"owner": {"role": "owner", "hash": PASSWORD}}}
        self.grant = {"schema_version": 1, "provider": "google_drive",
                      "client_id_sha256": hashlib.sha256(self.client["client_id"].encode()).hexdigest(),
                      "received_at": 100.0, "expires_at": 200.0, "token_type": "Bearer",
                      "access_token": ACCESS, "refresh_token": REFRESH,
                      "scopes": [destination.GOOGLE_SCOPE]}
        self.write("auth.json", self.auth)
        self.write("oauth-clients.json", {"schema_version": 1, "public_origin": ORIGIN,
                   "providers": {"google_drive": self.client}})
        self.write("oauth-google_drive.json", self.grant)
        self.proof = OwnerProof("owner", SESSION, hashlib.sha256(PASSWORD.encode()).hexdigest())
        self.sessions = {SESSION: self.proof}
        self.csrf_valid = True
        self.guard_calls = []
        setup.request_google_setup(self.state, ORIGIN, FOLDER, self.proof, self.guard, now=100)
        setup_dir = self.state / setup.WORKFLOW_DIRECTORY
        self.preparation_id = json.loads((setup_dir / "current.json").read_text())["request_id"]
        record = json.loads((setup_dir / (self.preparation_id + ".request.json")).read_text())
        with destination._private_directory(self.state) as root:
            _, _, config_digest = setup._authorization(root, ORIGIN)
        self.preparation = {"request_id": self.preparation_id, "folder_name": FOLDER,
                            "authorization_sha256": record["authorization_sha256"],
                            "rclone_config_sha256": config_digest}
        receipt = {"schema_version": 1, "provider": "google_drive", **self.preparation,
                   "configuration_matches": True, "prepared_at": 101,
                   "enabled": False, "test_required": True, "account_verified": False,
                   "folder_exists_verified": False, "automatic_deletion": False}
        setup.record_google_setup_receipt(self.state, ORIGIN, self.preparation_id,
                                          self.input_fd(receipt), now=102)
        self.guard_calls.clear()
        self.original_source = {name: ((self.state / name).read_bytes(), (self.state / name).stat().st_ino)
                                for name in ("auth.json", "oauth-clients.json", "oauth-google_drive.json")}

    def write(self, name, value):
        path = self.state / name
        path.write_bytes(destination._canonical(value))
        path.chmod(0o600)

    @contextmanager
    def guard(self, proof):
        self.guard_calls.append(proof)
        with accounts._auth_lock(self.state):
            auth = accounts.read_auth(self.state)
            user = auth["users"].get(proof.username)
            if (not self.csrf_valid or self.sessions.get(proof.session_id) != proof
                    or user is None or user["role"] != "owner"
                    or hashlib.sha256(user["hash"].encode()).hexdigest() != proof.credential_tag):
                raise OAuthError("oauth_owner_session_changed")
            yield

    @property
    def workflow(self):
        return self.state / control.WORKFLOW_DIRECTORY

    def current_id(self):
        return json.loads((self.workflow / "current.json").read_text())["request_id"]

    def current(self):
        return json.loads((self.workflow / (self.current_id() + ".request.json")).read_text())

    def request(self, action="test", now=1000, **changes):
        args = dict(state=self.state, origin=ORIGIN, action=action, proof=self.proof,
                    commit_guard=self.guard, now=now)
        args.update(changes)
        return control.request_google_control(**args)

    def input_fd(self, value=None, *, raw=None, trailing=b""):
        raw = destination._canonical(value) if raw is None else raw
        read, write = os.pipe()
        packet = len(raw).to_bytes(4, "big") + raw + trailing
        self.assertLess(len(packet), 8192)
        os.write(write, packet)
        os.close(write)
        return read

    def receipt(self, *, outcome=None, completed_at=1001):
        record = self.current()
        action = record["action"]
        outcome = ("verified" if action == "test" else "applied") if outcome is None else outcome
        result = None
        if action == "test":
            fixture = control.google_test_fixture(record["request_id"])
            result = {"generation_id": GENERATION, "folder_id": FOLDER_ID,
                      "operational_config_sha256": "c" * 64,
                      "operational_settings_sha256": self.settings_digest(),
                      "fixture_sha256": hashlib.sha256(fixture).hexdigest(), "fixture_size": len(fixture),
                      "fixture_uploaded": outcome == "verified",
                      "fixture_readback_verified": outcome == "verified",
                      "token_refresh_verified": outcome == "verified"}
        return {"schema_version": 1, "provider": "google_drive", "request_id": record["request_id"],
                "action": action, "preparation": record["preparation"], "test": record["test"],
                "completed_at": completed_at, "outcome": outcome, "result": result,
                "enabled": action == "enable" and outcome == "applied", "automatic_deletion": False}

    def settings_digest(self, **changes):
        return control.google_operational_settings_sha256({"remote_name": destination.REMOTE_NAME,
            "type": "drive", "scope": "drive.file", **self.client,
            "root_folder_id": FOLDER_ID, **changes})

    def record(self, receipt=None, *, now=1002, request_id=None):
        fd = self.input_fd(self.receipt() if receipt is None else receipt)
        try:
            return control.record_google_control_receipt(self.state, ORIGIN,
                self.current_id() if request_id is None else request_id, fd, now=now)
        finally:
            self.assert_closed(fd)

    def export(self, *, now=1001, request_id=None):
        read, write = os.pipe()
        try:
            report = control.export_requested_google_control(self.state, ORIGIN,
                self.current_id() if request_id is None else request_id, write, now=now)
            self.assert_closed(write)
            packet = destination._read_frame(read, time.monotonic() + 1)
            return report, packet
        finally:
            os.close(read)
            try:
                os.close(write)
            except OSError:
                pass

    def admission(self, *, now=1006):
        read, write = os.pipe()
        try:
            report = control.export_google_control_admission(self.state, ORIGIN, write, now=now)
            self.assert_closed(write)
            packet = destination._read_frame(read, time.monotonic() + 1)
            return report, packet
        finally:
            os.close(read)
            try:
                os.close(write)
            except OSError:
                pass

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def assert_safe(self, value):
        exposed = json.dumps(value) if isinstance(value, dict) else str(value)
        for private in (ORIGIN, FOLDER, FOLDER_ID, GENERATION, SECRET, ACCESS, REFRESH,
                        PASSWORD, SESSION, self.client["client_id"], str(self.state), self.preparation_id,
                        self.preparation["authorization_sha256"], self.preparation["rclone_config_sha256"]):
            self.assertNotIn(private, exposed)
        if self.workflow.exists():
            for path in self.workflow.glob("*.request.json"):
                self.assertNotIn(json.loads(path.read_text())["request_id"], exposed)

    def assert_status(self, value, state, *, admitted=False):
        self.assertEqual(value["control_state"], state)
        self.assertIs(value["admission_authorized"], admitted)
        self.assertIs(value["current_worker_state_checked"], False)
        self.assertIs(value["account_verified"], False)
        self.assertIs(value["automatic_deletion"], False)
        self.assert_safe(value)

    def verified_test(self):
        self.request()
        test_id = self.current_id()
        self.assert_status(self.record(), "test_verified")
        return test_id

    def enabled(self):
        test_id = self.verified_test()
        self.assert_status(self.request("enable", now=1003), "enable_requested")
        self.assert_status(self.record(self.receipt(completed_at=1004), now=1005), "enable_recorded", admitted=True)
        return test_id

    def test_old_matching_completed_preparation_supports_new_private_test_intent(self):
        self.assert_status(control.google_control_status(self.state, ORIGIN, now=1000), "not_requested")
        self.assertFalse(self.workflow.exists())
        self.assert_status(self.request(), "test_requested")
        record = self.current()
        self.assertEqual(record["preparation"], self.preparation)
        self.assertEqual(record["expires_at"], 1600)
        self.assertEqual(self.guard_calls, [self.proof])
        for path in [self.workflow] + list(self.workflow.iterdir()):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        for private in (SESSION, PASSWORD, SECRET, ACCESS, REFRESH, ORIGIN):
            self.assertNotIn(private, json.dumps(record))
        first = self.current_id()
        self.request(now=1001)
        self.assertEqual(self.current_id(), first)
        self.assertEqual(len(list(self.workflow.glob("*.request.json"))), 1)

    def test_actual_auth_guard_covers_publication_and_rejects_session_or_csrf_revocation(self):
        original = setup._publish_pointer
        def check_lock(fd, request_id):
            lock_fd = os.open(self.state / "auth.lock", os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(lock_fd)
            return original(fd, request_id)
        with mock.patch.object(setup, "_publish_pointer", side_effect=check_lock):
            self.request()
        current = self.current_id()
        for revoke in ("session", "csrf"):
            with self.subTest(revoke=revoke):
                self.sessions = {} if revoke == "session" else {SESSION: self.proof}
                self.csrf_valid = revoke != "csrf"
                with self.assertRaises(control.GoogleControlError):
                    self.request("disable", now=1001)
                self.assertEqual(self.current_id(), current)

    def test_dashboard_style_guard_preserves_control_error_and_actual_revocation(self):
        @contextmanager
        def wrapping_guard(proof):
            # Match the existing dashboard OAuth guard's error-wrapping policy,
            # while retaining the real file lock and live-session checks above.
            try:
                with self.guard(proof):
                    yield
            except (OSError, ValueError, TypeError, BackupError) as error:
                if isinstance(error, OAuthError):
                    raise
                raise OAuthError("oauth_owner_session_changed") from None
        with self.assertRaises(control.GoogleControlError) as error:
            self.request("enable", commit_guard=wrapping_guard)
        self.assertEqual(error.exception.code, "google_control_test_required")
        self.csrf_valid = False
        with self.assertRaises(control.GoogleControlError) as error:
            self.request("disable", commit_guard=wrapping_guard)
        self.assertEqual(error.exception.code, "google_control_owner_changed")
        self.assertFalse((self.workflow / "current.json").exists())

    def test_export_exact_private_schema_and_no_process_network_or_source_mutation(self):
        self.request()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("no process")), \
                mock.patch("subprocess.run", side_effect=AssertionError("no process")), \
                mock.patch("socket.socket", side_effect=AssertionError("no network")), \
                mock.patch("os.system", side_effect=AssertionError("no shell")):
            report, wire = self.export()
        self.assert_safe(report)
        self.assertEqual(set(wire), {"schema_version", "provider", "request_id", "action",
            "requested_at", "expires_at", "preparation", "test", "fixture"})
        self.assertEqual(wire["preparation"], self.preparation)
        self.assertIsNone(wire["test"])
        self.assertEqual(wire["fixture"], {"sha256": hashlib.sha256(control.google_test_fixture(self.current_id())).hexdigest(),
                                          "size": len(control.google_test_fixture(self.current_id()))})
        for name, before in self.original_source.items():
            self.assertEqual(((self.state / name).read_bytes(), (self.state / name).stat().st_ino), before)

    def test_test_receipt_is_historical_never_enables_and_enable_requires_it(self):
        self.request()
        with self.assertRaises(control.GoogleControlError):
            self.request("enable", now=1001)
        self.assert_status(self.record(), "test_verified")
        self.assertIs(self.admission()[1]["admission_authorized"], False)
        self.assert_status(self.request("enable", now=1003), "enable_requested")
        self.assertIs(self.admission()[1]["admission_authorized"], False)
        _, wire = self.export(now=1004)
        self.assertEqual(wire["test"]["generation_id"], GENERATION)
        self.assertEqual(wire["test"]["folder_id"], FOLDER_ID)
        self.assertEqual(wire["test"]["operational_settings_sha256"], self.settings_digest())
        self.assertIsNone(wire["fixture"])
        self.assert_status(self.record(self.receipt(completed_at=1004), now=1005), "enable_recorded", admitted=True)

    def test_fresh_admission_contains_stable_settings_not_mutable_token_hash_and_no_worker_claim(self):
        self.enabled()
        report, wire = self.admission(now=1000000)
        self.assert_safe(report)
        self.assertIs(wire["admission_authorized"], True)
        self.assertIs(wire["single_admission"], True)
        self.assertEqual(wire["control_request_id"], self.current_id())
        self.assertEqual(wire["test"]["operational_settings_sha256"], self.settings_digest())
        self.assertNotIn("operational_config_sha256", wire["test"])
        self.assert_status(control.google_control_status(self.state, ORIGIN, now=1000000), "enable_recorded", admitted=True)

    def test_stable_settings_hash_changes_for_folder_client_scope_backend_but_not_token_refresh(self):
        expected = self.settings_digest()
        self.assertNotEqual(expected, self.settings_digest(root_folder_id="OtherPrivateFolder123"))
        self.assertNotEqual(expected, self.settings_digest(client_id="other.apps.googleusercontent.com"))
        self.assertNotEqual(expected, self.settings_digest(client_secret="different-secret"))
        for change in ({"scope": "drive"}, {"type": "alias"}, {"remote_name": "other"}, {"token": "PRIVATE-refreshed"}):
            with self.subTest(change=change), self.assertRaises(control.GoogleControlError):
                self.settings_digest(**change)
        # Token belongs outside the canonical stable settings object entirely.
        self.assertEqual(expected, self.settings_digest())

    def test_failed_test_cannot_enable_and_explicit_retry_retains_old_records(self):
        self.request()
        old = self.current_id()
        self.assert_status(self.record(self.receipt(outcome="failed")), "test_failed")
        with self.assertRaises(control.GoogleControlError):
            self.request("enable", now=1003)
        self.request("test", now=1003)
        self.assertNotEqual(self.current_id(), old)
        self.assertTrue((self.workflow / (old + ".receipt.json")).exists())
        self.assertTrue((self.workflow / (old + ".request.json")).exists())

    def test_disable_supersedes_immediately_without_any_google_readiness(self):
        self.enabled()
        old_enable = self.current_id()
        (self.state / "oauth-clients.json").unlink()
        (self.state / "oauth-google_drive.json").unlink()
        self.assert_status(self.request("disable", now=1006), "disable_requested")
        first = self.current_id()
        self.assertNotEqual(first, old_enable)
        self.assertIs(self.admission(now=1007)[1]["admission_authorized"], False)
        self.assertIsNone(self.current()["preparation"])
        self.assertIsNone(self.current()["test"])
        self.assert_status(self.request("disable", now=1007), "disable_requested")
        self.assertEqual(self.current_id(), first)
        self.assert_status(self.record(self.receipt(completed_at=1007), now=1008), "disable_recorded")
        self.assert_status(self.request("disable", now=1000000), "disable_recorded")
        self.assertEqual(self.current_id(), first)

    def test_enable_and_test_become_stale_after_password_role_or_source_change(self):
        self.enabled()
        original_auth = copy.deepcopy(self.auth)
        for auth in ({**self.auth, "users": {"owner": {"role": "owner", "hash": "changed"}}},
                     {**self.auth, "users": {"owner": {"role": "viewer", "hash": PASSWORD}}}):
            self.write("auth.json", auth)
            self.assert_status(control.google_control_status(self.state, ORIGIN, now=1006), "stale_binding")
            with self.assertRaises(control.GoogleControlError):
                self.admission()
        self.write("auth.json", original_auth)
        self.write("oauth-google_drive.json", {**self.grant, "access_token": "PRIVATE-new-source-token"})
        self.assert_status(control.google_control_status(self.state, ORIGIN, now=1006), "stale_binding")
        with self.assertRaises(control.GoogleControlError):
            self.admission()

    def test_new_test_or_disable_denies_old_enable_admission_and_old_receipts(self):
        self.enabled()
        old_id = self.current_id()
        old_receipt = self.receipt(completed_at=1004)
        self.request("test", now=1006)
        self.assertIs(self.admission(now=1007)[1]["admission_authorized"], False)
        with self.assertRaises(control.GoogleControlError):
            self.record(old_receipt, now=1007, request_id=old_id)
        self.request("disable", now=1008)
        with self.assertRaises(control.GoogleControlError):
            self.request("enable", now=1009)

    def test_test_freshness_and_request_expiry_are_bounded(self):
        self.request()
        first = self.current_id()
        with self.assertRaises(control.GoogleControlError):
            self.export(now=1600)
        with self.assertRaises(control.GoogleControlError):
            self.record(self.receipt(completed_at=1001), now=1600)
        self.assert_status(control.google_control_status(self.state, ORIGIN, now=1600), "expired")
        self.request(now=1601)
        self.assertNotEqual(self.current_id(), first)
        self.record(self.receipt(completed_at=1602), now=1603)
        self.assert_status(control.google_control_status(self.state, ORIGIN, now=2202), "test_expired")
        with self.assertRaises(control.GoogleControlError):
            self.request("enable", now=2202)
        expired_test = self.current_id()
        self.assert_status(self.request("test", now=2203), "test_requested")
        self.assertNotEqual(self.current_id(), expired_test)

    def test_receipt_ingestion_rechecks_real_wall_clock_and_current_source(self):
        self.request()
        request_id = self.current_id()
        receipt = self.receipt()
        fd = self.input_fd(receipt)
        with mock.patch.object(setup.time, "time", side_effect=[1001, 1600]):
            with self.assertRaises(control.GoogleControlError):
                control.record_google_control_receipt(self.state, ORIGIN, request_id, fd)
        self.assert_closed(fd)
        self.assertFalse((self.workflow / (request_id + ".receipt.json")).exists())
        original_read = destination._read_frame
        def source_changes_during_input(fd, deadline):
            result = original_read(fd, deadline)
            self.write("oauth-google_drive.json", {**self.grant, "refresh_token": "PRIVATE-revoked-binding"})
            return result
        with mock.patch.object(destination, "_read_frame", side_effect=source_changes_during_input):
            with self.assertRaises(control.GoogleControlError):
                self.record(receipt)
        self.assertFalse((self.workflow / (request_id + ".receipt.json")).exists())

    def test_enable_receipt_rechecks_test_freshness_and_retries_only_fresh_failed_enable(self):
        self.verified_test()
        self.request("enable", now=1500)
        receipt = self.receipt(completed_at=1501)
        with self.assertRaises(control.GoogleControlError):
            self.record(receipt, now=1601)
        self.assertIs(self.admission(now=1601)[1]["admission_authorized"], False)
        self.record(self.receipt(outcome="failed", completed_at=1501), now=1502)
        failed_id = self.current_id()
        self.assert_status(self.request("enable", now=1503), "enable_requested")
        self.assertNotEqual(self.current_id(), failed_id)
        self.assertTrue((self.workflow / (failed_id + ".receipt.json")).exists())

    def test_modified_historical_test_receipt_revokes_existing_enable_admission(self):
        test_id = self.enabled()
        path = self.workflow / (test_id + ".receipt.json")
        receipt = json.loads(path.read_text())
        receipt["result"]["operational_config_sha256"] = "d" * 64
        self.write(str(path.relative_to(self.state)), receipt)
        self.assert_status(control.google_control_status(self.state, ORIGIN, now=1006), "stale_binding")
        with self.assertRaises(control.GoogleControlError):
            self.admission()

    def test_test_receipt_exact_fixture_generation_folder_refresh_and_settings_binding(self):
        self.request()
        original = self.receipt()
        changes = [{"fixture_sha256": "d" * 64}, {"fixture_size": True},
                   {"fixture_readback_verified": False}, {"fixture_uploaded": False},
                   {"token_refresh_verified": False}, {"generation_id": "../escape"},
                   {"folder_id": "root"}, {"folder_id": "bad/path"},
                   {"operational_settings_sha256": "d" * 64},
                   {"operational_config_sha256": None}]
        for change in changes:
            with self.subTest(change=change):
                receipt = copy.deepcopy(original)
                receipt["result"].update(change)
                with self.assertRaises(control.GoogleControlError):
                    self.record(receipt)
        for change in ({"enabled": True}, {"automatic_deletion": True},
                       {"extra": False}, {"preparation": None}, {"completed_at": 999}):
            with self.subTest(change=change), self.assertRaises(control.GoogleControlError):
                self.record({**original, **change})
        self.assertEqual(len(list(self.workflow.glob("*.receipt.json"))), 0)

    def test_receipt_no_replace_and_idempotency(self):
        self.request()
        receipt = self.receipt()
        self.record(receipt)
        path = self.workflow / (self.current_id() + ".receipt.json")
        before = path.read_bytes(), path.stat().st_ino
        self.assert_status(self.record(receipt, now=1003), "test_verified")
        conflicting = copy.deepcopy(receipt)
        conflicting["result"]["generation_id"] = "d" * 32
        with self.assertRaises(control.GoogleControlError):
            self.record(conflicting, now=1003)
        self.assertEqual((path.read_bytes(), path.stat().st_ino), before)
        with self.assertRaises(control.GoogleControlError):
            self.export(now=1003)

    def test_publish_failure_retains_orphan_and_private_incomplete_no_current_admission(self):
        original_write = destination._write_private
        def partial(fd, name, payload):
            original_write(fd, name, payload)
            raise OSError(errno.ENOSPC, "PRIVATE failure text")
        with mock.patch.object(destination, "_write_private", side_effect=partial):
            with self.assertRaises(control.GoogleControlError) as error:
                self.request()
        self.assert_safe(error.exception)
        self.assertFalse((self.workflow / "current.json").exists())
        retained = list(self.workflow.glob("*.incomplete"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(stat.S_IMODE(retained[0].stat().st_mode), 0o600)
        self.assertIs(self.admission()[1]["admission_authorized"], False)

    def test_disable_pointer_fsync_failure_retains_committed_denial_and_history(self):
        self.enabled()
        previous = self.current_id()
        original_publish = setup._publish_pointer
        def committed_then_failed(fd, request_id):
            original_publish(fd, request_id)
            raise OSError(errno.EIO, "PRIVATE fsync failure after publication")
        with mock.patch.object(setup, "_publish_pointer", side_effect=committed_then_failed):
            with self.assertRaises(control.GoogleControlError):
                self.request("disable", now=1006)
        self.assertNotEqual(self.current_id(), previous)
        self.assertEqual(self.current()["action"], "disable")
        self.assertIs(self.admission(now=1007)[1]["admission_authorized"], False)
        self.assertTrue((self.workflow / (previous + ".receipt.json")).exists())

    def test_request_collision_never_replaces_immutable_record_or_pointer(self):
        self.request()
        request_id = self.current_id()
        path = self.workflow / (request_id + ".request.json")
        before = path.read_bytes(), path.stat().st_ino
        with mock.patch.object(control.secrets, "token_hex", side_effect=[request_id, "f" * 32]):
            with self.assertRaises(control.GoogleControlError):
                self.request("disable", now=1001)
        self.assertEqual(self.current_id(), request_id)
        self.assertEqual((path.read_bytes(), path.stat().st_ino), before)
        self.assertTrue(list(self.workflow.glob("*.incomplete")))

    def test_permissive_umask_and_real_auth_control_locks_preserve_privacy(self):
        old_umask = os.umask(0)
        try:
            self.request()
        finally:
            os.umask(old_umask)
        for path in [self.workflow] + list(self.workflow.iterdir()):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        for path in (self.state / "auth.lock", self.workflow / "workflow.lock"):
            fd = os.open(path, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assert_status(control.google_control_status(self.state, ORIGIN, now=1001), "unavailable")
                with self.assertRaises(control.GoogleControlError):
                    self.export()
            finally:
                os.close(fd)

    def test_missing_preparation_blocks_test_but_disable_has_no_preparation_dependency(self):
        pointer = self.state / setup.WORKFLOW_DIRECTORY / "current.json"
        pointer.unlink()
        with self.assertRaises(control.GoogleControlError):
            self.request()
        self.assert_status(self.request("disable"), "disable_requested")
        self.assertIs(self.admission()[1]["admission_authorized"], False)

    def test_symlinks_insecure_files_and_malformed_pointer_fail_closed(self):
        self.request()
        request_id = self.current_id()
        pointer = self.workflow / "current.json"
        original = pointer.read_bytes()
        for contents in (b'{"schema_version":1,"request_id":"bad"}', b'{"schema_version":1,"schema_version":1}'):
            pointer.write_bytes(contents)
            self.assertFalse(control.google_control_status(self.state, ORIGIN, now=1001)["admission_authorized"])
            with self.assertRaises(control.GoogleControlError):
                self.export(request_id=request_id)
        pointer.write_bytes(original)
        pointer.chmod(0o644)
        with self.assertRaises(control.GoogleControlError):
            self.export()
        pointer.chmod(0o600)
        pointer.unlink()
        outside = self.state / "outside-pointer.json"
        outside.write_bytes(original)
        outside.chmod(0o600)
        pointer.symlink_to(outside)
        with self.assertRaises(control.GoogleControlError):
            self.request("disable", now=1001)
        self.assertEqual(outside.read_bytes(), original)

    def test_private_frame_validation_consumes_accepted_fd_without_echoing_payload(self):
        self.request()
        fd = self.input_fd(raw=b'{"PRIVATE-secret":true}', trailing=b"x")
        with self.assertRaises(control.GoogleControlError) as error:
            control.record_google_control_receipt(self.state, ORIGIN, self.current_id(), fd, now=1002)
        self.assert_closed(fd)
        self.assert_safe(error.exception)
        regular = os.open(self.state / "auth.json", os.O_RDONLY)
        try:
            with self.assertRaises(control.GoogleControlError):
                control.record_google_control_receipt(self.state, ORIGIN, self.current_id(), regular, now=1002)
            os.fstat(regular)  # An invalid endpoint was never accepted/consumed.
        finally:
            os.close(regular)


if __name__ == "__main__":
    unittest.main()
