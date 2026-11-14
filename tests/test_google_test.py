"""Strict mutable config, private root gate and synthetic Test executor checks."""

import ast
import errno
import fcntl
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_test as tester
from raspi_cloud_sync_pro.google_broker import _validate_config as validate_seed
from raspi_cloud_sync_pro.history import History

REQUEST = "b" * 32
PREPARATION = "a" * 32
SECRET = "PRIVATE-test-client-secret"
ACCESS = "PRIVATE-test-access"
REFRESH = "PRIVATE-test-refresh"
FOLDER_ID = "PrivateCloudFolder123"
SETTINGS = {"remote_name": "rcsp_google", "type": "drive", "scope": "drive.file",
            "client_id": "test.apps.googleusercontent.com", "client_secret": SECRET,
            "root_folder_id": FOLDER_ID}
TOKEN = {"access_token": ACCESS, "refresh_token": REFRESH, "token_type": "Bearer",
         "expiry": "2030-01-01T00:00:00.123456789Z", "expires_in": 3600}


def config(settings=None, token=None):
    settings = SETTINGS if settings is None else settings
    token = TOKEN if token is None else token
    lines = ["[rcsp_google]"] + [key + " = " + value for key, value in settings.items() if key != "remote_name"]
    return ("\n".join(lines) + "\ntoken = " + json.dumps(token) + "\n").encode()


def frame(value, *, trailing=b"", eof=True):
    parent, child = socket.socketpair()
    raw = json.dumps(value, separators=(",", ":")).encode()
    parent.sendall(len(raw).to_bytes(4, "big") + raw + trailing)
    if eof:
        parent.shutdown(socket.SHUT_WR)
    return parent, child.detach()


def release(**changes):
    return {"schema_version": 1, "provider": "google_drive", "purpose": "google_test_wifi_egress",
            "request_id": REQUEST, "worker_pid": os.getpid(), "expires_at": 1005, **changes}


