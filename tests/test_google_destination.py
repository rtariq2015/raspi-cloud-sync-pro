"""Google preparation uses synthetic credentials, private temp roots and local FDs."""

import configparser
import copy
import errno
import fcntl
import hashlib
import json
import os
import pty
import socket
import stat
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import google_destination as google

SECRET = "PRIVATE-test-client-secret"
ACCESS = "PRIVATE-test-access-token"
REFRESH = "PRIVATE-test-refresh-token"
ORIGIN = "https://device.example"


@unittest.skipIf(
    os.geteuid() == 0, "Preparation acceptance requires an ordinary service UID"
)
class GoogleDestinationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dashboard = self.root / "dashboard"
        self.worker = self.root / "worker"
        self.dashboard.mkdir(mode=0o700)
        self.worker.mkdir(mode=0o700)
        self.client = {
            "client_id": "test-client.apps.googleusercontent.com",
            "client_secret": SECRET,
        }
        self.authorization = {
            "schema_version": 1,
            "provider": "google_drive",
            "client_id_sha256": hashlib.sha256(
                self.client["client_id"].encode()
            ).hexdigest(),
            "received_at": 1000000000.0,
            "expires_at": 1000003600.5,
            "token_type": "Bearer",
            "access_token": ACCESS,
            "refresh_token": REFRESH,
            "scopes": [google.GOOGLE_SCOPE],
        }
        self.clients = {
            "schema_version": 1,
            "public_origin": ORIGIN,
            "providers": {"google_drive": self.client},
        }
        self.write_json(self.dashboard / "oauth-clients.json", self.clients)
        self.write_json(self.dashboard / "oauth-google_drive.json", self.authorization)
        # A trap: export must never open application authentication state.
        (self.dashboard / "auth.json").write_text("DO-NOT-OPEN-OR-CHANGE")
        (self.dashboard / "auth.json").chmod(0)
        self.addCleanup((self.dashboard / "auth.json").chmod, 0o600)

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def bundle(self):
        return {
            "schema_version": 1,
            "provider": "google_drive",
            "source_origin": ORIGIN,
            "client": copy.deepcopy(self.client),
            "authorization": copy.deepcopy(self.authorization),
        }

    def input_fd(self, value=None, *, raw=None, header=None, trailing=b""):
        if raw is None:
            raw = json.dumps(self.bundle() if value is None else value).encode()
        packet = (
            (len(raw) if header is None else header).to_bytes(4, "big") + raw + trailing
        )
        # Invalid oversize lengths need only their header; normal fixtures < PIPE_BUF.
        self.assertLess(len(packet), 8192)
        read_fd, write_fd = os.pipe()
        os.write(write_fd, packet)
        os.close(write_fd)
        return read_fd

    def prepare(self, value=None, **kwargs):
        fd = self.input_fd(value)
        return google.prepare_google_destination(
            self.worker, fd, "Raspi Cloud Sync Pro", **kwargs
        )

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def assert_sanitized(self, value):
        serialized = str(value) + repr(value)
        if isinstance(value, dict):
            serialized += json.dumps(value)
        for private in (SECRET, ACCESS, REFRESH, self.client["client_id"], ORIGIN):
            self.assertNotIn(private, serialized)

    def assert_disabled(self, result):
        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "provider": "google_drive",
                "preparation_state": "prepared_untested",
                "enabled": False,
                "test_required": True,
                "account_verified": False,
                "folder_exists_verified": False,
                "automatic_deletion": False,
                "approved_uplinks": ["wifi"],
                "allow_metered": False,
            },
        )
        self.assert_sanitized(result)

    def test_export_validates_binding_sends_one_bounded_frame_and_consumes_endpoint(
        self,
    ):
        before = {
            name: (self.dashboard / name).read_bytes()
            for name in ("oauth-clients.json", "oauth-google_drive.json")
        }
        read_fd, write_fd = os.pipe()
        try:
            report = google.export_google_authorization(
                self.dashboard, ORIGIN, write_fd
            )
            self.assert_closed(write_fd)
            packet = os.read(read_fd, 65540)
            self.assertEqual(int.from_bytes(packet[:4], "big"), len(packet[4:]))
            self.assertEqual(json.loads(packet[4:]), self.bundle())
            self.assertEqual(os.read(read_fd, 1), b"")
        finally:
            os.close(read_fd)
        self.assertEqual(
            report,
            {
                "provider": "google_drive",
                "authorization_exported": True,
                "uploads_enabled": False,
            },
        )
        self.assert_sanitized(report)
        for name, contents in before.items():
            self.assertEqual((self.dashboard / name).read_bytes(), contents)
        self.assertEqual((self.dashboard / "auth.json").stat().st_mode & 0o777, 0)

    def test_prepare_is_private_direct_drive_without_network_or_process_execution(self):
        with mock.patch('subprocess.run', side_effect=AssertionError('process forbidden')), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('process forbidden')), \
             mock.patch('socket.socket', side_effect=AssertionError('socket creation forbidden')), \
             mock.patch('os.system', side_effect=AssertionError('shell forbidden')), \
             mock.patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            result = self.prepare()
        self.assert_disabled(result)
        target = self.worker / google.PREPARATION_DIRECTORY
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        for name in ("rclone.conf", "preparation.json"):
            path = target / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())
        config = configparser.ConfigParser(interpolation=None)
        config.read(target / "rclone.conf")
        self.assertEqual(config.sections(), [google.REMOTE_NAME])
        remote = config[google.REMOTE_NAME]
        self.assertEqual(
            set(remote), {"type", "scope", "client_id", "client_secret", "token"}
        )
        self.assertEqual(remote["type"], "drive")
        self.assertEqual(remote["scope"], "drive.file")
        token = json.loads(remote["token"])
        self.assertEqual(token["refresh_token"], REFRESH)
        self.assertEqual(token["expiry"], "2001-09-09T02:46:40.500000Z")
        preparation = json.loads((target / "preparation.json").read_text())
        self.assertEqual(preparation["folder_name"], "Raspi Cloud Sync Pro")
        self.assertEqual(
            preparation["rclone_config_sha256"],
            hashlib.sha256((target / "rclone.conf").read_bytes()).hexdigest(),
        )
        self.assertNotIn(preparation["rclone_config_sha256"], json.dumps(result))
        self.assertNotIn("auth.json", os.listdir(self.worker))

    def test_export_rejects_wrong_client_scope_or_missing_refresh_before_any_bytes(
        self,
    ):
        for mutation in ("client", "scope", "refresh"):
            with self.subTest(mutation=mutation):
                auth = copy.deepcopy(self.authorization)
                if mutation == "client":
                    auth["client_id_sha256"] = "0" * 64
                if mutation == "scope":
                    auth["scopes"] = ["https://www.googleapis.com/auth/drive"]
                if mutation == "refresh":
                    auth["refresh_token"] = ""
                self.write_json(self.dashboard / "oauth-google_drive.json", auth)
                read_fd, write_fd = os.pipe()
                try:
                    with self.assertRaises(google.GoogleDestinationError) as error:
                        google.export_google_authorization(
                            self.dashboard, ORIGIN, write_fd
                        )
                    self.assertEqual(os.read(read_fd, 1), b"")
                    self.assert_closed(write_fd)
                    self.assert_sanitized(error.exception)
                finally:
                    os.close(read_fd)

    def test_invalid_imports_never_create_preparation(self):
        cases = []
        for kind in (
            "client",
            "scope",
            "refresh",
            "extra",
            "version",
            "provider",
            "nan",
            "huge",
            "newline",
            "unicode",
        ):
            value = self.bundle()
            if kind == "client":
                value["authorization"]["client_id_sha256"] = "f" * 64
            if kind == "scope":
                value["authorization"]["scopes"] = [
                    google.GOOGLE_SCOPE,
                    google.GOOGLE_SCOPE,
                ]
            if kind == "refresh":
                value["authorization"]["refresh_token"] = ""
            if kind == "extra":
                value["command"] = "PRIVATE-command"
            if kind == "version":
                value["schema_version"] = True
            if kind == "provider":
                value["provider"] = "alias"
            if kind == "nan":
                value["authorization"]["expires_at"] = float("nan")
            if kind == "huge":
                value["authorization"]["expires_at"] = 10**300
            if kind == "newline":
                value["authorization"]["access_token"] = "PRIVATE\n[other]"
            if kind == "unicode":
                value["authorization"]["refresh_token"] = "PRIVATE\u2028token"
            cases.append((kind, value))
        for kind, value in cases:
            with self.subTest(kind=kind), \
                 self.assertRaises(google.GoogleDestinationError) as error:
                self.prepare(value)
            self.assert_sanitized(error.exception)
            self.assertEqual(list(self.worker.iterdir()), [])

    def test_malformed_duplicate_deep_unicode_and_trailing_data_are_sanitized(self):
        valid = json.dumps(self.bundle()).encode()
        for raw, trailing in (
            (b"PRIVATE-invalid-json", b""),
            (b'{"PRIVATE-key":1,"PRIVATE-key":2}', b""),
            (b"[" * 2000 + b"]" * 2000, b""),
            (b'"PRIVATE-\xff"', b""),
            (valid, b"x"),
        ):
            with self.subTest(raw_kind=len(raw)):
                fd = self.input_fd(raw=raw, trailing=trailing)
                with self.assertRaises(google.GoogleDestinationError) as error:
                    google.prepare_google_destination(self.worker, fd, "Backups")
                self.assert_sanitized(error.exception)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assert_closed(fd)
                self.assertEqual(list(self.worker.iterdir()), [])

    def test_oversize_and_truncated_frames_are_bounded(self):
        for raw, header in ((b"", google.MAX_HANDOFF_BYTES + 1), (b"{}", 20), (b"", 0)):
            fd = self.input_fd(raw=raw, header=header)
            with self.assertRaises(google.GoogleDestinationError):
                google.prepare_google_destination(
                    self.worker, fd, "Backups", timeout=0.05
                )
            self.assert_closed(fd)
            self.assertEqual(list(self.worker.iterdir()), [])

    def test_ini_injection_is_rejected_and_json_tokens_cannot_add_sections(self):
        for value in (
            "PRIVATE\n[other]",
            "PRIVATE\r\ntype=alias",
            "PRIVATE #comment",
            "PRIVATE;comment",
            " PRIVATE",
            "PRIVATE[section]",
        ):
            bundle = self.bundle()
            bundle["client"]["client_secret"] = value
            with self.subTest(value=value), \
                 self.assertRaises(google.GoogleDestinationError) as error:
                self.prepare(bundle)
            self.assert_sanitized(error.exception)
        bundle = self.bundle()
        bundle["authorization"]["access_token"] = 'PRIVATE-quote";[literal]'
        self.prepare(bundle)
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.worker / google.PREPARATION_DIRECTORY / "rclone.conf")
        self.assertEqual(config.sections(), [google.REMOTE_NAME])
        self.assertEqual(
            json.loads(config[google.REMOTE_NAME]["token"])["access_token"],
            bundle["authorization"]["access_token"],
        )

    def test_existing_any_target_is_not_replaced(self):
        target = self.worker / google.PREPARATION_DIRECTORY
        target.write_text("KEEP")
        with self.assertRaises(google.GoogleDestinationError) as error:
            self.prepare()
        self.assertEqual(error.exception.code, "google_preparation_exists")
        self.assertEqual(target.read_text(), "KEEP")
        target.unlink()
        target.mkdir(mode=0o700)
        with self.assertRaises(google.GoogleDestinationError):
            self.prepare()
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        target.rmdir()
        target.symlink_to(self.dashboard, target_is_directory=True)
        with self.assertRaises(google.GoogleDestinationError):
            self.prepare()
        self.assertTrue(target.is_symlink())

    def test_atomic_publication_refuses_concurrent_empty_target_and_retains_partial(
        self,
    ):
        publish = google._publish_directory

        def race(root_fd, incomplete):
            os.mkdir(google.PREPARATION_DIRECTORY, mode=0o700, dir_fd=root_fd)
            return publish(root_fd, incomplete)

        with mock.patch.object(google, '_publish_directory', side_effect=race), \
             self.assertRaises(google.GoogleDestinationError) as error:
            self.prepare()
        self.assertEqual(error.exception.code, "google_preparation_exists")
        target = self.worker / google.PREPARATION_DIRECTORY
        self.assertEqual(list(target.iterdir()), [])
        partials = list(self.worker.glob("*.incomplete"))
        self.assertEqual(len(partials), 1)
        self.assertEqual(
            {p.name for p in partials[0].iterdir()},
            {"rclone.conf", "preparation.json"},
        )

    def test_second_file_failure_retains_private_config_without_publication(self):
        write = google._write_private

        def fail_second(fd, filename, payload):
            if filename == "preparation.json":
                raise OSError("PRIVATE-filesystem-error")
            return write(fd, filename, payload)

        with mock.patch.object(google, '_write_private', side_effect=fail_second), \
             self.assertRaises(google.GoogleDestinationError) as error:
            self.prepare()
        self.assert_sanitized(error.exception)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertFalse((self.worker / google.PREPARATION_DIRECTORY).exists())
        partials = list(self.worker.glob("*.incomplete"))
        self.assertEqual(len(partials), 1)
        self.assertEqual(stat.S_IMODE(partials[0].stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((partials[0] / "rclone.conf").stat().st_mode), 0o600
        )

    def test_fsync_failure_retains_partial_and_never_claims_success(self):
        with mock.patch.object(google.os, 'fsync', side_effect=OSError('PRIVATE-disk-error')), \
             self.assertRaises(google.GoogleDestinationError) as error:
            self.prepare()
        self.assert_sanitized(error.exception)
        self.assertEqual(len(list(self.worker.glob("*.incomplete"))), 1)
        self.assertFalse((self.worker / google.PREPARATION_DIRECTORY).exists())

    def test_interruption_retains_data_and_closes_consumed_fd(self):
        write = google._write_private
        fd = self.input_fd()

        def interrupt(directory, filename, payload):
            if filename == "preparation.json":
                raise KeyboardInterrupt()
            return write(directory, filename, payload)

        with mock.patch.object(google, '_write_private', side_effect=interrupt), \
             self.assertRaises(KeyboardInterrupt):
            google.prepare_google_destination(self.worker, fd, "Backups")
        self.assert_closed(fd)
        self.assertEqual(len(list(self.worker.glob("*.incomplete"))), 1)
        self.assertFalse((self.worker / google.PREPARATION_DIRECTORY).exists())

    def test_bad_leaf_or_ancestor_permissions_and_symlinks_are_not_changed(self):
        self.worker.chmod(0o750)
        with self.assertRaises(google.GoogleDestinationError):
            self.prepare()
        self.assertEqual(stat.S_IMODE(self.worker.stat().st_mode), 0o750)
        self.worker.chmod(0o700)
        link = self.root / "linked-worker"
        link.symlink_to(self.worker, target_is_directory=True)
        fd = self.input_fd()
        with self.assertRaises(google.GoogleDestinationError):
            google.prepare_google_destination(link, fd, "Backups")
        self.root.chmod(0o777)
        with self.assertRaises(google.GoogleDestinationError):
            self.prepare()
        self.root.chmod(0o700)
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(self.root, target_is_directory=True)
        fd = self.input_fd()
        with self.assertRaises(google.GoogleDestinationError):
            google.prepare_google_destination(linked_parent / "worker", fd, "Backups")

    def test_wrong_state_ownership_is_refused(self):
        real_fstat = google.os.fstat
        worker_identity = self.worker.stat().st_ino

        def other_owner(fd):
            info = real_fstat(fd)
            if info.st_ino == worker_identity:
                values = list(info)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return info

        with mock.patch.object(google.os, 'fstat', side_effect=other_owner), \
             self.assertRaises(google.GoogleDestinationError):
            self.prepare()
        self.assertEqual(list(self.worker.iterdir()), [])

    def test_source_private_permissions_symlinks_and_changed_identity_are_refused(self):
        source = self.dashboard / "oauth-google_drive.json"
        source.chmod(0o640)
        self.assert_export_refused()
        source.chmod(0o600)
        moved = self.dashboard / "retained.json"
        source.rename(moved)
        source.symlink_to(moved)
        self.assert_export_refused()
        source.unlink()
        moved.rename(source)
        original = google._source_authorization
        real_stat = google.os.stat

        def swapped(name, *args, **kwargs):
            if name == "oauth-google_drive.json" and kwargs.get("dir_fd") is not None:
                info = real_stat(name, *args, **kwargs)
                values = list(info)
                values[1] += 1
                return os.stat_result(values)
            return real_stat(name, *args, **kwargs)

        with mock.patch.object(google.os, "stat", side_effect=swapped):
            self.assert_export_refused()
        self.assertIs(original, google._source_authorization)

    def assert_export_refused(self):
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaises(google.GoogleDestinationError) as error:
                google.export_google_authorization(self.dashboard, ORIGIN, write_fd)
            self.assertEqual(os.read(read_fd, 1), b"")
            self.assert_sanitized(error.exception)
        finally:
            os.close(read_fd)

    def test_origin_mismatch_and_source_extra_keys_are_refused_before_bytes(self):
        self.clients["public_origin"] = "https://other.example"
        self.write_json(self.dashboard / "oauth-clients.json", self.clients)
        self.assert_export_refused()
        self.clients["public_origin"] = ORIGIN
        self.clients["command"] = "PRIVATE-command"
        self.write_json(self.dashboard / "oauth-clients.json", self.clients)
        self.assert_export_refused()

    def test_source_origin_is_private_canonical_and_changes_authorization_binding(self):
        first_bundle = self.bundle()
        second_bundle = self.bundle()
        second_bundle["source_origin"] = "https://other.example"
        first = google._canonical(google._validate_bundle(first_bundle)._bundle())
        second = google._canonical(google._validate_bundle(second_bundle)._bundle())
        self.assertNotEqual(
            hashlib.sha256(first).digest(), hashlib.sha256(second).digest()
        )
        self.assertEqual(
            google._render_config(google._validate_bundle(first_bundle)),
            google._render_config(google._validate_bundle(second_bundle)),
        )
        for origin in (
            "https://device.example\nPRIVATE",
            "https://device.example\t",
            "https://dévice.example",
            "https://user:PRIVATE@device.example",
            "http://device.example",
            "https://device.example/path",
            "https://device.example/",
            "https://device.example:0",
            "https://device.example:PRIVATE",
            "https://bad host.example",
            "https://@device.example",
            "https://device.example?",
            "https://device.example#",
        ):
            bundle = self.bundle()
            bundle["source_origin"] = origin
            with self.subTest(origin_type=type(origin).__name__), \
                 self.assertRaises(google.GoogleDestinationError) as error:
                self.prepare(bundle)
            self.assert_sanitized(error.exception)
            self.assertEqual(list(self.worker.iterdir()), [])

    def test_exception_chain_never_discloses_invalid_hostname(self):
        bundle = self.bundle()
        bundle["source_origin"] = "https://PRIVATE-origin!invalid.example"
        try:
            self.prepare(bundle)
        except google.GoogleDestinationError as error:
            formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            self.assertNotIn("PRIVATE-origin", formatted)
            self.assertTrue(error.__suppress_context__)
        else:
            self.fail("Invalid origin was accepted")

    def test_unsafe_folder_names_refuse_without_files(self):
        for name in (
            "../Backups",
            "Backups/Other",
            "-option",
            "Backups\nPRIVATE",
            ".hidden",
            "x" * 65,
            " Backups",
            "Backups ",
        ):
            with self.subTest(name=name):
                fd = self.input_fd()
                with self.assertRaises(google.GoogleDestinationError):
                    google.prepare_google_destination(self.worker, fd, name)
                self.assertEqual(list(self.worker.iterdir()), [])

    def test_standard_fds_and_aliases_refused_without_closing(self):
        for standard in (0, 1, 2):
            with self.subTest(standard=standard), \
                 self.assertRaises(google.GoogleDestinationError):
                google.export_google_authorization(self.dashboard, ORIGIN, standard)
            os.fstat(standard)
        alias = os.dup(1)
        try:
            with self.assertRaises(google.GoogleDestinationError):
                google.export_google_authorization(self.dashboard, ORIGIN, alias)
            os.fstat(alias)
        finally:
            os.close(alias)

    def test_regular_tty_named_fifo_and_wrong_direction_refused_untouched(self):
        path = self.root / "ordinary"
        regular = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        master, slave = pty.openpty()
        read_fd, write_fd = os.pipe()
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        try:
            for fd in (regular, master, slave, read_fd, fifo_fd):
                with self.subTest(fd=fd), \
                     self.assertRaises(google.GoogleDestinationError):
                    google.export_google_authorization(self.dashboard, ORIGIN, fd)
                os.fstat(fd)
            with self.assertRaises(google.GoogleDestinationError):
                google.prepare_google_destination(self.worker, write_fd, "Backups")
            os.fstat(write_fd)
        finally:
            for fd in (regular, master, slave, read_fd, write_fd, fifo_fd):
                os.close(fd)

    def test_connected_unix_stream_can_export_and_import_but_no_inet_or_datagrams(self):
        first, second = socket.socketpair()
        output_fd = first.detach()
        try:
            google.export_google_authorization(self.dashboard, ORIGIN, output_fd)
            raw = second.recv(65540)
            self.assertEqual(json.loads(raw[4:]), self.bundle())
            self.assertEqual(second.recv(1), b"")
        finally:
            second.close()
        first, second = socket.socketpair()
        input_fd = first.detach()
        payload = json.dumps(self.bundle()).encode()
        try:
            second.sendall(len(payload).to_bytes(4, "big") + payload)
            second.shutdown(socket.SHUT_WR)
            self.assert_disabled(
                google.prepare_google_destination(self.worker, input_fd, "Backups")
            )
        finally:
            second.close()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as inet, \
             self.assertRaises(google.GoogleDestinationError):
            google.export_google_authorization(self.dashboard, ORIGIN, inet.fileno())
        first, second = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            with self.assertRaises(google.GoogleDestinationError):
                google.export_google_authorization(
                    self.dashboard, ORIGIN, first.fileno()
                )
        finally:
            first.close()
            second.close()

    def test_timeout_eof_and_epipe_restore_flags_through_retained_alias(self):
        read_fd, write_fd = os.pipe()
        alias = os.dup(read_fd)
        initial = fcntl.fcntl(alias, fcntl.F_GETFL)
        try:
            with self.assertRaises(google.GoogleDestinationError) as error:
                google.prepare_google_destination(
                    self.worker, read_fd, "Backups", timeout=0.03
                )
            self.assertEqual(error.exception.code, "google_handoff_timeout")
            self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), initial)
            self.assert_closed(read_fd)
        finally:
            os.close(alias)
            os.close(write_fd)
        read_fd, write_fd = os.pipe()
        alias = os.dup(write_fd)
        initial = fcntl.fcntl(alias, fcntl.F_GETFL)
        os.close(read_fd)
        try:
            with self.assertRaises(google.GoogleDestinationError) as error:
                google.export_google_authorization(self.dashboard, ORIGIN, write_fd)
            self.assertEqual(error.exception.code, "google_handoff_write_failed")
            self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), initial)
            self.assert_closed(write_fd)
        finally:
            os.close(alias)

    def test_sender_must_finish_and_expired_access_stays_disabled(self):
        raw = json.dumps(self.bundle()).encode()
        read_fd, write_fd = os.pipe()
        os.write(write_fd, len(raw).to_bytes(4, "big") + raw)
        try:
            with self.assertRaises(google.GoogleDestinationError) as error:
                google.prepare_google_destination(
                    self.worker, read_fd, "Backups", timeout=0.03
                )
            self.assertEqual(error.exception.code, "google_handoff_timeout")
        finally:
            os.close(write_fd)
        self.assert_disabled(self.prepare())
        self.assert_sanitized(google._validate_bundle(self.bundle()))

    def test_backpressure_write_timeout_is_bounded_and_restores_flags(self):
        self.authorization["access_token"] = "P" * 32768
        self.authorization["refresh_token"] = "R" * 16384
        self.write_json(self.dashboard / "oauth-google_drive.json", self.authorization)
        read_fd, write_fd = os.pipe()
        alias = os.dup(write_fd)
        initial = fcntl.fcntl(alias, fcntl.F_GETFL)
        try:
            # F_SETPIPE_SZ is not exposed by Python 3.9. Fill the ordinary
            # pipe instead, then leave one page for an actual partial write.
            os.set_blocking(write_fd, False)
            filled = 0
            for _ in range(512):
                try:
                    filled += os.write(write_fd, b"\0" * 4096)
                except BlockingIOError:
                    break
            else:
                self.fail("Synthetic pipe did not reach backpressure within 2 MiB")
            self.assertEqual(len(os.read(read_fd, 4096)), 4096)
            fcntl.fcntl(write_fd, fcntl.F_SETFL, initial)
            with self.assertRaises(google.GoogleDestinationError) as error:
                google.export_google_authorization(
                    self.dashboard, ORIGIN, write_fd, timeout=0.03
                )
            self.assertEqual(error.exception.code, "google_handoff_timeout")
            self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), initial)
            self.assert_closed(write_fd)
            os.set_blocking(read_fd, False)
            remaining = 0
            while True:
                try:
                    remaining += len(os.read(read_fd, 8192))
                except BlockingIOError:
                    break
            self.assertGreater(remaining, filled - 4096)
        finally:
            os.close(alias)
            os.close(read_fd)
            try:
                os.close(write_fd)
            except OSError:
                pass  # The exporter normally consumed the accepted descriptor.


