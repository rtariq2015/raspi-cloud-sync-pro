"""Local synthetic grants and mocked root gates; no account/service/network I/O."""

import errno
import hashlib
import io
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import google_broker as broker
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_setup as setup
from raspi_cloud_sync_pro import accounts
from raspi_cloud_sync_pro.oauth import OwnerProof

REQUEST = "a" * 32
DIGEST = "b" * 64
ORIGIN = "https://device.example"
SECRET = "PRIVATE-broker-client-secret"
ACCESS = "PRIVATE-broker-access-token"
REFRESH = "PRIVATE-broker-refresh-token"


def metadata():
    return {"schema_version": 1, "provider": "google_drive", "request_id": REQUEST,
            "folder_name": "Proposed App Folder", "authorization_sha256": DIGEST,
            "rclone_config_sha256": "e" * 64}


def receipt():
    return {**metadata(), "configuration_matches": True, "prepared_at": time.time() - 1,
            "enabled": False, "test_required": True, "account_verified": False,
            "folder_exists_verified": False, "automatic_deletion": False}


def packet(value):
    raw = destination._canonical(value)
    return len(raw).to_bytes(4, "big") + raw


class Child:
    def __init__(self, code=0, callback=None):
        self.code, self.callback, self.pid = code, callback, 990001

    def wait(self, timeout=None):
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        return self.code

    def poll(self):
        return self.code


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.settings = broker.GoogleBrokerSettings(
            enabled=True, interpreter=Path("/usr/bin/python3.11"),
            source_state=Path("/var/lib/example/dashboard"),
            backup_state=Path("/var/lib/example/backup"), dashboard_user="dashboard",
            backup_user="backup", public_origin=ORIGIN,
            approval_file=Path("/etc/example/approval.json"),
            lock_file=Path("/run/example/broker.lock"), config_path=Path("/etc/example/broker.json"),
            _identity=(1, 2, 3, 4, 5))
        self.source = SimpleNamespace(pw_uid=1001, pw_gid=1001)
        self.backup = SimpleNamespace(pw_uid=1002, pw_gid=1002)

    def assert_public(self, value):
        serialized = json.dumps(value)
        for private in (REQUEST, DIGEST, "Proposed App Folder", ORIGIN, SECRET, ACCESS, REFRESH,
                        "/var/lib/", "/etc/", "dashboard", "backup"):
            self.assertNotIn(private, serialized)
        self.assertIs(value["uploads_enabled"], False)
        self.assertIs(value["account_verified"], False)

    def test_disabled_exits_before_hardware_lock_state_or_subprocess(self):
        with mock.patch.object(broker, "_platform_gate") as gate, \
                mock.patch.object(broker, "_state_directory") as state, \
                mock.patch.object(broker, "_broker_lock") as lock, \
                mock.patch.object(broker, "_spawn") as spawn:
            result = broker.run_google_setup_broker(broker.GoogleBrokerSettings())
        self.assertEqual(result["status"], "disabled")
        for operation in (gate, state, lock, spawn):
            operation.assert_not_called()
        self.assert_public(result)

    def test_settings_schema_default_disabled_and_explicit_enabled(self):
        with mock.patch.object(broker, "_root_json", return_value=({"schema_version": 1}, (1,))):
            self.assertFalse(broker.GoogleBrokerSettings.load("/etc/example.json").enabled)
        data = {key: str(getattr(self.settings, key)) for key in broker._SETTINGS_FIELDS
                if key not in ("schema_version", "enabled")}
        data.update(schema_version=1, enabled=True)
        with mock.patch.object(broker, "_root_json", return_value=(data, (1,))):
            loaded = broker.GoogleBrokerSettings.load("/etc/example.json")
        self.assertTrue(loaded.enabled)
        self.assertEqual(loaded.lock_file, self.settings.lock_file)
        self.assertNotIn("/etc", repr(loaded))

    def test_settings_reject_unknown_fields_false_schema_and_unsafe_values(self):
        invalid = [{"schema_version": True}, {"schema_version": 1, "enabled": 1},
                   {"schema_version": 1, "shell": "arbitrary"},
                   {"schema_version": 1, "enabled": True}]
        full = {key: str(getattr(self.settings, key)) for key in broker._SETTINGS_FIELDS
                if key not in ("schema_version", "enabled")}
        full.update(schema_version=1, enabled=True)
        for key, value in (("interpreter", "relative"), ("source_state", "/a/../b"),
                           ("dashboard_user", "root;true"), ("public_origin", "http://device.example"),
                           ("backup_user", "dashboard"), ("backup_state", str(self.settings.source_state))):
            invalid.append({**full, key: value})
        for data in invalid:
            with self.subTest(data=data), mock.patch.object(broker, "_root_json", return_value=(data, (1,))):
                with self.assertRaises(broker.GoogleBrokerError):
                    broker.GoogleBrokerSettings.load("/etc/example.json")

    def test_real_root_gate_fails_before_writes_as_ordinary_uid(self):
        if os.geteuid() == 0:
            self.skipTest("ordinary UID check")
        with mock.patch.object(broker, "_broker_lock") as lock, mock.patch.object(broker, "_spawn") as spawn:
            result = broker.run_google_setup_broker(self.settings)
        self.assertEqual(result["code"], "google_broker_root_required")
        lock.assert_not_called()
        spawn.assert_not_called()
        self.assert_public(result)

    def test_hardware_requires_exact_model_and_aarch64(self):
        with mock.patch.object(os, "getuid", return_value=0), mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(broker.platform, "system", return_value="Linux"), \
                mock.patch.object(broker.platform, "machine", return_value="aarch64"), \
                mock.patch("builtins.open", mock.mock_open(read_data=b"Raspberry Pi Zero 2 W Rev 1.0\x00")):
            broker._platform_gate()
            with mock.patch.object(broker.platform, "machine", return_value="armv7l"):
                with self.assertRaises(broker.GoogleBrokerError):
                    broker._platform_gate()
            with mock.patch("builtins.open", mock.mock_open(read_data=b"Raspberry Pi Zero 2 W-like board\x00")):
                with self.assertRaises(broker.GoogleBrokerError):
                    broker._platform_gate()

    def test_config_and_approval_gates_precede_state_and_lock(self):
        with mock.patch.object(broker, "_platform_gate"), \
                mock.patch.object(broker.GoogleBrokerSettings, "load", return_value=self.settings), \
                mock.patch.object(broker, "_root_json", return_value=({"schema_version": 1, "google_setup_broker_approved": False}, (1,))), \
                mock.patch.object(broker, "_state_directory") as state, \
                mock.patch.object(broker, "_broker_lock") as lock, mock.patch.object(broker, "_spawn") as spawn:
            result = broker.run_google_setup_broker(self.settings)
        self.assertEqual(result["code"], "google_broker_approval_required")
        for operation in (state, lock, spawn):
            operation.assert_not_called()
        self.assert_public(result)

    def test_changed_config_and_threaded_callers_fail_before_state(self):
        with mock.patch.object(broker, "_platform_gate"), \
                mock.patch.object(broker.GoogleBrokerSettings, "load", return_value=broker.GoogleBrokerSettings()), \
                mock.patch.object(broker, "_state_directory") as state:
            result = broker.run_google_setup_broker(self.settings)
        self.assertEqual(result["code"], "google_broker_configuration_changed")
        state.assert_not_called()
        with mock.patch.object(broker, "_platform_gate"), \
                mock.patch.object(broker.GoogleBrokerSettings, "load", return_value=self.settings), \
                mock.patch.object(broker.threading, "active_count", return_value=2), \
                mock.patch.object(broker, "_root_json") as approval:
            result = broker.run_google_setup_broker(self.settings)
        self.assertEqual(result["code"], "google_broker_single_thread_required")
        approval.assert_not_called()

    def test_spawn_fixed_commands_clean_environment_and_no_target_work_before_exec(self):
        events = []
        with mock.patch.object(broker.subprocess, "Popen") as popen:
            broker._spawn(self.settings, self.backup, ["google-prepare-destination", "--input-fd", "7"],
                          (7, 42))
        args, kwargs = popen.call_args
        self.assertEqual(args[0][:5], [str(self.settings.interpreter), "-I", "-B", "-m", "raspi_cloud_sync_pro"])
        self.assertEqual(kwargs["pass_fds"], (7, 42))
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["cwd"], "/")
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/nonexistent"})
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(kwargs[stream], subprocess.DEVNULL)
        with mock.patch.object(broker, "_drop_identity", side_effect=lambda *a: events.append("drop")), \
                mock.patch.object(broker, "_make_target", side_effect=lambda *a: events.append("mkdir")), \
                mock.patch.object(os, "close", side_effect=lambda *a: events.append("close")):
            kwargs["preexec_fn"]()
        self.assertEqual(events, ["drop"])

    def test_drop_identity_permanent_ids_groups_capabilities_and_no_new_privileges(self):
        events = []
        status = "CapEff:\t0\nCapPrm:\t0\nCapInh:\t0\nCapAmb:\t0\nNoNewPrivs:\t1\n"
        with mock.patch.object(os, "umask"), \
                mock.patch.object(broker, "_prctl", side_effect=lambda *a: events.append(("prctl",) + a)), \
                mock.patch.object(os, "setgroups", side_effect=lambda a: events.append(("groups", a))), \
                mock.patch.object(os, "setresgid", side_effect=lambda *a: events.append(("gid",) + a)), \
                mock.patch.object(os, "setresuid", side_effect=lambda *a: events.append(("uid",) + a)), \
                mock.patch.object(broker, "_clear_capabilities", side_effect=lambda: events.append(("caps",))), \
                mock.patch.object(broker.resource, "setrlimit") as limit, \
                mock.patch.object(os, "getresuid", return_value=(1002, 1002, 1002)), \
                mock.patch.object(os, "getresgid", return_value=(1002, 1002, 1002)), \
                mock.patch.object(os, "getgroups", return_value=[]), \
                mock.patch("builtins.open", return_value=io.StringIO(status)):
            broker._drop_identity(1002, 1002)
        self.assertLess(events.index(("groups", [])), events.index(("gid", 1002, 1002, 1002)))
        self.assertLess(events.index(("gid", 1002, 1002, 1002)), events.index(("uid", 1002, 1002, 1002)))
        self.assertLess(events.index(("uid", 1002, 1002, 1002)), events.index(("caps",)))
        self.assertIn(("prctl", 38, 1), events)
        limit.assert_called_once_with(broker.resource.RLIMIT_CORE, (0, 0))

    def test_metadata_strict_and_receipt_exact_binding(self):
        broker._metadata(metadata(), REQUEST)
        broker._receipt_matches(receipt(), metadata())
        for key, value in (("request_id", "c" * 32), ("folder_name", "other"),
                           ("authorization_sha256", "d" * 64), ("enabled", True),
                           ("rclone_config_sha256", "f" * 64),
                           ("test_required", False), ("prepared_at", float("inf")),
                           ("schema_version", True), ("account_verified", 0)):
            with self.subTest(key=key), self.assertRaises(broker.GoogleBrokerError):
                broker._receipt_matches({**receipt(), key: value}, metadata())
        for value in ({**metadata(), "extra": "x"}, {**metadata(), "folder_name": "../bad"},
                      {**metadata(), "request_id": "c" * 32}, {**metadata(), "authorization_sha256": "bad"}):
            with self.assertRaises((broker.GoogleBrokerError, destination.GoogleDestinationError)):
                broker._metadata(value, REQUEST)

    def run_pair(self, *, meta=None, report=None, exporter_code=0, importer_code=0,
                 inspector_code=0, recorder_code=0):
        calls, recorded = [], []

        def spawn(settings, account, args, fds, **kwargs):
            command = args[0]
            calls.append((command, list(args), fds, kwargs))
            if command == "google-setup-export":
                os.write(fds[0], packet(metadata() if meta is None else meta))
                os.write(fds[1], packet({"private": SECRET}))
                return Child(exporter_code)
            if command == "google-setup-import":
                if not importer_code:
                    # The importer alone consumes the synthetic credential pipe.
                    with destination._channel(os.dup(fds[0]), writing=False) as fd:
                        self.assertEqual(destination._read_frame(fd, time.monotonic() + 1), {"private": SECRET})
                return Child(importer_code)
            if command == "google-setup-receipt":
                if not inspector_code:
                    os.write(fds[0], packet(receipt() if report is None else report))
                return Child(inspector_code)
            if command == "google-setup-record":
                inherited = os.dup(fds[0])

                def read():
                    with destination._channel(inherited, writing=False) as fd:
                        recorded.append(destination._read_frame(fd, time.monotonic() + 1))

                return Child(recorder_code, read)
            self.fail("unexpected command")

        with mock.patch.object(broker, "_current_request", return_value=REQUEST), \
                mock.patch.object(broker, "_spawn", side_effect=spawn):
            result = broker._orchestrate(self.settings, self.source, self.backup, 20, 21)
        return result, calls, recorded

    def test_full_private_preparation_and_record_without_public_identifiers(self):
        result, calls, recorded = self.run_pair()
        self.assertEqual(result["status"], "prepared")
        self.assertEqual([item[0] for item in calls], ["google-setup-export", "google-setup-import",
                                                     "google-setup-receipt", "google-setup-record"])
        self.assertEqual(calls[1][3], {})
        self.assertEqual(calls[1][2][1], 21)
        self.assertEqual(calls[1][1][2], str(self.settings.backup_state))
        self.assertEqual(len(recorded), 1)
        for _, args, _, _ in calls:
            self.assertNotIn(SECRET, " ".join(args))
        self.assert_public(result)

    def test_existing_prepared_reconciles_only_after_fresh_metadata_and_exact_receipt(self):
        result, calls, recorded = self.run_pair(exporter_code=2, importer_code=2)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(len(recorded), 1)
        with self.assertRaises(broker.GoogleBrokerError):
            self.run_pair(exporter_code=2, importer_code=2,
                          report={**receipt(), "authorization_sha256": "c" * 64})

    def test_partial_missing_receipt_and_record_failure_fail_closed(self):
        for kwargs in ({"importer_code": 2, "inspector_code": 2},
                       {"exporter_code": 2}, {"recorder_code": 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(broker.GoogleBrokerError):
                self.run_pair(**kwargs)

    def test_bad_metadata_stops_before_importer(self):
        with self.assertRaises(broker.GoogleBrokerError):
            self.run_pair(meta={**metadata(), "request_id": "c" * 32})

    def test_no_request_is_idle_without_any_process(self):
        with mock.patch.object(broker, "_current_request", return_value=None), \
                mock.patch.object(broker, "_spawn") as spawn:
            result = broker._orchestrate(self.settings, self.source, self.backup, 20, 21)
        spawn.assert_not_called()
        self.assertEqual(result["status"], "idle")
        self.assert_public(result)

    def test_current_pointer_is_bounded_private_exact_and_never_reads_source_auth(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            workflow = state / "google-setup"
            workflow.mkdir(mode=0o700)
            pointer = workflow / "current.json"
            source_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertIsNone(broker._current_request(source_fd, os.geteuid()))
                pointer.write_bytes(destination._canonical({"schema_version": 1, "request_id": REQUEST}))
                pointer.chmod(0o600)
                with mock.patch.object(broker, "_read_private_bytes", wraps=broker._read_private_bytes) as read:
                    self.assertEqual(broker._current_request(source_fd, os.geteuid()), REQUEST)
                self.assertEqual(read.call_args.args[1], "current.json")
                for raw in (b"x" * 257, b'{"schema_version":true,"request_id":"' + REQUEST.encode() + b'"}',
                            b'{"schema_version":1,"request_id":"bad"}',
                            destination._canonical({"schema_version": 1, "request_id": REQUEST, "extra": 1})):
                    pointer.write_bytes(raw)
                    with self.assertRaises(broker.GoogleBrokerError):
                        broker._current_request(source_fd, os.geteuid())
                pointer.chmod(0o640)
                with self.assertRaises(broker.GoogleBrokerError):
                    broker._current_request(source_fd, os.geteuid())
                pointer.unlink()
                pointer.symlink_to("missing")
                with self.assertRaises(OSError):
                    broker._current_request(source_fd, os.geteuid())
            finally:
                os.close(source_fd)

    def test_root_directory_does_not_accept_sticky_writable_parent(self):
        with self.assertRaises(broker.GoogleBrokerError):
            with broker._root_directory(Path("/tmp")):
                pass

    def test_broker_lock_is_exclusive_and_rejects_hardlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            lock_path = Path(temporary) / "broker.lock"

            @contextmanager
            def parent(_path):
                yield parent_fd

            info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1)
            try:
                with mock.patch.object(broker, "_root_directory", side_effect=parent), \
                        mock.patch.object(os, "fstat", return_value=info):
                    with broker._broker_lock(lock_path):
                        with self.assertRaises(broker.GoogleBrokerError) as error:
                            with broker._broker_lock(lock_path):
                                pass
                        self.assertEqual(error.exception.code, "google_broker_busy")
                    info.st_nlink = 2
                    with self.assertRaises(broker.GoogleBrokerError) as error:
                        with broker._broker_lock(lock_path):
                            pass
                    self.assertEqual(error.exception.code, "google_broker_lock_unsafe")
            finally:
                os.close(parent_fd)

    def test_private_frames_are_bounded_unique_and_require_eof(self):
        cases = [(4097).to_bytes(4, "big"), b"\0\0\0\0", packet(metadata()) + b"x",
                 (7).to_bytes(4, "big") + b'{"x":1}',
                 (13).to_bytes(4, "big") + b'{"x":1,"x":2}']
        for index, raw in enumerate(cases):
            fd, writer = os.pipe()
            os.write(writer, raw)
            os.close(writer)
            if index == 3:
                self.assertEqual(broker._receive(fd, time.monotonic() + 1), {"x": 1})
                continue
            with self.assertRaises((broker.GoogleBrokerError, destination.GoogleDestinationError, ValueError)):
                broker._receive(fd, time.monotonic() + 1)
            with self.assertRaises(OSError) as error:
                os.fstat(fd)
            self.assertEqual(error.exception.errno, errno.EBADF)

    def test_private_frame_timeout_closes_consumed_fd(self):
        fd, writer = os.pipe()
        self.addCleanup(os.close, writer)
        with self.assertRaises(destination.GoogleDestinationError):
            broker._receive(fd, time.monotonic() + 0.01)
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_private_pipe_duplicates_low_descriptors_and_closes_originals(self):
        with mock.patch.object(os, "pipe", return_value=(0, 1)), \
                mock.patch.object(broker.fcntl, "fcntl", side_effect=[3, 4]) as duplicate, \
                mock.patch.object(os, "close") as close:
            self.assertEqual(broker._private_pipe(), (3, 4))
        self.assertEqual(duplicate.call_args_list, [mock.call(0, broker.fcntl.F_DUPFD_CLOEXEC, 3),
                                                    mock.call(1, broker.fcntl.F_DUPFD_CLOEXEC, 3)])
        self.assertEqual(close.call_args_list, [mock.call(0), mock.call(1)])

    def test_local_locked_service_accounts_do_not_use_nss_or_commands(self):
        databases = {"/etc/passwd": b"dashboard:x:1001:1001::/nonexistent:/usr/sbin/nologin\n",
                     "/etc/shadow": b"dashboard:!:20000:0:99999:7:::\n"}
        self.assertEqual(len(databases["/etc/shadow"].decode().strip().split(":")), 9)

        @contextmanager
        def root_file(path):
            yield str(path), None

        with mock.patch.object(broker, "_root_file", side_effect=root_file), \
                mock.patch.object(broker, "_read_bytes", side_effect=lambda fd, *_: databases[fd]), \
                mock.patch.object(broker.pwd, "getpwnam") as nss, \
                mock.patch.object(broker.subprocess, "Popen") as command:
            user = broker._service_identity("dashboard")
            self.assertEqual(user.pw_uid, 1001)
            databases["/etc/shadow"] = b"dashboard:UNLOCKED:20000:0:99999:7:::\n"
            with self.assertRaises(broker.GoogleBrokerError):
                broker._service_identity("dashboard")
        nss.assert_not_called()
        command.assert_not_called()

    def test_child_timeout_and_cleanup_only_kill_owned_live_process_group(self):
        child = mock.Mock(pid=12345)
        child.wait.side_effect = subprocess.TimeoutExpired("private", 1)
        with self.assertRaises(broker.GoogleBrokerError):
            broker._wait_child(child, time.monotonic() + 0.01)
        child.poll.return_value = None
        stopped = Child(0)
        with mock.patch.object(os, "killpg") as kill:
            broker._stop_children([child, stopped])
        kill.assert_called_once_with(child.pid, broker.signal.SIGKILL)


@unittest.skipIf(os.geteuid() == 0, "private preparation runs under an ordinary UID")
class PrivateReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / REQUEST
        self.target.mkdir(mode=0o700)
        client = {"client_id": "synthetic.apps.googleusercontent.com", "client_secret": SECRET}
        self.bundle = {"schema_version": 1, "provider": "google_drive", "source_origin": ORIGIN,
                       "client": client, "authorization": {
                           "schema_version": 1, "provider": "google_drive",
                           "client_id_sha256": hashlib.sha256(client["client_id"].encode()).hexdigest(),
                           "received_at": 1000000000.0, "expires_at": 1000003600.5,
                           "token_type": "Bearer", "access_token": ACCESS, "refresh_token": REFRESH,
                           "scopes": [destination.GOOGLE_SCOPE]}}
        fd, writer = os.pipe()
        os.write(writer, packet(self.bundle))
        os.close(writer)
        destination.prepare_google_destination(self.target, fd, "Proposed App Folder")
        self.prepared = self.target / destination.PREPARATION_DIRECTORY

    def export(self):
        read_fd, write_fd = os.pipe()
        try:
            report = broker.export_google_preparation_receipt(self.target, REQUEST, write_fd, now=1234567890.0)
            with destination._channel(read_fd, writing=False) as fd:
                private = destination._read_frame(fd, time.monotonic() + 1)
            read_fd = None
            return report, private
        finally:
            if read_fd is not None:
                os.close(read_fd)

    def write_manifest(self, value):
        path = self.prepared / "preparation.json"
        path.write_bytes(destination._canonical(value))
        path.chmod(0o600)

    def test_valid_receipt_remains_private_and_reports_disabled(self):
        report, private = self.export()
        self.assertTrue(report["receipt_exported"])
        self.assertFalse(report["uploads_enabled"])
        self.assertFalse(report["account_verified"])
        self.assertEqual(private["request_id"], REQUEST)
        self.assertEqual(private["authorization_sha256"], hashlib.sha256(destination._canonical(self.bundle)).hexdigest())
        self.assertEqual(private["prepared_at"], 1234567890.0)
        self.assertIs(private["configuration_matches"], True)
        for secret in (SECRET, ACCESS, REFRESH, ORIGIN, REQUEST, "Proposed App Folder"):
            self.assertNotIn(secret, json.dumps(report))

    def test_disabled_manifest_flags_exact_and_no_extra_fields(self):
        original = json.loads((self.prepared / "preparation.json").read_bytes())
        for key, value in (("enabled", True), ("test_required", False), ("account_verified", True),
                           ("automatic_deletion", True), ("allow_metered", True),
                           ("approved_uplinks", ["wifi", "ethernet"]), ("schema_version", True),
                           ("remote_name", "other"), ("authorization_sha256", "bad"), ("extra", "value")):
            with self.subTest(key=key):
                self.write_manifest({**original, key: value})
                with self.assertRaises(broker.GoogleBrokerError):
                    self.export()
        self.write_manifest(original)

    def test_config_hash_tampering_detected(self):
        path = self.prepared / "rclone.conf"
        path.write_bytes(path.read_bytes().replace(ACCESS.encode(), b"changed-token"))
        with self.assertRaises(broker.GoogleBrokerError):
            self.export()

    def test_coordinated_valid_config_and_manifest_forgery_fails_fresh_source_binding(self):
        path = self.prepared / "rclone.conf"
        original = path.read_bytes()
        expected = {**metadata(), "authorization_sha256": hashlib.sha256(destination._canonical(self.bundle)).hexdigest(),
                    "rclone_config_sha256": hashlib.sha256(original).hexdigest()}
        changed = original.replace(ACCESS.encode(), b"different-valid-token")
        path.write_bytes(changed)
        manifest = json.loads((self.prepared / "preparation.json").read_bytes())
        self.write_manifest({**manifest, "rclone_config_sha256": hashlib.sha256(changed).hexdigest()})
        _, private = self.export()
        self.assertEqual(private["authorization_sha256"], expected["authorization_sha256"])
        self.assertNotEqual(private["rclone_config_sha256"], expected["rclone_config_sha256"])
        with self.assertRaises(broker.GoogleBrokerError):
            broker._receipt_matches(private, expected)

    def test_source_export_worker_inspection_and_independent_source_record_binding(self):
        source = self.root / "dashboard"
        source.mkdir(mode=0o700)
        auth_hash = "synthetic-owner-password-hash"
        fixtures = {"auth.json": {"secret": "s" * 64,
                                  "users": {"owner": {"role": "owner", "hash": auth_hash}}},
                    "oauth-clients.json": {"schema_version": 1, "public_origin": ORIGIN,
                                           "providers": {"google_drive": self.bundle["client"]}},
                    "oauth-google_drive.json": self.bundle["authorization"]}
        for name, value in fixtures.items():
            path = source / name
            path.write_bytes(destination._canonical(value))
            path.chmod(0o600)
        proof = OwnerProof("owner", "synthetic-session", hashlib.sha256(auth_hash.encode()).hexdigest())

        @contextmanager
        def guard(_proof):
            with accounts._auth_lock(source):
                yield

        with mock.patch.object(setup.secrets, "token_hex", return_value=REQUEST):
            setup.request_google_setup(source, ORIGIN, "Proposed App Folder", proof, guard,
                                       now=1234567888.0)
        metadata_reader, metadata_writer = os.pipe()
        auth_reader, auth_writer = os.pipe()
        setup.export_requested_google_setup(source, ORIGIN, REQUEST, metadata_writer, auth_writer,
                                            now=1234567889.0)
        expected = broker._receive(metadata_reader, time.monotonic() + 1)
        with destination._channel(auth_reader, writing=False) as fd:
            transferred = destination._read_frame(fd, time.monotonic() + 1)
        self.assertEqual(hashlib.sha256(destination._canonical(transferred)).hexdigest(), expected["authorization_sha256"])
        _, private = self.export()
        broker._receipt_matches(private, expected)

        def record(value):
            fd, writer = os.pipe()
            os.write(writer, packet(value))
            os.close(writer)
            return setup.record_google_setup_receipt(source, ORIGIN, REQUEST, fd,
                                                     now=1234567891.0)

        result = record(private)
        self.assertEqual(result["preparation_state"], "prepared_untested")
        receipt_path = source / "google-setup" / (REQUEST + ".receipt.json")
        original = receipt_path.read_bytes()
        # Even an otherwise exact private report cannot assert a different valid
        # config: source recorder recomputes rendering independently of broker.
        with self.assertRaises(setup.GoogleSetupError):
            record({**private, "rclone_config_sha256": "f" * 64})
        self.assertEqual(receipt_path.read_bytes(), original)

    def fresh_input(self):
        reader, writer = os.pipe()
        os.write(writer, packet(self.bundle))
        os.close(writer)
        return reader

    def test_post_exec_import_consumes_both_fds_and_never_overwrites(self):
        request_id = "c" * 32
        anchor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        channel = self.fresh_input()
        result = broker.import_requested_google_setup(self.root, request_id, anchor, channel, "Folder")
        self.assertFalse(result["enabled"])
        for fd in (anchor, channel):
            with self.assertRaises(OSError):
                os.fstat(fd)
        config = self.root / request_id / destination.PREPARATION_DIRECTORY / "rclone.conf"
        original = config.read_bytes()
        anchor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        channel = self.fresh_input()
        with self.assertRaises(broker.GoogleBrokerError):
            broker.import_requested_google_setup(self.root, request_id, anchor, channel, "Folder")
        self.assertEqual(config.read_bytes(), original)
        for fd in (anchor, channel):
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_post_exec_import_rejects_mismatched_anchor_without_outside_write(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        anchor = os.open(outside, os.O_RDONLY | os.O_DIRECTORY)
        channel = self.fresh_input()
        self.addCleanup(os.close, anchor)
        self.addCleanup(os.close, channel)
        with self.assertRaises(broker.GoogleBrokerError):
            broker.import_requested_google_setup(self.root, "c" * 32, anchor, channel, "Folder")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.root / ("c" * 32)).exists())

    def test_post_exec_target_symlink_race_does_not_follow_or_write_outside(self):
        request_id = "d" * 32
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        original_mkdir = os.mkdir

        def race(name, *args, **kwargs):
            if name == request_id:
                (self.root / request_id).symlink_to(outside, target_is_directory=True)
                raise FileExistsError()
            return original_mkdir(name, *args, **kwargs)

        anchor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        channel = self.fresh_input()
        with mock.patch.object(os, "mkdir", side_effect=race), self.assertRaises(broker.GoogleBrokerError):
            broker.import_requested_google_setup(self.root, request_id, anchor, channel, "Folder")
        for fd in (anchor, channel):
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertEqual(list(outside.iterdir()), [])

    def test_fixed_direct_drive_schema_even_with_recomputed_manifest_hash(self):
        path = self.prepared / "rclone.conf"
        original = path.read_bytes()
        manifest = json.loads((self.prepared / "preparation.json").read_bytes())
        for replacement in (original.replace(b"type = drive", b"type = alias"),
                            original + b"[second]\ntype = local\n", original.replace(b"scope = drive.file", b"scope = drive"),
                            original.replace(b"client_id = ", b"client_id = ;")):
            path.write_bytes(replacement)
            self.write_manifest({**manifest, "rclone_config_sha256": hashlib.sha256(replacement).hexdigest()})
            with self.assertRaises(broker.GoogleBrokerError):
                self.export()

    def test_symlinks_permissions_and_hardlinks_are_rejected(self):
        config = self.prepared / "rclone.conf"
        config.chmod(0o640)
        with self.assertRaises(broker.GoogleBrokerError):
            self.export()
        config.chmod(0o600)
        os.link(config, self.prepared / "alias")
        with self.assertRaises(broker.GoogleBrokerError):
            self.export()
        (self.prepared / "alias").unlink()
        renamed = self.prepared / "saved"
        config.rename(renamed)
        config.symlink_to(renamed)
        with self.assertRaises(broker.GoogleBrokerError):
            self.export()

    def test_missing_final_retains_partial_and_never_creates_or_overwrites(self):
        retained = self.target / ".google-drive-synthetic.incomplete"
        self.prepared.rename(retained)
        before = sorted(path.name for path in retained.iterdir())
        with self.assertRaises(broker.GoogleBrokerError):
            self.export()
        self.assertFalse(self.prepared.exists())
        self.assertEqual(sorted(path.name for path in retained.iterdir()), before)

    def test_target_creation_is_uid_owned_anchored_and_refuses_symlinks(self):
        backup_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, backup_fd)
        broker._make_target(backup_fd, "c" * 32, os.geteuid())
        self.assertEqual(stat.S_IMODE((self.root / ("c" * 32)).stat().st_mode), 0o700)
        broker._make_target(backup_fd, "c" * 32, os.geteuid())
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (self.root / ("d" * 32)).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            broker._make_target(backup_fd, "d" * 32, os.geteuid())
        self.assertEqual(list(outside.iterdir()), [])

    def test_root_interpreter_and_lock_paths_reject_untrusted_and_symlink_files(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)

        @contextmanager
        def parent(_path):
            yield fd

        executable = self.root / "python"
        executable.write_text("private")
        executable.chmod(0o755)
        linked = self.root / "linked"
        linked.symlink_to(executable)
        with mock.patch.object(broker, "_root_directory", side_effect=parent):
            for path in (executable, linked):
                with self.assertRaises((broker.GoogleBrokerError, OSError)):
                    with broker._root_file(path, executable=True):
                        pass
            with self.assertRaises(broker.GoogleBrokerError):
                with broker._broker_lock(self.root / "lock"):
                    pass


if __name__ == "__main__":
    unittest.main()