class GoogleTestParserTests(unittest.TestCase):
    def safe(self, value):
        text = str(value)
        for private in (SECRET, ACCESS, REFRESH, FOLDER_ID):
            self.assertNotIn(private, text)

    def test_exact_direct_settings_and_rclone_refresh_shape_have_private_repr(self):
        parsed = tester.validate_google_operational_config(config(), SETTINGS, now=1000, require_fresh=True)
        self.assertEqual(parsed.settings, SETTINGS)
        self.assertEqual(parsed.token, TOKEN)
        self.assertEqual(parsed.settings_sha256, control.google_operational_settings_sha256(SETTINGS))
        self.assertEqual(parsed.config_sha256, hashlib.sha256(config()).hexdigest())
        self.safe(parsed)
        with self.assertRaises(Exception):
            validate_seed(config())  # Immutable preparation validator stays unchanged.

    def test_token_refresh_changes_raw_hash_but_never_stable_settings_binding(self):
        first = tester.validate_google_operational_config(config(), SETTINGS)
        second = tester.validate_google_operational_config(config(token={**TOKEN,
            "access_token": "PRIVATE-rotated", "refresh_token": "PRIVATE-rotated-refresh",
            "expiry": "2030-01-02T00:00:00+00:00"}), SETTINGS)
        self.assertEqual(first.settings_sha256, second.settings_sha256)
        self.assertNotEqual(first.config_sha256, second.config_sha256)
        for name in ("client_id", "client_secret", "root_folder_id"):
            changed = {**SETTINGS, name: "DifferentBoundValue123"}
            with self.subTest(name=name), self.assertRaises(tester.GoogleTestError):
                tester.validate_google_operational_config(config(changed), SETTINGS)

    def test_extra_backend_endpoint_credentials_scope_and_ini_injection_are_refused(self):
        cases = [config().replace(b"type = drive", b"type = alias"),
                 config().replace(b"scope = drive.file", b"scope = drive"),
                 config().replace(b"root_folder_id = " + FOLDER_ID.encode(), b"root_folder_id = root"),
                 b"[DEFAULT]\ntoken_url = https://private.invalid\n" + config(),
                 config() + b"[other]\ntype = local\n", config() + b"scope = drive.file\n"]
        for key in ("token_url", "auth_url", "service_account_file", "service_account_credentials",
                    "env_auth", "team_drive", "alias", "remote", "impersonate"):
            cases.append(config() + (key + " = PRIVATE\n").encode())
        for raw in cases:
            with self.subTest(raw_size=len(raw)), self.assertRaises(tester.GoogleTestError) as error:
                tester.validate_google_operational_config(raw, SETTINGS)
            self.safe(error.exception)

    def test_duplicate_deep_oversized_json_and_bad_token_values_fail_fixed(self):
        cases = [config(token={**TOKEN, "expires_in": True}), config(token={**TOKEN, "expires_in": float("inf")}),
                 config(token={**TOKEN, "token_type": "Basic"}), config(token={**TOKEN, "refresh_token": ""}),
                 config(token={**TOKEN, "id_token": "PRIVATE"}), config(token={**TOKEN, "expiry": "not-time"}),
                 config(token={**TOKEN, "expiry": "2030-02-30T00:00:00Z"}), b"x" * 65537,
                 config().replace(b'"access_token":', b'"access_token":"PRIVATE-duplicate", "access_token":'),
                 config().split(b"token = ")[0] + b"token = " + b"[" * 2000 + b"0" + b"]" * 2000]
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertRaises(tester.GoogleTestError) as error:
                tester.validate_google_operational_config(raw, SETTINGS)
            self.safe(error.exception)

    def test_expired_token_parse_is_distinct_from_successful_refresh_requirement(self):
        raw = config(token={**TOKEN, "expiry": "2000-01-01T00:00:00Z"})
        tester.validate_google_operational_config(raw, SETTINGS)
        with self.assertRaises(tester.GoogleTestError):
            tester.validate_google_operational_config(raw, SETTINGS, now=1000000000, require_fresh=True)
        for stamp in ("2030-01-01T01:00:00+01:00", "2030-01-01T00:00:00Z", "2030-01-01T00:00:00.000000001Z"):
            self.assertEqual(int(tester._expiry(stamp)), 1893456000)

    def test_default_refusal_precedes_any_state_io_or_process(self):
        with mock.patch("os.open", side_effect=AssertionError("No state I/O")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("No process")), \
                mock.patch.object(tester.operational, "create_google_operational_generation", side_effect=AssertionError("No seed")):
            with self.assertRaises(tester.GoogleTestError) as error:
                tester.run_google_test("PRIVATE", "PRIVATE", {})
        self.assertEqual(error.exception.code, "google_test_network_disabled")

    def test_public_projection_never_relays_unvalidated_private_constructor_fields(self):
        outcome = tester.GoogleTestOutcome({"outcome": SECRET}, SECRET)
        self.safe(outcome)
        self.safe(outcome.projection())
        self.assertEqual(outcome.projection()["test_state"], "failed")
        self.assertEqual(outcome.projection()["error_code"], "google_test_failed")

    def test_actual_unprivileged_peer_is_refused_and_accepted_fd_consumed(self):
        if os.geteuid() == 0:
            self.skipTest("This check needs an ordinary Unix peer")
        parent, fd = frame(release())
        try:
            with self.assertRaises(tester.GoogleTestError):
                tester._gate(fd, REQUEST, 1000)
            with self.assertRaises(OSError):
                os.fstat(fd)
        finally:
            parent.close()

    def test_root_authority_frame_binds_pid_request_expiry_exact_schema_and_eof(self):
        bad = [{"worker_pid": True}, {"worker_pid": os.getpid() + 1}, {"request_id": "c" * 32},
               {"expires_at": 1000}, {"expires_at": 1031}, {"expires_at": float("nan")},
               {"schema_version": True}, {"purpose": "allow_anything"}, {"extra": True}]
        for changes in ({}, *bad):
            parent, fd = frame(release(**changes))
            try:
                with mock.patch.object(tester, "_root_peer"):
                    if changes:
                        with self.assertRaises(tester.GoogleTestError):
                            tester._gate(fd, REQUEST, 1000)
                    else:
                        tester._gate(fd, REQUEST, 1000)
                with self.assertRaises(OSError):
                    os.fstat(fd)
            finally:
                parent.close()
        parent, fd = frame(release(), trailing=b"PRIVATE")
        try:
            with mock.patch.object(tester, "_root_peer"), self.assertRaises(tester.GoogleTestError):
                tester._gate(fd, REQUEST, 1000)
        finally:
            parent.close()

    def test_open_writer_deadline_restores_alias_flags_and_consumes_only_accepted_endpoint(self):
        parent, fd = frame(release(), eof=False)
        alias = os.dup(fd)
        before = fcntl.fcntl(alias, fcntl.F_GETFL)
        try:
            with mock.patch.object(tester, "_root_peer"), \
                    mock.patch.object(destination, "_deadline", return_value=time.monotonic() + .03), \
                    self.assertRaises(tester.GoogleTestError):
                tester._gate(fd, REQUEST, 1000)
            self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), before)
            with self.assertRaises(OSError):
                os.fstat(fd)
        finally:
            os.close(alias)
            parent.close()

    def test_pipe_cannot_forge_root_peer_and_invalid_stdio_is_not_consumed(self):
        read, write = os.pipe()
        try:
            with self.assertRaises(tester.GoogleTestError):
                tester._gate(read, REQUEST, 1000)
            with self.assertRaises(OSError):
                os.fstat(read)
            with self.assertRaises(tester.GoogleTestError):
                tester._gate(0, REQUEST, 1000)
            os.fstat(0)
        finally:
            os.close(write)

    def test_folder_json_null_duplicates_shortcuts_and_wrong_types(self):
        name = "RCSP-Test-" + PREPARATION
        good = {"id": FOLDER_ID, "name": name, "mimeType": "application/vnd.google-apps.folder",
                "parents": ["PrivateParentRoot123"]}
        self.assertEqual(tester._folder_matches(b"null\n", name), [])
        self.assertEqual(tester._folder_matches(destination._canonical([good]), name), [good])
        for value in ({}, [good] * 3, [{**good, "id": "root"}], [{**good, "parents": []}],
                      [{**good, "shortcutDetails": {}}], [{**good, "mimeType": "text/plain"}],
                      [{**good, "trashed": True}]):
            with self.subTest(value=value), self.assertRaises((tester.GoogleTestError, ValueError)):
                tester._folder_matches(destination._canonical(value), name)

    def runner(self, deadline):
        runner = object.__new__(tester._Runner)
        runner.executable = sys.executable
        runner.executable_identity = destination._file_identity(Path(sys.executable).lstat())
        runner.deadline = deadline
        runner.config = Path("/nonexistent-private-test-config")
        return runner

    def test_actual_process_clean_environment_and_private_capture(self):
        runner = self.runner(time.monotonic() + 3)
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "PRIVATE-proxy", "RCLONE_CONFIG_PASS": "PRIVATE-password"}):
            raw = runner.run(["-c", "import os,json;print(json.dumps(sorted(os.environ)))"], capture=True)
        names = json.loads(raw)
        self.assertNotIn("HTTPS_PROXY", names)
        self.assertNotIn("RCLONE_CONFIG_PASS", names)
        self.assertIn("GODEBUG", names)

    def test_output_allocation_failure_happens_before_any_process_launch(self):
        runner = self.runner(time.monotonic() + 3)
        with mock.patch.object(tester, "bytearray", side_effect=MemoryError, create=True), \
                mock.patch.object(tester.subprocess, "Popen") as launched:
            with self.assertRaises(MemoryError):
                runner.run(["no-network-command"])
            launched.assert_not_called()

    def test_actual_process_timeout_output_limit_stderr_and_exit_are_sanitized_reaped(self):
        original = subprocess_popen = tester.subprocess.Popen
        processes = []
        def launched(*args, **kwargs):
            child = original(*args, **kwargs)
            processes.append(child)
            return child
        with mock.patch.object(tester.subprocess, "Popen", side_effect=launched):
            for code, timeout in (("import time;time.sleep(2)", .03), ("print('x'*70000)", 2),
                                  ("import sys;sys.stderr.write('PRIVATE-secret')", 2),
                                  ("raise SystemExit(7)", 2)):
                with self.subTest(code=code), self.assertRaises(tester.GoogleTestError) as error:
                    self.runner(time.monotonic() + timeout).run(["-c", code], capture=True)
                self.assertNotIn("PRIVATE-secret", str(error.exception))
        self.assertTrue(all(child.poll() is not None and child.stdout.closed and child.stderr.closed for child in processes))
        self.assertIs(subprocess_popen, original)

    def test_python39_grammar(self):
        ast.parse(Path(tester.__file__).read_text(), feature_version=(3, 9))