class IdentityRefusalTests(unittest.TestCase):
    def test_timeout_values_are_always_fixed_errors_without_channel_access(self):
        for timeout in (10**400, float("nan"), float("inf"), -1, 0, True, "PRIVATE"):
            with self.subTest(timeout_type=type(timeout).__name__), \
                 mock.patch.object(google, '_unprivileged'), \
                 mock.patch.object(google, '_channel', side_effect=AssertionError('No FD access')):
                with self.assertRaises(google.GoogleDestinationError) as error:
                    google.export_google_authorization(
                        "/private", ORIGIN, 9, timeout=timeout
                    )
                self.assertEqual(error.exception.code, "google_handoff_timeout_invalid")

    def test_root_and_mixed_uid_are_refused_before_source_or_channel_access(self):
        for uid, effective in ((0, 0), (1000, 0), (0, 1000), (1000, 1001)):
            with self.subTest(uid=uid, effective=effective), \
                 mock.patch.object(google.os, 'getuid', return_value=uid), \
                 mock.patch.object(google.os, 'geteuid', return_value=effective), \
                 mock.patch.object(google, '_channel', side_effect=AssertionError('No FD access')), \
                 mock.patch.object(google, '_private_directory', side_effect=AssertionError('No source access')):
                for operation in (
                    lambda: google.export_google_authorization("/private", ORIGIN, 9),
                    lambda: google.prepare_google_destination("/private", 9, "Backups"),
                ):
                    with self.assertRaises(google.GoogleDestinationError) as error:
                        operation()
                    self.assertEqual(
                        error.exception.code,
                        "google_preparation_requires_unprivileged_identity",
                    )


if __name__ == "__main__":
    unittest.main()
