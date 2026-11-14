"""Credential-free bootstrap tests; no real provider, Pi or systemd operations."""

import ast
import errno
import fcntl
import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import cli
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_test_executor as executor


def binding(phase="go"):
    return {"schema_version": 1, "provider": "google_drive", "phase": phase, "worker_pid": os.getpid(),
            "request_id": "a" * 32, "nonce": "b" * 32, "dns_ipv4": "192.168.4.1", "loopback_port": 40217}


def frame(value):
    raw = json.dumps(value, separators=(",", ":")).encode()
    return len(raw).to_bytes(4, "big") + raw


class GoogleTestExecutorTests(unittest.TestCase):
    def pair(self):
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.setblocking(False)
        second.setblocking(False)
        return first, second

    def test_python39_grammar_and_fixed_official_url(self):
        ast.parse(Path(executor.__file__).read_text(), feature_version=(3, 9))
        self.assertEqual(executor.OFFICIAL_URL, "https://raw.githubusercontent.com/rclone/rclone/v1.76.0/COPYING")
        self.assertEqual(executor.FD_NAMES, {"gate", "request", "receipt", "canary"})

    def test_duplex_bounded_frames_do_not_require_intermediate_eof(self):
        sender, receiver = self.pair()
        executor.canary_write_frame(sender.fileno(), binding(), time.monotonic() + 1)
        executor.canary_write_frame(sender.fileno(), binding("ack"), time.monotonic() + 1)
        self.assertEqual(executor.canary_read_frame(receiver.fileno(), time.monotonic() + 1), binding())
        self.assertEqual(executor.canary_read_frame(receiver.fileno(), time.monotonic() + 1), binding("ack"))

    def test_frame_invalid_json_duplicate_deep_and_disconnect(self):
        invalid = [b"\0\0\0\0", (4097).to_bytes(4, "big"), b"\0\0\0\x0d{\"x\":1,\"x\":2}",
                   b"\0\0\0\x01\xff", frame([]), b"\0\0\0\x05{}"]
        nested = ("[" * 1200 + "0" + "]" * 1200).encode()
        invalid.append(len(nested).to_bytes(4, "big") + nested)
        for raw in invalid:
            with self.subTest(raw_size=len(raw)):
                sender, receiver = self.pair()
                sender.sendall(raw)
                sender.shutdown(socket.SHUT_WR)
                with self.assertRaises(executor.GoogleTestExecutorError):
                    executor.canary_read_frame(receiver.fileno(), time.monotonic() + 1)

    def test_read_eof_and_backpressure_have_one_deadline(self):
        first, second = self.pair()
        start = time.monotonic()
        with self.assertRaises(executor.GoogleTestExecutorError):
            executor.canary_read_frame(second.fileno(), start + .025)
        self.assertLess(time.monotonic() - start, .5)
        with self.assertRaises(executor.GoogleTestExecutorError):
            executor._eof(second.fileno(), time.monotonic() + .025)
        second.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        first.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        while True:
            try:
                first.send(b"x" * 4096)
            except BlockingIOError:
                break
        with self.assertRaises(destination.GoogleDestinationError):
            executor.canary_write_frame(first.fileno(), binding(), time.monotonic() + .025)

    def test_eof_rejects_trailing_and_concatenated_frames(self):
        for raw in (b"x", frame(binding())):
            sender, receiver = self.pair()
            sender.sendall(raw)
            sender.shutdown(socket.SHUT_WR)
            with self.assertRaises(executor.GoogleTestExecutorError):
                executor._eof(receiver.fileno(), time.monotonic() + 1)

    def test_binding_strict_numeric_types_nonce_pid_dns_and_port(self):
        self.assertEqual(executor._binding(binding(), "go"), binding())
        bad = [{"schema_version": True}, {"worker_pid": True}, {"worker_pid": os.getpid() + 1},
               {"phase": "ack"}, {"request_id": "A" * 32}, {"nonce": "../bad"},
               {"dns_ipv4": "127.0.0.1"}, {"dns_ipv4": "::1"}, {"dns_ipv4": "0.0.0.0"},
               {"dns_ipv4": "224.1.1.1"}, {"dns_ipv4": "169.254.1.1"}, {"dns_ipv4": "999.1.2.3"},
               {"loopback_port": True}, {"loopback_port": 53}, {"loopback_port": 65536}, {"extra": "private"}]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises((executor.GoogleTestExecutorError, ValueError)):
                executor._binding({**binding(), **changes}, "go")

    def test_real_unprivileged_peer_and_tcp_are_not_root_authority(self):
        first, second = self.pair()
        peer = executor._peer(first.fileno())
        self.assertEqual(peer[1:], (os.getuid(), os.getgid()))
        with tempfile.TemporaryFile() as regular:
            with self.assertRaises((OSError, executor.GoogleTestExecutorError)):
                executor._peer(regular.fileno())
        # No socket creation occurs while wrapping/inspecting inherited FDs.
        with mock.patch.object(socket, "socket", wraps=socket.socket) as constructor:
            executor._peer(second.fileno())
        self.assertEqual(constructor.call_args.kwargs.keys(), {"fileno"})

    def test_default_missing_activation_refuses_before_state_or_process(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(executor, "_sandbox", side_effect=AssertionError("No sandbox or state")), \
                mock.patch.object(executor.google_test, "_Runner", side_effect=AssertionError("No executable")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("No process")):
            with self.assertRaises(executor.GoogleTestExecutorError):
                executor.run_google_test_executor("/private/prepared", "/private/operational", "/pinned/rclone", "a" * 64)

    def activation_child(self, changes=None, *, alias=False, extra=False, nonroot=False):
        # Other Google tests may have helper threads. Isolate the subsequent
        # descriptor-remapping fork in a fresh, single-threaded interpreter.
        script = ("import json,sys; from test_google_test_executor import GoogleTestExecutorTests; "
                  "value=json.loads(sys.argv[1]); GoogleTestExecutorTests()._activation_child_impl(**value)")
        root = Path(__file__).resolve().parent.parent
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                       "PYTHONPATH": str(root / "src") + os.pathsep + str(root / "tests")}
        result = subprocess.run([sys.executable, "-B", "-c", script,
                                 json.dumps({"changes": changes, "alias": alias, "extra": extra, "nonroot": nonroot})],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environment, timeout=5, close_fds=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def _activation_child_impl(self, changes=None, *, alias=False, extra=False, nonroot=False):
        """Test actual FD3..6 in an isolated child, without changing runner FDs."""
        endpoints = [socket.socketpair() for _ in range(4)]
        process = os.fork()
        if process == 0:
            try:
                copies = [fcntl.fcntl(pair[1].fileno(), fcntl.F_DUPFD_CLOEXEC, 20) for pair in endpoints]
                for index, fd in enumerate(copies):
                    os.dup2(fd, 3 + index)
                for value in os.listdir("/proc/self/fd"):
                    fd = int(value)
                    if fd > 6:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                if alias:
                    os.dup2(3, 4)
                if extra:
                    os.dup2(3, 8)
                os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="4", LISTEN_FDNAMES="gate:request:receipt:canary")
                if changes:
                    os.environ.update(changes)
                peer = executor._peer
                def inspected(fd):
                    pid, uid, gid = peer(fd)
                    return pid, uid if nonroot else 0, gid if nonroot else 0
                expected_failure = bool(changes or alias or extra or nonroot)
                try:
                    with mock.patch.object(executor, "_peer", side_effect=inspected):
                        with executor._activation() as (channels, accepted):
                            if expected_failure:
                                raise AssertionError("Accepted invalid activation")
                            assert channels == {"gate": 3, "request": 4, "receipt": 5, "canary": 6}
                            assert len(accepted) == 4
                            assert all(not os.get_inheritable(fd) for fd in accepted)
                            assert not any(key.startswith("LISTEN_") for key in os.environ)
                except executor.GoogleTestExecutorError:
                    if not expected_failure:
                        raise
                for fd in range(3, 7):
                    try:
                        os.fstat(fd)
                    except OSError as error:
                        assert error.errno == errno.EBADF
                    else:
                        raise AssertionError("Accepted channel leaked")
                os._exit(0)
            except BaseException:
                os._exit(1)
        for pair in endpoints:
            for endpoint in pair:
                endpoint.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            child, status = os.waitpid(process, os.WNOHANG)
            if child:
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                return
            time.sleep(.005)
        os.kill(process, 9)
        os.waitpid(process, 0)
        self.fail("Activation child did not finish")

    def test_actual_four_activation_fds_are_consumed_and_cloexec(self):
        self.activation_child()

    def test_activation_wrong_pid_duplicate_name_alias_extra_fd_and_nonroot_peer_fail(self):
        for changes in ({"LISTEN_PID": "1"}, {"LISTEN_FDNAMES": "gate:request:receipt:gate"},
                        {"LISTEN_FDNAMES": "canary:request:receipt:private"}):
            self.activation_child(changes)
        self.activation_child(alias=True)
        self.activation_child(extra=True)
        if os.geteuid() != 0:
            self.activation_child(nonroot=True)

    def test_safe_close_restores_alias_flags_and_preserves_reused_number(self):
        first, second = self.pair()
        fd = os.dup(first.fileno())
        identity = destination._descriptor_identity(fd)
        original = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, original ^ os.O_NONBLOCK)
        executor._safe_close(fd, identity, original)
        self.assertEqual(fcntl.fcntl(first.fileno(), fcntl.F_GETFL), original)
        with tempfile.TemporaryFile() as unrelated:
            os.dup2(unrelated.fileno(), fd)
            try:
                executor._safe_close(fd, identity, original)
                os.fstat(fd)
            finally:
                if fd != unrelated.fileno():
                    os.close(fd)

    @contextmanager
    def protocol(self, *, bad_go=None, bad_ack=None, trailing=False, disconnect=False, canary_failure=False,
                 completion=False, bad_completion=None, completion_trailing=False, completion_silent=False):
        pairs = {name: socket.socketpair() for name in executor.FD_NAMES}
        channels = {name: pair[1].fileno() for name, pair in pairs.items()}
        flags = {fd: fcntl.fcntl(fd, fcntl.F_GETFL) for fd in channels.values()}
        accepted = {fd: (destination._descriptor_identity(fd), flags[fd]) for fd in channels.values()}
        for pair in pairs.values():
            pair[0].setblocking(False)
            pair[1].setblocking(False)
        errors = []
        def root():
            try:
                fd = pairs["canary"][0].fileno()
                ready = executor.canary_read_frame(fd, time.monotonic() + 1)
                self.assertEqual(ready, {"schema_version": 1, "provider": "google_drive", "phase": "ready", "worker_pid": os.getpid()})
                if disconnect:
                    pairs["canary"][0].shutdown(socket.SHUT_WR)
                    return
                executor.canary_write_frame(fd, {**binding(), **(bad_go or {})}, time.monotonic() + 1)
                if bad_go or canary_failure:
                    return
                report = executor.canary_read_frame(fd, time.monotonic() + 1)
                self.assertEqual(report, {**binding("report"), **dict.fromkeys(executor._REPORT_FLAGS, True), "official_https_bytes": 64})
                executor._eof(fd, time.monotonic() + 1)
                executor.canary_write_frame(fd, {**binding("ack"), **(bad_ack or {})}, time.monotonic() + 1)
                if trailing:
                    os.write(fd, b"x")
                pairs["canary"][0].shutdown(socket.SHUT_WR)
                if completion:
                    receipt = pairs["receipt"][0].fileno()
                    self.assertEqual(executor.canary_read_frame(receipt, time.monotonic() + 1), {"private": "test-receipt"})
                    executor._eof(receipt, time.monotonic() + 1)
                    if completion_silent:
                        return
                    value = {"schema_version": 1, "provider": "google_drive", "phase": "complete",
                             "worker_pid": os.getpid(), "request_id": binding()["request_id"], "nonce": binding()["nonce"]}
                    executor.canary_write_frame(receipt, {**value, **(bad_completion or {})}, time.monotonic() + 1)
                    if completion_trailing:
                        os.write(receipt, b"x")
                    pairs["receipt"][0].shutdown(socket.SHUT_WR)
            except BaseException as error:
                errors.append(error)
        @contextmanager
        def activation():
            try:
                yield channels, accepted
            finally:
                for fd, (identity, mode) in accepted.items():
                    executor._safe_close(fd, identity, mode)
        def canaries(value, runner, deadline):
            self.assertEqual(value, binding())
            if canary_failure:
                raise RuntimeError("PRIVATE provider data")
            return {**value, "phase": "report", **dict.fromkeys(executor._REPORT_FLAGS, True), "official_https_bytes": 64}
        thread = threading.Thread(target=root)
        thread.start()
        try:
            with mock.patch.object(executor, "_activation", activation), \
                    mock.patch.object(executor, "_sandbox"), \
                    mock.patch.object(executor, "_inherited") as inherited, \
                    mock.patch.object(executor.google_test, "_Runner") as runner, \
                    mock.patch.object(executor, "_canaries", side_effect=canaries), \
                    mock.patch.object(cli, "main") as command:
                yield channels, command, runner, inherited
        finally:
            thread.join(2)
            for pair in pairs.values():
                for endpoint in pair:
                    try:
                        endpoint.close()
                    except OSError:
                        pass
            self.assertFalse(thread.is_alive())
            if errors:
                raise errors[0]

    def execute(self):
        return executor.run_google_test_executor("/private/prepared", "/private/operational", "/pinned/rclone", "c" * 64)

    def test_full_protocol_closes_canary_then_calls_cli_same_pid_with_original_three(self):
        with self.protocol(completion=True) as (channels, command, runner, inherited):
            def cli_handoff(args):
                self.assertEqual(args[:3], ["google-test", "--prepared-state", "/private/prepared"])
                self.assertNotIn("canary", channels)
                self.assertEqual(set(channels), {"gate", "request", "receipt"})
                for name, fd in channels.items():
                    self.assertEqual(args[args.index("--" + ("root-gate" if name == "gate" else name) + "-fd") + 1], str(fd))
                executor.canary_write_frame(channels["receipt"], {"private": "test-receipt"}, time.monotonic() + 1)
                for fd in channels.values():
                    os.close(fd)
                return 0
            command.side_effect = cli_handoff
            self.assertEqual(self.execute(), 0)
            runner.assert_called_once()
            self.assertEqual(runner.call_args.args[-1], Path("/dev/null"))
            self.assertEqual(inherited.call_count, 3)
            for fd in channels.values():
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_protocol_disconnect_bad_go_bad_ack_trailing_and_canary_fail_never_enter_cli(self):
        for options in ({"disconnect": True}, {"bad_go": {"nonce": "bad"}},
                        {"bad_ack": {"nonce": "d" * 32}}, {"trailing": True}, {"canary_failure": True}):
            with self.subTest(options=options), self.protocol(**options) as (channels, command, _, _):
                with self.assertRaises(executor.GoogleTestExecutorError):
                    self.execute()
                command.assert_not_called()
                for fd in channels.values():
                    with self.assertRaises(OSError):
                        os.fstat(fd)

    def test_sandbox_requires_actual_backup_identity_and_cap0_nnp_seccomp_cgroup(self):
        uid, gid = 12345, 12346
        status = ("Uid:\t" + " ".join([str(uid)] * 4) + "\nGid:\t" + " ".join([str(gid)] * 4)
                  + "\nCapInh:0\nCapPrm:0\nCapEff:0\nCapBnd:0\nCapAmb:0\nNoNewPrivs:1\nSeccomp:2\n").encode()
        cgroup = b"0::/system.slice/raspi-cloud-google-test-" + b"a" * 32 + b".service\n"
        def check(raw=status, group=cgroup, identity=(uid, uid, uid)):
            with mock.patch.object(executor.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=uid, pw_gid=gid)), \
                    mock.patch.object(executor.os, "getresuid", return_value=identity), \
                    mock.patch.object(executor.os, "getresgid", return_value=(gid,) * 3), \
                    mock.patch.object(executor.os, "getgroups", return_value=[gid]), \
                    mock.patch.object(executor, "_read", side_effect=[raw, group]), \
                    mock.patch.object(executor, "_denied_sockets") as denied:
                executor._sandbox()
                denied.assert_called_once()
        check()
        for old, new in ((b"CapEff:0", b"CapEff:1"), (b"CapBnd:0", b"CapBnd:1"),
                         (b"NoNewPrivs:1", b"NoNewPrivs:0"), (b"Seccomp:2", b"Seccomp:0")):
            with self.assertRaises(executor.GoogleTestExecutorError):
                check(status.replace(old, new))
        with self.assertRaises(executor.GoogleTestExecutorError):
            check(group=b"0::/system.slice/unrelated.service\n")
        with self.assertRaises(executor.GoogleTestExecutorError):
            check(identity=(uid, 0, uid))

    def test_denial_requires_eafnosupport_and_io_uring_eperm(self):
        library = SimpleNamespace(syscall=mock.Mock(return_value=-1))
        with mock.patch.object(executor.socket, "socket", side_effect=OSError(errno.EAFNOSUPPORT, "blocked")), \
                mock.patch.object(executor.platform, "system", return_value="Linux"), \
                mock.patch.object(executor.platform, "machine", return_value="aarch64"), \
                mock.patch.object(executor.ctypes, "CDLL", return_value=library), \
                mock.patch.object(executor.ctypes, "get_errno", return_value=errno.EPERM):
            executor._denied_sockets()
        library.syscall.assert_called_once()
        with mock.patch.object(executor.socket, "socket", side_effect=OSError(errno.EPERM, "not AF filter")):
            with self.assertRaises(executor.GoogleTestExecutorError):
                executor._denied_sockets()

    def test_real_loopback_nonce_echo_is_rejected_as_unconfined(self):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with self.subTest(kind=kind), socket.socket(socket.AF_INET, kind) as server:
                server.bind(("127.0.0.1", 0))
                server.settimeout(1)
                if kind == socket.SOCK_STREAM:
                    server.listen(1)
                def echo():
                    if kind == socket.SOCK_STREAM:
                        peer, _ = server.accept()
                        with peer:
                            data = peer.recv(256)
                            peer.sendall(data)
                    else:
                        data, address = server.recvfrom(256)
                        server.sendto(data, address)
                thread = threading.Thread(target=echo)
                thread.start()
                self.assertFalse(executor._loopback(kind, server.getsockname()[1], "a" * 32, time.monotonic() + 1))
                thread.join(1)
                self.assertFalse(thread.is_alive())

    def test_canaries_require_all_checks_and_exact_fixed_public_command(self):
        runner = mock.Mock()
        runner.run.return_value = b"x" * 64
        with mock.patch.object(executor, "_resolver") as resolver, \
                mock.patch.object(executor, "_loopback", return_value=True) as loop, \
                mock.patch.object(executor, "_dns") as dns:
            report = executor._canaries(binding(), runner, time.monotonic() + 5)
            self.assertTrue(all(report[name] is True for name in executor._REPORT_FLAGS))
            self.assertEqual(loop.call_count, 2)
            self.assertEqual(dns.call_count, 2)
            resolver.assert_called_once_with("192.168.4.1")
            command = runner.run.call_args.args[0]
            self.assertEqual(command, ["cat", executor.OFFICIAL_HTTP_PATH, "--http-url", executor.OFFICIAL_HTTP_ROOT,
                                       "--count", "64", "--bind", "0.0.0.0"])
            for size in (0, 63, 65):
                runner.run.return_value = b"x" * size
                with self.assertRaises(executor.GoogleTestExecutorError):
                    executor._canaries(binding(), runner, time.monotonic() + 1)
        with mock.patch.object(executor, "_resolver"), mock.patch.object(executor, "_loopback", return_value=False):
            with self.assertRaises(executor.GoogleTestExecutorError):
                executor._canaries(binding(), runner, time.monotonic() + 1)

    def test_dns_rejects_wrong_response_id_flags_counts_source_and_lengths(self):
        ident = int(binding()["nonce"][:4], 16)
        question = b"\x03raw\x11githubusercontent\x03com\0" + struct.pack("!HH", 1, 1)
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton("185.199.108.133")
        valid = struct.pack("!HHHHHH", ident, 0x8180, 1, 1, 0, 0) + question + answer
        for raw, source, okay in ((valid, ("192.168.4.1", 53), True),
                                 (valid, ("192.168.4.2", 53), False),
                                 (struct.pack("!HHHHHH", ident + 1, 0x8180, 1, 1, 0, 0), ("192.168.4.1", 53), False),
                                 (struct.pack("!HHHHHH", ident, 0x8380, 1, 1, 0, 0), ("192.168.4.1", 53), False),
                                 (struct.pack("!HHHHHH", ident, 0x8183, 1, 1, 0, 0), ("192.168.4.1", 53), False),
                                 (struct.pack("!HHHHHH", ident, 0x8180, 1, 0, 0, 0), ("192.168.4.1", 53), False),
                                 (b"x" * 4097, ("192.168.4.1", 53), False)):
            fake = mock.MagicMock()
            fake.__enter__.return_value = fake
            fake.recvfrom.return_value = raw, source
            with mock.patch.object(executor.socket, "socket", return_value=fake):
                if okay:
                    executor._dns("192.168.4.1", False, binding()["nonce"], time.monotonic() + 1)
                else:
                    with self.assertRaises(executor.GoogleTestExecutorError):
                        executor._dns("192.168.4.1", False, binding()["nonce"], time.monotonic() + 1)

    def test_dns_tcp_fragments_and_response_structure_are_validated(self):
        ident = int(binding()["nonce"][:4], 16)
        question = b"\x03raw\x11githubusercontent\x03com\0" + struct.pack("!HH", 1, 1)
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton("185.199.108.133")
        raw = struct.pack("!HHHHHH", ident, 0x8180, 1, 1, 0, 0) + question + answer
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        data = bytearray(len(raw).to_bytes(2, "big") + raw)
        def receive(count):
            block = bytes(data[:min(3, count)])
            del data[:len(block)]
            return block
        fake.recv.side_effect = receive
        with mock.patch.object(executor.socket, "socket", return_value=fake):
            executor._dns("192.168.4.1", True, binding()["nonce"], time.monotonic() + 1)
        self.assertFalse(data)
        for broken in (raw[:12], raw[:-1], raw + b"x", raw.replace(b"raw", b"bad", 1),
                       raw[:len(raw) - len(answer)] + b"\xff\xff" + answer[2:],
                       raw[:-4] + socket.inet_aton("127.0.0.1")):
            with self.subTest(size=len(broken)), self.assertRaises((executor.GoogleTestExecutorError, ValueError)):
                executor._dns_response(broken, ident)

    def test_resolver_real_file_exact_bytes_owner_modes_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            resolver = Path(directory) / "resolv.conf"
            resolver.write_text("nameserver 192.168.4.1\noptions timeout:2 attempts:1\n")
            resolver.chmod(0o644)
            # Only root ownership is synthesized; real no-follow reads and
            # inode/size/timestamp checks run as the ordinary test UID.
            class RootResolver:
                def __fspath__(self):
                    return str(resolver)
                def lstat(self):
                    real = resolver.lstat()
                    values = {key: getattr(real, key) for key in dir(real) if key.startswith("st_")}
                    values["st_uid"] = 0
                    return SimpleNamespace(**values)
            with mock.patch.object(executor, "Path", return_value=RootResolver()):
                executor._resolver("192.168.4.1")
                with self.assertRaises(executor.GoogleTestExecutorError):
                    executor._resolver("192.168.4.2")
                resolver.chmod(0o666)
                with self.assertRaises(executor.GoogleTestExecutorError):
                    executor._resolver("192.168.4.1")
                resolver.unlink()
                resolver.symlink_to("missing")
                with self.assertRaises(executor.GoogleTestExecutorError):
                    executor._resolver("192.168.4.1")

    def test_cli_handoff_exception_or_unaccepted_descriptors_are_closed(self):
        with self.protocol() as (channels, command, _, _):
            command.side_effect = RuntimeError("PRIVATE token source path")
            with self.assertRaises(executor.GoogleTestExecutorError) as error:
                self.execute()
            self.assertEqual(str(error.exception), "google_test_executor_refused")
            self.assertIsNone(error.exception.__cause__)
            for fd in channels.values():
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_completion_wrong_nonce_pid_types_trailing_and_deadline_refuse(self):
        for changes, trailing in (({"nonce": "d" * 32}, False), ({"worker_pid": True}, False),
                                  ({"schema_version": True}, False), ({}, True)):
            with self.subTest(changes=changes, trailing=trailing), \
                    self.protocol(completion=True, bad_completion=changes, completion_trailing=trailing) as (channels, command, _, _):
                def complete(args):
                    executor.canary_write_frame(channels["receipt"], {"private": "test-receipt"}, time.monotonic() + 1)
                    for fd in channels.values():
                        os.close(fd)
                    return 0
                command.side_effect = complete
                with self.assertRaises(executor.GoogleTestExecutorError):
                    self.execute()

    def test_completion_silence_is_bounded_and_cannot_exit_success(self):
        with self.protocol(completion=True, completion_silent=True) as (channels, command, _, _):
            def complete(args):
                executor.canary_write_frame(channels["receipt"], {"private": "test-receipt"}, time.monotonic() + 1)
                for fd in channels.values():
                    os.close(fd)
                return 0
            command.side_effect = complete
            started = time.monotonic()
            with mock.patch.object(executor, "COMPLETION_TIMEOUT", .03):
                with self.assertRaisesRegex(executor.GoogleTestExecutorError, "timeout"):
                    self.execute()
            self.assertLess(time.monotonic() - started, 1)

    def test_main_errors_suppress_private_paths_arguments_and_raw_exceptions(self):
        arguments = ["--prepared-state", "/PRIVATE/prepared", "--operational-state", "/PRIVATE/operational",
                     "--rclone", "/PRIVATE/rclone", "--rclone-sha256", "PRIVATE", "--timeout", "PRIVATE"]
        for args in ([], arguments):
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors), mock.patch.object(executor, "run_google_test_executor") as run:
                self.assertEqual(executor.main(args), 2)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), executor._ERROR + "\n")
            run.assert_not_called()
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors), \
                mock.patch.object(executor, "run_google_test_executor", side_effect=RuntimeError("PRIVATE token")):
            self.assertEqual(executor.main(arguments[:-2]), 2)
        self.assertNotIn("PRIVATE", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