@unittest.skipIf(os.geteuid() == 0, "Real private operational files require an ordinary UID")
class GoogleTestExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prepared = self.root / PREPARATION
        self.prepared.mkdir(mode=0o700)
        self.operational = self.root / "operational"
        self.operational.mkdir(mode=0o700)
        client = {key: SETTINGS[key] for key in ("client_id", "client_secret")}
        bundle = {"schema_version": 1, "provider": "google_drive", "source_origin": "https://private-test.example",
            "client": client, "authorization": {"schema_version": 1, "provider": "google_drive",
                "client_id_sha256": hashlib.sha256(client["client_id"].encode()).hexdigest(),
                "received_at": 100, "expires_at": 200, "token_type": "Bearer", "access_token": ACCESS,
                "refresh_token": REFRESH, "scopes": [destination.GOOGLE_SCOPE]}}
        raw = destination._canonical(bundle)
        read, write = os.pipe()
        os.write(write, len(raw).to_bytes(4, "big") + raw)
        os.close(write)
        destination.prepare_google_destination(self.prepared, read, "Private Folder")
        self.proof = self.prepared / destination.PREPARATION_DIRECTORY
        manifest = json.loads((self.proof / "preparation.json").read_bytes())
        fixture = control.google_test_fixture(REQUEST)
        self.request = {"schema_version": 1, "provider": "google_drive", "request_id": REQUEST,
            "action": "test", "requested_at": 1000, "expires_at": 1600, "test": None,
            "preparation": {"request_id": PREPARATION, "folder_name": "Private Folder",
                "authorization_sha256": manifest["authorization_sha256"], "rclone_config_sha256": manifest["rclone_config_sha256"]},
            "fixture": {"sha256": hashlib.sha256(fixture).hexdigest(), "size": len(fixture)}}
        self.original = {path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                         for path in self.proof.iterdir()}

    def execute(self, *, refresh=True, corrupt=False, duplicates=False, tamper_settings=False):
        calls = []
        class FakeRunner:
            def __init__(self, executable, digest, deadline, config):
                self.executable, self.deadline, self.config = str(executable), deadline, config
                self.created = False
                self.data = None
            def check(self):
                if time.monotonic() >= self.deadline:
                    tester._fail("google_test_timeout")
            def run(self, args, *, capture=False):
                calls.append(list(args))
                if args[:2] == ["backend", "query"]:
                    if not self.created:
                        return b"null\n"
                    name = args[3].split("'")[1]
                    record = {"id": FOLDER_ID, "name": name, "mimeType": "application/vnd.google-apps.folder",
                              "parents": ["PrivateParentRoot123"]}
                    return destination._canonical([record] * (2 if duplicates else 1))
                if args[0] == "mkdir":
                    self.created = True
                elif args[0] == "copyto":
                    self.data = Path(args[1]).read_bytes()
                    if tamper_settings:
                        parsed = tester._parse(tester._read_config(self.config), root_required=True)
                        tester._write_config(self.config, {**parsed.settings, "root_folder_id": "WrongFolder123"}, parsed.token)
                elif args[0] == "checksum":
                    assert "--download" in args and "--one-way" in args
                    wanted = Path(args[2]).read_text().split()[0]
                    assert hashlib.sha256(self.data).hexdigest() == wanted
                    if corrupt:
                        tester._fail("google_test_command_failed")
                    if refresh:
                        parsed = tester._parse(tester._read_config(self.config), root_required=True)
                        tester._write_config(self.config, parsed.settings, {**parsed.token,
                            "expiry": "2030-01-01T00:00:00.123456789Z", "expires_in": 3600})
                return None
        parent, fd = frame(release())
        try:
            with mock.patch.object(tester, "_root_peer"), mock.patch.object(tester, "_Runner", FakeRunner), \
                    mock.patch("socket.create_connection", side_effect=AssertionError("No real network")), \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("No real cloud process")):
                result = tester.run_google_test(self.prepared, self.operational, self.request, root_gate_fd=fd,
                    executable="/protected/rclone", executable_sha256="c" * 64, now=1001)
            return result, calls
        finally:
            parent.close()
            with self.assertRaises(OSError) as error:
                os.fstat(fd)
            self.assertEqual(error.exception.errno, errno.EBADF)

    def test_synthetic_full_test_uses_engine_download_and_forced_refresh_keeps_preparation_immutable(self):
        result, calls = self.execute()
        self.assertEqual(result.receipt["outcome"], "verified")
        self.assertFalse(result.receipt["enabled"])
        self.assertTrue(result.receipt["result"]["token_refresh_verified"])
        self.assertEqual([call[0] for call in calls], ["backend", "mkdir", "backend", "copyto", "checksum"])
        self.assertIn("--immutable", calls[3])
        self.assertIn("--download", calls[4])
        self.assertIn("name = 'Private Folder-", calls[0][3])
        self.assertTrue(calls[1][1].startswith("rcsp_google:Private Folder-"))
        for name, original in self.original.items():
            path = self.proof / name
            self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), original)
        generation = self.operational / result.receipt["result"]["generation_id"]
        with History(generation / "test-history.sqlite3") as history:
            jobs = history.jobs()
            self.assertEqual(jobs[0]["state"], "verified")
            self.assertIn("/generation/", jobs[0]["destination"])
            self.assertNotEqual(jobs[0]["destination"], "google_drive_rclone/rcsp_google:")
        for private in (SECRET, ACCESS, REFRESH, FOLDER_ID, str(generation), REQUEST, PREPARATION):
            self.assertNotIn(private, repr(result))
            self.assertNotIn(private, json.dumps(result.projection()))
        provenance = json.loads((generation / "generation.json").read_bytes())
        self.assertFalse(provenance["enabled"])
        self.assertTrue(provenance["test_required"])
        for path in generation.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_corrupt_readback_missing_refresh_or_settings_tamper_never_verify_and_retain_files(self):
        for arguments in ({"corrupt": True}, {"refresh": False}, {"tamper_settings": True}):
            with self.subTest(arguments=arguments), self.assertRaises(tester.GoogleTestError):
                self.execute(**arguments)
        generations = [path for path in self.operational.iterdir() if path.is_dir()]
        self.assertEqual(len(generations), 3)
        self.assertTrue(all((path / "config/rclone.conf").exists() for path in generations))
        self.assertTrue(all((path / "generation.json").exists() for path in generations))

    def test_duplicate_cloud_folder_is_rejected_before_fixture_upload(self):
        with self.assertRaises(tester.GoogleTestError):
            self.execute(duplicates=True)
        self.assertFalse(any(self.operational.rglob("*.ready")))

    def test_request_mismatch_after_gate_refuses_before_generation_or_executable(self):
        self.request["fixture"]["size"] = True
        with mock.patch.object(tester.operational, "create_google_operational_generation", side_effect=AssertionError("No seed")), \
                self.assertRaises(tester.GoogleTestError):
            self.execute()


if __name__ == "__main__":
    unittest.main()
