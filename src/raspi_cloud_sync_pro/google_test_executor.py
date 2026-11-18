"""Ordinary-UID bootstrap for a root-coordinated first Google Test.

Four inherited, connected root-peer Unix streams carry separate canary, request,
receipt and root-gate protocols. No preparation/configuration state is opened
here. Root must inspect the actual held worker cgroup and BPF policy before GO,
then recheck it and current owner intent before ACK and the separate root gate.
Canary success proves no root authority and never enables uploads by itself.
SO_PEERCRED authenticates the coordinator, not network confinement. No USB-peer
packet acceptance is claimed. The same process enters the existing private CLI.
"""

import argparse
import ctypes
import errno
import fcntl
import ipaddress
import json
import math
import os
import platform
import pwd
import re
import selectors
import socket
import stat
import struct
import sys
import time
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
import io

from . import google_destination as destination
from . import google_test
from .config import _unique_object
from .errors import BackupError

FD_NAMES = frozenset(("gate", "request", "receipt", "canary"))
CANARY_LIMIT = 4096
CANARY_TIMEOUT = 45.0
COMPLETION_TIMEOUT = 15.0
OFFICIAL_URL = "https://raw.githubusercontent.com/rclone/rclone/v1.76.0/COPYING"
OFFICIAL_HTTP_ROOT = "https://raw.githubusercontent.com"
OFFICIAL_HTTP_PATH = ":http:rclone/rclone/v1.76.0/COPYING"
OFFICIAL_BYTES = 64
_ERROR = "Private Google Test executor unavailable; no private details were printed."
_BINDING_KEYS = {"schema_version", "provider", "phase", "worker_pid", "request_id", "nonce",
                 "dns_ipv4", "loopback_port"}
_REPORT_FLAGS = {"kernel_sandbox_verified", "loopback_tcp_blocked", "loopback_udp_blocked",
                 "dns_udp_verified", "dns_tcp_verified", "official_https_verified"}
_ID = re.compile(r"[0-9a-f]{32}\Z")
_CGROUP = re.compile(r"0::/system.slice/raspi-cloud-google-test-[0-9a-f]{32}\.service\n?\Z")


class GoogleTestExecutorError(BackupError):
    """Fixed failure codes, never provider output, paths or private context."""


def _fail(code="google_test_executor_refused"):
    raise GoogleTestExecutorError(code) from None


def _check(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or time.monotonic() >= deadline:
        _fail("google_test_executor_timeout")


def _wait(selector, deadline):
    _check(deadline)
    if not selector.select(deadline - time.monotonic()):
        _fail("google_test_executor_timeout")


def canary_read_frame(fd, deadline):
    """Read one bounded duplex frame, without consuming the next protocol phase."""
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)

        def read(count):
            result = bytearray()
            while len(result) < count:
                _wait(selector, deadline)
                try:
                    block = os.read(fd, count - len(result))
                except BlockingIOError:
                    continue
                if not block:
                    _fail("google_test_executor_protocol_invalid")
                result.extend(block)
            return bytes(result)

        length = int.from_bytes(read(4), "big")
        if not 0 < length <= CANARY_LIMIT:
            _fail("google_test_executor_protocol_invalid")
        try:
            value = json.loads(read(length), object_pairs_hook=_unique_object)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            _fail("google_test_executor_protocol_invalid")
        if not isinstance(value, dict):
            _fail("google_test_executor_protocol_invalid")
        return value


def canary_write_frame(fd, value, deadline):
    """Write one bounded duplex frame; the caller owns EOF/shutdown semantics."""
    raw = destination._canonical(value)
    if not 0 < len(raw) <= CANARY_LIMIT:
        _fail("google_test_executor_protocol_invalid")
    destination._write_frame(fd, raw, deadline)


def _eof(fd, deadline):
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        _wait(selector, deadline)
        if os.read(fd, 1):
            _fail("google_test_executor_protocol_invalid")


def _shutdown_write(fd):
    # Wrap the existing descriptor without creating either a socket or a
    # transient extra descriptor visible to the root inspector.
    probe = socket.socket(fileno=fd)
    try:
        probe.shutdown(socket.SHUT_WR)
    finally:
        probe.detach()


def _peer(fd):
    duplicate = os.dup(fd)
    try:
        probe = socket.socket(fileno=duplicate)
    except BaseException:
        os.close(duplicate)
        raise
    with probe:
        if probe.family != socket.AF_UNIX or probe.type != socket.SOCK_STREAM:
            _fail("google_test_executor_channels_invalid")
        probe.getpeername()
        return struct.unpack("3i", probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def _safe_close(fd, identity, flags):
    try:
        if destination._descriptor_identity(fd) == identity:
            try:
                fcntl.fcntl(fd, fcntl.F_SETFL, flags)
            finally:
                os.close(fd)
    except OSError:
        pass


def _inherited(allowed):
    # Bound enumeration as well as the final list; proc's enumeration FD closes
    # before fstat, and is permitted only if it has already disappeared.
    entries = []
    with os.scandir("/proc/self/fd") as iterator:
        for entry in iterator:
            if len(entries) >= 256:
                _fail("google_test_executor_channels_invalid")
            entries.append(entry.name)
    for value in entries:
        fd = int(value)
        try:
            info = os.fstat(fd)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
            continue
        if fd in allowed:
            continue
        if fd > 2 or stat.S_ISSOCK(info.st_mode):
            _fail("google_test_executor_channels_invalid")


@contextmanager
def _activation():
    environment = {key: os.environ.pop(key, None)
                   for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")}
    # With no activation protocol, default to refusal without accepting FDs.
    if environment["LISTEN_FDS"] != "4":
        _fail("google_test_executor_channels_invalid")
    accepted, channels, peers = {}, {}, []
    valid = True
    try:
        names = (environment["LISTEN_FDNAMES"] or "").split(":")
        for fd in range(3, 7):
            try:
                identity = destination._descriptor_identity(fd)
                if (not stat.S_ISSOCK(os.fstat(fd).st_mode) or os.isatty(fd)
                        or any(destination._descriptor_identity(standard) == identity for standard in (0, 1, 2))):
                    valid = False
                    continue
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                peer = _peer(fd)
                accepted[fd] = identity, flags
                peers.append(peer)
                os.set_inheritable(fd, False)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            except (OSError, BackupError, ValueError, TypeError):
                valid = False
        if (not valid or len(accepted) != 4 or len(set(item[0] for item in accepted.values())) != 4
                or len(set(peers)) != 1 or peers[0][0] <= 1 or peers[0][1:] != (0, 0)
                or environment["LISTEN_PID"] != str(os.getpid()) or len(names) != 4 or set(names) != FD_NAMES):
            _fail("google_test_executor_channels_invalid")
        channels = dict(zip(names, range(3, 7)))
        _inherited(set(channels.values()))
        yield channels, accepted
    finally:
        for fd, (identity, flags) in accepted.items():
            _safe_close(fd, identity, flags)


def _read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            _fail()
        return data
    finally:
        os.close(fd)


def _denied_sockets():
    for family, kind in ((socket.AF_INET6, socket.SOCK_STREAM), (socket.AF_INET6, socket.SOCK_DGRAM),
                         (socket.AF_UNIX, socket.SOCK_STREAM), (socket.AF_NETLINK, socket.SOCK_RAW),
                         (socket.AF_PACKET, socket.SOCK_RAW)):
        try:
            descriptor = socket.socket(family, kind)
        except OSError as error:
            if error.errno != errno.EAFNOSUPPORT:
                _fail("google_test_executor_sandbox_invalid")
        else:
            descriptor.close()
            _fail("google_test_executor_sandbox_invalid")
    if platform.system() != "Linux" or platform.machine() != "aarch64" or ctypes.sizeof(ctypes.c_void_p) != 8:
        _fail("google_test_executor_sandbox_invalid")
    library = ctypes.CDLL(None, use_errno=True)
    # aarch64 __NR_io_uring_setup=425. Blocking this avoids AF-filter bypasses.
    result = library.syscall(425, 1, ctypes.create_string_buffer(256))
    if result >= 0:
        os.close(result)
        _fail("google_test_executor_sandbox_invalid")
    if ctypes.get_errno() != errno.EPERM:
        _fail("google_test_executor_sandbox_invalid")


def _sandbox():
    account = pwd.getpwnam("raspi-backup")
    uid, gid = account.pw_uid, account.pw_gid
    if (uid <= 0 or gid <= 0 or os.getresuid() != (uid,) * 3 or os.getresgid() != (gid,) * 3
            or not set(os.getgroups()).issubset({gid})):
        _fail("google_test_executor_sandbox_invalid")
    status = dict(line.split(":", 1) for line in _read("/proc/self/status", 16384).decode("ascii").splitlines()
                  if ":" in line)
    if (any([int(value) for value in status[name].split()] != [expected] * 4
            for name, expected in (("Uid", uid), ("Gid", gid)))
            or any(int(status[name].strip(), 16) != 0 for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
            or status["NoNewPrivs"].strip() != "1" or status["Seccomp"].strip() != "2"
            or not _CGROUP.fullmatch(_read("/proc/self/cgroup", 4096).decode("ascii"))):
        _fail("google_test_executor_sandbox_invalid")
    _denied_sockets()


def _dns_address(value):
    if not isinstance(value, str):
        _fail("google_test_executor_protocol_invalid")
    address = ipaddress.IPv4Address(value)
    if str(address) != value or any((address.is_loopback, address.is_unspecified, address.is_multicast,
                                     address.is_link_local, address.is_reserved)):
        _fail("google_test_executor_protocol_invalid")
    return value


def _binding(value, phase):
    if (not isinstance(value, dict) or set(value) != _BINDING_KEYS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["provider"] != "google_drive" or value["phase"] != phase
            or type(value["worker_pid"]) is not int or value["worker_pid"] != os.getpid()
            or not isinstance(value["request_id"], str) or not _ID.fullmatch(value["request_id"])
            or not isinstance(value["nonce"], str) or not _ID.fullmatch(value["nonce"])
            or type(value["loopback_port"]) is not int or not 1024 <= value["loopback_port"] <= 65535):
        _fail("google_test_executor_protocol_invalid")
    _dns_address(value["dns_ipv4"])
    return value


def _resolver(dns):
    path = Path("/etc/resolv.conf")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022
            or not 0 < info.st_size <= 1024):
        _fail("google_test_executor_sandbox_invalid")
    expected = ("nameserver " + dns + "\noptions timeout:2 attempts:1\n").encode("ascii")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if (destination._file_identity(before) != destination._file_identity(info)
                or os.read(fd, 1025) != expected
                or destination._file_identity(os.fstat(fd)) != destination._file_identity(info)
                or destination._file_identity(path.lstat()) != destination._file_identity(info)):
            _fail("google_test_executor_sandbox_invalid")
    finally:
        os.close(fd)


def _timeout(deadline, cap):
    _check(deadline)
    return max(.001, min(cap, deadline - time.monotonic()))


def _receive(sock, count, deadline):
    result = bytearray()
    while len(result) < count:
        sock.settimeout(_timeout(deadline, 3))
        block = sock.recv(count - len(result))
        if not block:
            _fail("google_test_executor_canary_failed")
        result.extend(block)
    return bytes(result)


def _loopback(kind, port, nonce, deadline):
    with socket.socket(socket.AF_INET, kind) as descriptor:
        descriptor.settimeout(_timeout(deadline, .8))
        try:
            if kind == socket.SOCK_STREAM:
                descriptor.connect(("127.0.0.1", port))
                descriptor.sendall(nonce.encode("ascii"))
                result = _receive(descriptor, len(nonce), deadline)
            else:
                descriptor.sendto(nonce.encode("ascii"), ("127.0.0.1", port))
                result, address = descriptor.recvfrom(257)
                if address != ("127.0.0.1", port):
                    _fail("google_test_executor_canary_failed")
        except OSError:
            _check(deadline)
            return True
        # An echo demonstrates loopback access. Other unexpected responses also
        # fail closed; absence alone is meaningful only with root's held policy
        # and independently positive, nonce-only listener checks.
        if result == nonce.encode("ascii"):
            return False
        _fail("google_test_executor_canary_failed")


def _dns(dns, tcp, nonce, deadline):
    identifier = int(nonce[:4], 16)
    query = struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 0)
    query += b"\x03raw\x11githubusercontent\x03com\0" + struct.pack("!HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as descriptor:
        descriptor.settimeout(_timeout(deadline, 3))
        if tcp:
            descriptor.connect((dns, 53))
            descriptor.sendall(len(query).to_bytes(2, "big") + query)
            length = int.from_bytes(_receive(descriptor, 2, deadline), "big")
            if not 12 <= length <= 4096:
                _fail("google_test_executor_canary_failed")
            raw = _receive(descriptor, length, deadline)
        else:
            descriptor.sendto(query, (dns, 53))
            raw, address = descriptor.recvfrom(4097)
            if address != (dns, 53):
                _fail("google_test_executor_canary_failed")
        _dns_response(raw, identifier)
    _check(deadline)


def _dns_response(raw, identifier):
    if not 12 <= len(raw) <= 4096:
        _fail("google_test_executor_canary_failed")
    ident, flags, questions, answers, authority, additional = struct.unpack("!HHHHHH", raw[:12])
    if (ident != identifier or not flags & 0x8000 or flags & 0x7a0f or questions != 1
            or answers == 0 or answers + authority + additional > 64):
        _fail("google_test_executor_canary_failed")

    def name(offset):
        labels, seen, end = [], set(), None
        for _ in range(128):
            if offset >= len(raw) or offset in seen:
                _fail("google_test_executor_canary_failed")
            seen.add(offset)
            size = raw[offset]
            if size & 0xc0 == 0xc0:
                if offset + 1 >= len(raw):
                    _fail("google_test_executor_canary_failed")
                target = ((size & 0x3f) << 8) | raw[offset + 1]
                if target >= offset:
                    _fail("google_test_executor_canary_failed")
                if end is None:
                    end = offset + 2
                offset = target
            elif size == 0:
                return b".".join(labels).lower(), end if end is not None else offset + 1
            else:
                if size > 63 or offset + 1 + size > len(raw):
                    _fail("google_test_executor_canary_failed")
                labels.append(raw[offset + 1:offset + 1 + size])
                if sum(map(len, labels)) + len(labels) > 254:
                    _fail("google_test_executor_canary_failed")
                offset += 1 + size
        _fail("google_test_executor_canary_failed")

    question, offset = name(12)
    if (question != b"raw.githubusercontent.com" or offset + 4 > len(raw)
            or raw[offset:offset + 4] != struct.pack("!HH", 1, 1)):
        _fail("google_test_executor_canary_failed")
    offset += 4
    addresses, aliases = set(), {}
    for index in range(answers + authority + additional):
        owner, offset = name(offset)
        if offset + 10 > len(raw):
            _fail("google_test_executor_canary_failed")
        record_type, record_class, _, length = struct.unpack("!HHIH", raw[offset:offset + 10])
        offset += 10
        end = offset + length
        if end > len(raw):
            _fail("google_test_executor_canary_failed")
        if index < answers and record_class == 1:
            if record_type == 1:
                if length != 4:
                    _fail("google_test_executor_canary_failed")
                _dns_address(socket.inet_ntoa(raw[offset:end]))
                addresses.add(owner)
            elif record_type == 5:
                target, consumed = name(offset)
                if consumed != end or owner in aliases:
                    _fail("google_test_executor_canary_failed")
                aliases[owner] = target
        offset = end
    if offset != len(raw):
        _fail("google_test_executor_canary_failed")
    current, seen = question, set()
    for _ in range(17):
        if current in addresses:
            return
        if current in seen or current not in aliases:
            break
        seen.add(current)
        current = aliases[current]
    _fail("google_test_executor_canary_failed")


def _canaries(binding, runner, deadline):
    _resolver(binding["dns_ipv4"])
    if (not _loopback(socket.SOCK_STREAM, binding["loopback_port"], binding["nonce"], deadline)
            or not _loopback(socket.SOCK_DGRAM, binding["loopback_port"], binding["nonce"], deadline)):
        _fail("google_test_executor_canary_failed")
    _dns(binding["dns_ipv4"], False, binding["nonce"], deadline)
    _dns(binding["dns_ipv4"], True, binding["nonce"], deadline)
    runner.deadline = min(deadline, time.monotonic() + 16)
    output = runner.run(["cat", OFFICIAL_HTTP_PATH, "--http-url", OFFICIAL_HTTP_ROOT,
                         "--count", str(OFFICIAL_BYTES), "--bind", "0.0.0.0"], capture=True)
    if len(output) != OFFICIAL_BYTES:
        _fail("google_test_executor_canary_failed")
    _check(deadline)
    return {**binding, "phase": "report", **dict.fromkeys(_REPORT_FLAGS, True), "official_https_bytes": OFFICIAL_BYTES}


def run_google_test_executor(prepared_state, operational_state, executable, executable_sha256, *,
                             timeout=120, canary_timeout=CANARY_TIMEOUT):
    """Consume activation channels; credentials remain behind the existing gate."""
    try:
        with _activation() as (channels, accepted):
            if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300
                    or type(canary_timeout) not in (int, float) or not math.isfinite(canary_timeout)
                    or not 0 < canary_timeout <= 60):
                _fail()
            deadline = time.monotonic() + canary_timeout
            _sandbox()
            # Validates only the protected public executable; /dev/null is the
            # canary's only rclone config. It never reads source/prepared state.
            runner = google_test._Runner(executable, executable_sha256, deadline, Path("/dev/null"))
            control = channels["canary"]
            canary_write_frame(control, {"schema_version": 1, "provider": "google_drive", "phase": "ready",
                                         "worker_pid": os.getpid()}, deadline)
            binding = _binding(canary_read_frame(control, deadline), "go")
            report = _canaries(binding, runner, deadline)
            _inherited(set(channels.values()))
            canary_write_frame(control, report, deadline)
            _shutdown_write(control)
            acknowledgement = _binding(canary_read_frame(control, deadline), "ack")
            if acknowledgement != {**binding, "phase": "ack"}:
                _fail("google_test_executor_protocol_invalid")
            _eof(control, deadline)
            identity, flags = accepted.pop(control)
            _safe_close(control, identity, flags)
            del channels["canary"]
            _inherited(set(channels.values()))
            _check(deadline)
            # Restore original status flags before transferring these exact
            # descriptors. CLI now consumes them, including on early refusal.
            handoff = {}
            for name, fd in channels.items():
                identity, flags = accepted[fd]
                fcntl.fcntl(fd, fcntl.F_SETFL, flags)
                handoff[fd] = identity, flags
            receipt_identity, receipt_flags = accepted[channels["receipt"]]
            # The live worker must remain inspectable after its Test receipt.
            # This duplicate is CLOEXEC and rclone also uses close_fds=True.
            completion = fcntl.fcntl(channels["receipt"], fcntl.F_DUPFD_CLOEXEC, 3)
            try:
                from . import cli
                arguments = ["google-test", "--prepared-state", str(prepared_state),
                    "--operational-state", str(operational_state), "--request-fd", str(channels["request"]),
                    "--root-gate-fd", str(channels["gate"]), "--receipt-fd", str(channels["receipt"]),
                    "--rclone", str(executable), "--rclone-sha256", str(executable_sha256), "--timeout", str(timeout)]
                for fd in handoff:
                    accepted.pop(fd)
                result = cli.main(arguments)
                if type(result) is not int or result != 0:
                    return 2
                # CLI consumed all three original channels. No operational or
                # credential work follows this point. Root now checks the live
                # cgroup, descendants, process and BPF policy before ACK.
                _inherited({completion})
                _sandbox()
                fcntl.fcntl(completion, fcntl.F_SETFL, receipt_flags | os.O_NONBLOCK)
                _shutdown_write(completion)
                completion_deadline = time.monotonic() + COMPLETION_TIMEOUT
                acknowledgement = canary_read_frame(completion, completion_deadline)
                expected = {"schema_version": 1, "provider": "google_drive", "phase": "complete",
                            "worker_pid": os.getpid(), "request_id": binding["request_id"], "nonce": binding["nonce"]}
                if (acknowledgement != expected or type(acknowledgement.get("schema_version")) is not int
                        or type(acknowledgement.get("worker_pid")) is not int):
                    _fail("google_test_executor_protocol_invalid")
                _eof(completion, completion_deadline)
                # ACK grants only closure and exit, never a further operation.
                return 0
            finally:
                for fd, (identity, flags) in handoff.items():
                    _safe_close(fd, identity, flags)
                _safe_close(completion, receipt_identity, receipt_flags)
    except GoogleTestExecutorError:
        raise
    except Exception:
        _fail()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private root-coordinated Google Test bootstrap")
    parser.add_argument("--prepared-state", required=True, type=Path)
    parser.add_argument("--operational-state", required=True, type=Path)
    parser.add_argument("--rclone", required=True, type=Path)
    parser.add_argument("--rclone-sha256", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    try:
        with redirect_stderr(io.StringIO()):
            args = parser.parse_args(argv)
    except SystemExit as error:
        if error.code == 0:
            return 0
        print(_ERROR, file=sys.stderr)
        return 2
    try:
        return run_google_test_executor(args.prepared_state, args.operational_state, args.rclone,
                                        args.rclone_sha256, timeout=args.timeout)
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
