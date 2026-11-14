"""Opt-in root coordinator for one private owner Google Test.

No saved QA receipt grants authority. Every credential release requires a fresh
worker identity, held kernel Wi-Fi policy, successful credential-free canaries,
unchanged NetworkManager policy and a fresh matching source-authority export.
Only ordinary source/backup identities read credentials. Test never enables a
recurring worker. The public result contains no request, account or path data.

Native installation/activation and empirical hardware acceptance are separate;
this module is disabled by default and makes no such acceptance claim.
"""

import hashlib
import ipaddress
import json
import os
import re
import secrets
import selectors
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import google_broker as preparation
from . import google_control as control
from . import google_destination as destination
from .config import _unique_object
from .errors import BackupError

_APPROVAL = {"schema_version": 1, "google_test_broker_approved": True}
_FIELDS = {"schema_version", "enabled", "interpreter", "rclone", "rclone_sha256",
           "source_state", "backup_state", "operational_state", "dashboard_user", "backup_user",
           "public_origin", "approval_file", "runtime_directory", "lock_file",
           "wifi_profile_uuid", "dns_ipv4", "allow_metered"}
_PATHS = ("interpreter", "rclone", "source_state", "backup_state", "operational_state",
          "approval_file", "runtime_directory", "lock_file")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SAFE_PATH = re.compile(r"/[A-Za-z0-9_./-]+\Z")
_NAMES = ("gate", "request", "receipt", "canary")
_TIMEOUT = 240.0
_CANARY_TIMEOUT = 45.0
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}
_CANARY_FLAGS = {"kernel_sandbox_verified", "loopback_tcp_blocked", "loopback_udp_blocked",
                 "dns_udp_verified", "dns_tcp_verified", "official_https_verified"}
_SYSTEMD_PROPERTIES = (
    "Id", "Description", "InvocationID", "LoadState", "ActiveState", "SubState",
    "MainPID", "ControlPID", "Transient", "FragmentPath", "DropInPaths", "User",
    "NoNewPrivileges", "CapabilityBoundingSet", "AmbientCapabilities", "RestrictAddressFamilies",
    "PrivateNetwork", "ControlGroup", "ExecMainCode", "ExecMainStatus", "Result",
)


class GoogleTestBrokerError(BackupError):
    """Fixed codes only; never attach raw subprocess, credential or path data."""


def _fail(code):
    raise GoogleTestBrokerError(code) from None


def _result(status, code):
    return {"status": status, "code": code, "uploads_enabled": False,
            "account_verified": False, "current_worker_state_checked": False}


def _path(value):
    path = preparation._absolute(value)
    if _SAFE_PATH.fullmatch(str(path)) is None:
        _fail("google_test_broker_configuration_invalid")
    return path


@dataclass(frozen=True, repr=False)
class GoogleTestBrokerSettings:
    enabled: bool = False
    interpreter: Path = None
    rclone: Path = None
    rclone_sha256: str = None
    source_state: Path = None
    backup_state: Path = None
    operational_state: Path = None
    dashboard_user: str = None
    backup_user: str = None
    public_origin: str = None
    approval_file: Path = None
    runtime_directory: Path = None
    lock_file: Path = None
    wifi_profile_uuid: str = None
    dns_ipv4: str = None
    allow_metered: bool = False
    config_path: Path = field(default=None, repr=False)
    _identity: tuple = field(default=None, repr=False)

    def __repr__(self):
        return "<GoogleTestBrokerSettings protected>"

    @classmethod
    def load(cls, root_config_path):
        try:
            path = _path(root_config_path)
            data, identity = preparation._root_json(path, private=True)
            if (not isinstance(data, dict) or set(data) - _FIELDS
                    or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                    or type(data.get("enabled", False)) is not bool):
                _fail("google_test_broker_configuration_invalid")
            if not data.get("enabled", False):
                return cls(config_path=path, _identity=identity)
            if (set(data) != _FIELDS or data["backup_user"] != "raspi-backup"
                    or type(data["allow_metered"]) is not bool
                    or not isinstance(data["wifi_profile_uuid"], str)
                    or _UUID.fullmatch(data["wifi_profile_uuid"]) is None
                    or not control._hash(data["rclone_sha256"])):
                _fail("google_test_broker_configuration_invalid")
            values = {name: _path(data[name]) for name in _PATHS}
            roots = [values[name] for name in ("source_state", "backup_state", "operational_state")]
            if any(a == b or a in b.parents or b in a.parents for index, a in enumerate(roots) for b in roots[index + 1:]):
                _fail("google_test_broker_configuration_invalid")
            if any(not isinstance(data[name], str) or preparation._USER.fullmatch(data[name]) is None
                   for name in ("dashboard_user", "backup_user")):
                _fail("google_test_broker_configuration_invalid")
            origin = destination._origin(data["public_origin"])
            dns = ipaddress.IPv4Address(data["dns_ipv4"])
            if (origin != data["public_origin"] or str(dns) != data["dns_ipv4"]
                    or dns.is_loopback or dns.is_unspecified or dns.is_multicast or dns.is_link_local
                    or values["lock_file"].parent != values["runtime_directory"]):
                _fail("google_test_broker_configuration_invalid")
            for name in ("dashboard_user", "backup_user", "rclone_sha256", "wifi_profile_uuid", "allow_metered"):
                values[name] = data[name]
            return cls(enabled=True, config_path=path, _identity=identity, public_origin=origin,
                       dns_ipv4=str(dns), **values)
        except GoogleTestBrokerError:
            raise
        except Exception:
            _fail("google_test_broker_configuration_invalid")


def _remaining(deadline, maximum=5):
    remaining = min(maximum, deadline - time.monotonic())
    if remaining <= 0:
        _fail("google_test_broker_timeout")
    return remaining


def _command(arguments, deadline, *, limit=65536):
    """Fixed privileged command, bounded output/deadline, own process group only."""
    _remaining(deadline)
    output = bytearray()
    child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=_ENV, cwd="/", close_fds=True,
                             start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                events = selector.select(_remaining(deadline))
                if not events:
                    _fail("google_test_broker_timeout")
                for key, _ in events:
                    block = os.read(key.fd, 8192)
                    if not block:
                        selector.unregister(key.fd)
                    else:
                        output.extend(block)
                        if len(output) > limit:
                            _fail("google_test_broker_command_invalid")
        code = child.wait(timeout=_remaining(deadline))
        return code, bytes(output)
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.stdout.close()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


class _Source:
    def __init__(self, settings, account):
        self.settings, self.account = settings, account

    def export(self, request_id, deadline):
        _remaining(deadline)
        incoming, outgoing = preparation._private_pipe()
        child = None
        try:
            child = preparation._spawn(self.settings, self.account,
                ["google-control-export", "--state", str(self.settings.source_state),
                 "--public-origin", self.settings.public_origin, "--request-id", request_id,
                 "--output-fd", str(outgoing)], (outgoing,))
            os.close(outgoing)
            outgoing = None
            owned, incoming = incoming, None
            value = preparation._receive(owned, time.monotonic() + _remaining(deadline), 65536)
            if preparation._wait_child(child, time.monotonic() + _remaining(deadline)):
                _fail("google_test_broker_source_refused")
            return value
        finally:
            for fd in (incoming, outgoing):
                if fd is not None:
                    os.close(fd)
            if child is not None:
                preparation._stop_children([child])

    def record(self, request_id, receipt, deadline):
        _remaining(deadline)
        incoming, outgoing = preparation._private_pipe()
        child = None
        try:
            child = preparation._spawn(self.settings, self.account,
                ["google-control-record", "--state", str(self.settings.source_state),
                 "--public-origin", self.settings.public_origin, "--request-id", request_id,
                 "--receipt-fd", str(incoming)], (incoming,))
            os.close(incoming)
            incoming = None
            owned, outgoing = outgoing, None
            with destination._channel(owned, writing=True) as channel:
                destination._write_frame(channel, destination._canonical(receipt),
                                         time.monotonic() + _remaining(deadline))
            if preparation._wait_child(child, time.monotonic() + _remaining(deadline)):
                _fail("google_test_broker_source_refused")
        finally:
            for fd in (incoming, outgoing):
                if fd is not None:
                    os.close(fd)
            if child is not None:
                preparation._stop_children([child])


def _current_request(source_fd, owner):
    try:
        workflow = os.open(control.WORKFLOW_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
    except FileNotFoundError:
        return None
    try:
        preparation._directory_info(workflow, owner, private=True)
        try:
            raw = preparation._read_private_bytes(workflow, "current.json", owner, 256)
        except FileNotFoundError:
            return None
        pointer = json.loads(raw, object_pairs_hook=_unique_object)
        if (not isinstance(pointer, dict) or set(pointer) != {"schema_version", "request_id"}
                or type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1
                or not control._id(pointer["request_id"])):
            _fail("google_test_broker_request_invalid")
        return pointer["request_id"]
    finally:
        os.close(workflow)


def _request(value, request_id):
    now = time.time()
    if (not isinstance(value, dict) or set(value) != {"schema_version", "provider", "request_id", "action",
            "requested_at", "expires_at", "preparation", "test", "fixture"}
            or not control.setup._valid_header(value) or value["request_id"] != request_id
            or value["action"] != "test" or value["test"] is not None
            or not control.setup._timestamp(value["requested_at"])
            or not control.setup._timestamp(value["expires_at"])
            or value["expires_at"] != value["requested_at"] + control.REQUEST_LIFETIME
            or not value["requested_at"] <= now < value["expires_at"]
            or not control._preparation_valid(value["preparation"])):
        _fail("google_test_broker_request_invalid")
    fixture = control.google_test_fixture(request_id)
    if (not isinstance(value["fixture"], dict) or type(value["fixture"].get("size")) is not int
            or value["fixture"] != {"size": len(fixture), "sha256": hashlib.sha256(fixture).hexdigest()}):
        _fail("google_test_broker_request_invalid")
    return value


def _receipt(value, request):
    if (not isinstance(value, dict) or set(value) != control._RECEIPT_FIELDS
            or not control.setup._valid_header(value)
            or any(value[key] != request[key] for key in ("request_id", "action", "preparation", "test"))
            or value["outcome"] != "verified" or value["enabled"] is not False
            or value["automatic_deletion"] is not False
            or not control.setup._timestamp(value["completed_at"])
            or not request["requested_at"] <= value["completed_at"] < request["expires_at"]
            or value["completed_at"] > time.time()):
        _fail("google_test_broker_receipt_invalid")
    result = value["result"]
    if (not isinstance(result, dict) or set(result) != control._RESULT_FIELDS
            or not control._id(result["generation_id"]) or not control._folder(result["folder_id"])
            or any(not control._hash(result[key]) for key in
                   ("operational_config_sha256", "operational_settings_sha256"))
            or result["fixture_sha256"] != request["fixture"]["sha256"]
            or type(result["fixture_size"]) is not int or result["fixture_size"] != request["fixture"]["size"]
            or any(result[key] is not True for key in
                   ("fixture_uploaded", "fixture_readback_verified", "token_refresh_verified"))):
        _fail("google_test_broker_receipt_invalid")
    return value


def _write_private(directory, name, data, *, mode=0o600):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        os.fchmod(fd, mode)
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                _fail("google_test_broker_storage_invalid")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory)


@contextmanager
def _runtime(settings):
    with preparation._root_directory(settings.runtime_directory) as root:
        preparation._directory_info(root, 0, private=True)
        identifier = secrets.token_hex(16)
        os.mkdir(identifier, 0o700, dir_fd=root)
        os.fsync(root)
        directory = os.open(identifier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            preparation._directory_info(directory, 0, private=True)
            yield settings.runtime_directory / identifier, directory, identifier
        finally:
            os.close(directory)


@contextmanager
def _listeners(path):
    with ExitStack() as stack:
        listeners = {}
        for name in _NAMES:
            listener = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            listener.bind(str(path / (name + ".sock")))
            os.chmod(path / (name + ".sock"), 0o600)
            listener.listen(1)
            listeners[name] = listener
        yield listeners


def _accept(listeners, deadline):
    peers = {}
    try:
        for name in _NAMES:
            listener = listeners[name]
            listener.settimeout(_remaining(deadline, 10))
            peer, _ = listener.accept()
            peer.setblocking(False)
            peers[name] = peer
        return peers
    except BaseException:
        for peer in peers.values():
            peer.close()
        raise


class _Loopback:
    """Nonce-only IPv4 echo controls; no proxying, payload capture or forwarding."""
    def __init__(self, nonce):
        self.nonce = nonce.encode("ascii")
        self.tcp = self.udp = None

    def __enter__(self):
        try:
            self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp.bind(("127.0.0.1", 0))
            self.tcp.listen(2)
            self.port = self.tcp.getsockname()[1]
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("127.0.0.1", self.port))
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def positive(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            tcp.settimeout(1)
            udp.settimeout(1)
            self.tcp.settimeout(1)
            self.udp.settimeout(1)
            tcp.connect(("127.0.0.1", self.port))
            tcp.sendall(self.nonce)
            with self.tcp.accept()[0] as accepted:
                accepted.settimeout(1)
                if accepted.recv(33) != self.nonce:
                    _fail("google_test_broker_canary_invalid")
                accepted.sendall(self.nonce)
            if tcp.recv(33) != self.nonce:
                _fail("google_test_broker_canary_invalid")
            udp.sendto(self.nonce, ("127.0.0.1", self.port))
            data, address = self.udp.recvfrom(33)
            if data != self.nonce:
                _fail("google_test_broker_canary_invalid")
            self.udp.sendto(data, address)
            if udp.recvfrom(33)[0] != self.nonce:
                _fail("google_test_broker_canary_invalid")
        self.tcp.setblocking(False)
        self.udp.setblocking(False)

    def assert_quiet(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.tcp, selectors.EVENT_READ)
            selector.register(self.udp, selectors.EVENT_READ)
            if selector.select(0):
                _fail("google_test_broker_loopback_reached")

    def __exit__(self, *args):
        for channel in (self.tcp, self.udp):
            if channel is not None:
                channel.close()


def _unit_properties(settings, backup, path, resolver):
    return {
        "Type": "exec", "RemainAfterExit": "yes", "Restart": "no", "RuntimeMaxSec": "200",
        "TimeoutStartSec": "10", "TimeoutStopSec": "3", "KillMode": "control-group", "SendSIGKILL": "yes",
        "User": settings.backup_user, "Group": str(backup.pw_gid), "SupplementaryGroups": "",
        "UMask": "0077", "WorkingDirectory": "/", "StandardInput": "null", "StandardOutput": "null",
        "StandardError": "null", "NoNewPrivileges": "yes", "CapabilityBoundingSet": "", "AmbientCapabilities": "",
        "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
        "PrivateNetwork": "no", "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes",
        "ProtectKernelLogs": "yes", "ProtectControlGroups": "yes", "RestrictNamespaces": "yes",
        "RestrictRealtime": "yes", "RestrictSUIDSGID": "yes", "LockPersonality": "yes",
        "MemoryDenyWriteExecute": "yes", "RestrictAddressFamilies": "AF_INET", "SystemCallArchitectures": "native",
        "SystemCallFilter": "~io_uring_setup io_uring_enter io_uring_register ptrace process_vm_readv process_vm_writev pidfd_getfd",
        "SystemCallErrorNumber": "EPERM", "RestrictNetworkInterfaces": "wlan0",
        "ReadOnlyPaths": str(settings.backup_state), "ReadWritePaths": str(settings.operational_state),
        "InaccessiblePaths": str(settings.source_state), "BindReadOnlyPaths": str(resolver) + ":/etc/resolv.conf",
        "Environment": "PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/nonexistent LANG=C LC_ALL=C TZ=UTC GODEBUG=netdns=go",
        "UnsetEnvironment": "PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy SSL_CERT_FILE SSL_CERT_DIR RCLONE_CONFIG RCLONE_CONFIG_PASS RCLONE_PASSWORD_COMMAND",
        # v257.13 parses one colon-delimited spec per property occurrence.
        "OpenFile": tuple(str(path / (name + ".sock")) + ":" + name for name in _NAMES),
    }


def _argv(settings, request):
    return [str(settings.interpreter), "-I", "-B", "-m", "raspi_cloud_sync_pro.google_test_executor",
            "--prepared-state", str(settings.backup_state / request["preparation"]["request_id"]),
            "--operational-state", str(settings.operational_state), "--rclone", str(settings.rclone),
            "--rclone-sha256", settings.rclone_sha256, "--timeout", "120"]


def _trusted_load_path(path):
    for index in range(1, len(path.parts)):
        prefix = Path(*path.parts[:index + 1])
        try:
            info = prefix.lstat()
        except FileNotFoundError:
            break
        if info.st_uid != 0:
            _fail("google_test_broker_unit_conflict")
        if stat.S_ISLNK(info.st_mode):
            with preparation._root_directory(prefix.resolve(strict=True)):
                pass
        elif not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            _fail("google_test_broker_unit_conflict")


def _no_overrides(unit, actual_paths=(), *, deadline=None):
    names = [unit, unit + ".d", "service.d"]
    stem = unit[:-8]
    names += [stem[:index + 1] + ".service.d" for index, char in enumerate(stem) if char == "-"]
    bases = set(actual_paths) | {
        "/run/systemd/system", "/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system",
        "/run/systemd/transient", "/run/systemd/generator", "/run/systemd/generator.early", "/run/systemd/generator.late",
        "/etc/systemd/system.control", "/run/systemd/system.control", "/etc/systemd/system.attached",
        "/run/systemd/system.attached", "/usr/local/lib/systemd/system"}
    for base in bases:
        if deadline is not None:
            _remaining(deadline)
        directory = _path(base)
        _trusted_load_path(directory)
        if any(os.path.lexists(directory / name) for name in names):
            _fail("google_test_broker_unit_conflict")
        try:
            with os.scandir(directory) as entries:
                for count, entry in enumerate(entries):
                    if count >= 4096:
                        _fail("google_test_broker_unit_conflict")
                    if entry.name.endswith((".wants", ".requires")) and os.path.lexists(Path(entry.path) / unit):
                        _fail("google_test_broker_unit_conflict")
        except FileNotFoundError:
            pass


class _Systemd:
    def __init__(self, settings, backup, deadline):
        self.settings, self.backup, self.deadline = settings, backup, deadline

    def _run(self, args):
        code, raw = _command(args, min(self.deadline, time.monotonic() + 5))
        if code:
            _fail("google_test_broker_systemd_unavailable")
        return raw

    def show(self, unit):
        # v257 omits empty fields by default. Absence is not an empty value:
        # startup/capture checks must see e.g. DropInPaths= explicitly. Query
        # only the single-valued fields used here: OpenFile legitimately emits
        # repeated lines, and unrelated structured properties need no parsing.
        raw = self._run(["/usr/bin/systemctl", "show", "--all", "--no-pager",
                         "--property=" + ",".join(_SYSTEMD_PROPERTIES), unit])
        result = {}
        for line in raw.decode("utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in _SYSTEMD_PROPERTIES or key in result:
                _fail("google_test_broker_unit_invalid")
            result[key] = value
        return result

    def start(self, unit, description, properties, arguments):
        actual_paths = self._run(["/usr/bin/systemctl", "show", "--property=UnitPath", "--value"]).decode("utf-8").split()
        if not 0 < len(actual_paths) <= 64:
            _fail("google_test_broker_unit_conflict")
        _no_overrides(unit, actual_paths, deadline=self.deadline)
        current = self.show(unit)
        if (current.get("LoadState") != "not-found" or current.get("ActiveState") != "inactive"
                or current.get("DropInPaths") != ""):
            _fail("google_test_broker_unit_conflict")
        args = ["/usr/bin/systemd-run", "--quiet", "--unit", unit, "--description", description]
        for name, value in properties.items():
            values = value if name == "OpenFile" else (value,)
            args.extend("--property=" + name + "=" + item for item in values)
        self._run(args + ["--"] + arguments)

    def capture(self, unit, description):
        value = self.show(unit)
        if (value.get("Id") != unit or value.get("Description") != description
                or not control._id(value.get("InvocationID")) or value.get("Transient") != "yes"
                or value.get("DropInPaths") != "" or value.get("User") != self.settings.backup_user):
            _fail("google_test_broker_unit_invalid")
        return value["InvocationID"]

    def authenticate(self, unit, description, invocation, arguments):
        value = self.show(unit)
        if (self.capture(unit, description) != invocation or value.get("ActiveState") != "active"
                or value.get("SubState") != "running" or value.get("ControlGroup") != "/system.slice/" + unit
                or value.get("NoNewPrivileges") != "yes" or value.get("CapabilityBoundingSet") != ""
                or value.get("AmbientCapabilities") != "" or value.get("RestrictAddressFamilies") != "AF_INET"
                or value.get("PrivateNetwork") != "no"):
            _fail("google_test_broker_unit_invalid")
        pid = int(value["MainPID"])
        if pid <= 1:
            _fail("google_test_broker_unit_invalid")
        descriptor = os.pidfd_open(pid, 0)
        try:
            identity = _kernel_worker(pid, self.backup, arguments, self.settings)
            if identity[1] != "0::/system.slice/" + unit:
                _fail("google_test_broker_worker_invalid")
            return _Worker(pid, descriptor, identity, "/system.slice/" + unit)
        except BaseException:
            os.close(descriptor)
            raise

    def recheck(self, unit, description, invocation, worker, arguments):
        if self.capture(unit, description) != invocation:
            _fail("google_test_broker_unit_changed")
        if _kernel_worker(worker.pid, self.backup, arguments, self.settings) != worker.identity:
            _fail("google_test_broker_worker_changed")
        with selectors.DefaultSelector() as selector:
            selector.register(worker.pidfd, selectors.EVENT_READ)
            if selector.select(0):
                _fail("google_test_broker_worker_changed")

    def wait(self, unit, description, invocation, worker, deadline):
        with selectors.DefaultSelector() as selector:
            selector.register(worker.pidfd, selectors.EVENT_READ)
            if not selector.select(_remaining(deadline, 130)):
                _fail("google_test_broker_timeout")
        value = self.show(unit)
        if (self.capture(unit, description) != invocation or value.get("MainPID") != "0"
                or value.get("ExecMainCode") != "1" or value.get("ExecMainStatus") != "0"
                or value.get("Result") != "success"):
            _fail("google_test_broker_worker_failed")
        if _cgroup_pids(worker.control_group, missing=True):
            _fail("google_test_broker_worker_failed")

    def complete_recheck(self, unit, description, invocation, worker, arguments):
        self.recheck(unit, description, invocation, worker, arguments)
        if _cgroup_pids(worker.control_group) != (worker.pid,):
            _fail("google_test_broker_completion_invalid")
        # All rclone children/config/history descriptors are gone. The pinned
        # bootstrap retains just one already-connected Unix completion stream;
        # no Internet socket or additional descendant may survive the barrier.
        sockets = []
        with os.scandir("/proc/" + str(worker.pid) + "/fd") as entries:
            count = 0
            for entry in entries:
                count += 1
                if count > 64 or len(sockets) > 1:
                    _fail("google_test_broker_completion_invalid")
                link = os.readlink(entry.path)
                match = re.fullmatch(r"socket:\[([0-9]+)\]", link)
                if match:
                    sockets.append(match[1])
        unix = _proc_bytes(worker.pid, "net/unix", 65536).decode("ascii").splitlines()[1:]
        unix_inodes = {line.split()[6] for line in unix if len(line.split()) >= 7}
        if len(sockets) != 1 or sockets[0] not in unix_inodes:
            _fail("google_test_broker_completion_invalid")

    def stop(self, unit, description, invocation):
        # Never stop an adopted unit, another invocation, or an unconfirmed tag.
        previous_deadline = self.deadline
        self.deadline = time.monotonic() + 5
        try:
            value = self.show(unit)
            if value.get("LoadState") == "not-found":
                # A refused start may never create a unit. Confirm absence
                # without adopting/stopping a differently identified unit.
                if (value.get("Id") != unit or value.get("ActiveState") != "inactive"
                        or value.get("SubState") != "dead" or value.get("MainPID") != "0"
                        or value.get("ControlPID") != "0" or value.get("Transient") != "no"
                        or value.get("FragmentPath") != "" or value.get("DropInPaths") != ""
                        or value.get("InvocationID") != "" or value.get("ControlGroup") != ""
                        or _cgroup_pids("/system.slice/" + unit, missing=True)):
                    _fail("google_test_broker_cleanup_unconfirmed")
                return
            current = self.capture(unit, description)
            if invocation is not None and current != invocation:
                _fail("google_test_broker_cleanup_unconfirmed")
            self._run(["/usr/bin/systemctl", "stop", unit])
            value = self.show(unit)
            if (value.get("Id") != unit or value.get("MainPID") != "0" or value.get("ControlPID") != "0"
                    or value.get("ActiveState") not in ("inactive", "failed")
                    or (value.get("LoadState") != "not-found" and value.get("Description") != description)
                    or _cgroup_pids("/system.slice/" + unit, missing=True)):
                _fail("google_test_broker_cleanup_unconfirmed")
        finally:
            self.deadline = previous_deadline


@dataclass(repr=False)
class _Worker:
    pid: int
    pidfd: int
    identity: tuple
    control_group: str

    def __repr__(self):
        return "<GoogleTestWorker private>"

    def close(self):
        os.close(self.pidfd)


def _proc_bytes(pid, name, limit):
    with open("/proc/" + str(pid) + "/" + name, "rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        _fail("google_test_broker_worker_invalid")
    return value


def _cgroup_pids(control_group, *, missing=False):
    if re.fullmatch(r"/system.slice/raspi-cloud-google-test-[0-9a-f]{32}\.service", control_group) is None:
        _fail("google_test_broker_completion_invalid")
    try:
        with open("/sys/fs/cgroup" + control_group + "/cgroup.procs", "rb") as stream:
            raw = stream.read(4097)
    except FileNotFoundError:
        if missing:
            return ()
        raise
    if len(raw) > 4096:
        _fail("google_test_broker_completion_invalid")
    return tuple(sorted(int(pid) for pid in raw.split()))


def _kernel_worker(pid, account, arguments, settings):
    status = dict(line.split(":", 1) for line in _proc_bytes(pid, "status", 16384).decode("ascii").splitlines() if ":" in line)
    if (any([int(part) for part in status[key].split()] != [expected] * 4
            for key, expected in (("Uid", account.pw_uid), ("Gid", account.pw_gid)))
            or any(int(part) != account.pw_gid for part in status["Groups"].split())
            or any(int(status[key].strip(), 16) != 0 for key in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"))
            or status["NoNewPrivs"].strip() != "1" or status["Seccomp"].strip() != "2"
            or _proc_bytes(pid, "cmdline", 16384) != b"\0".join(argument.encode() for argument in arguments) + b"\0"
            or os.readlink("/proc/" + str(pid) + "/exe") != str(settings.interpreter)
            or os.readlink("/proc/" + str(pid) + "/ns/net") != os.readlink("/proc/1/ns/net")):
        _fail("google_test_broker_worker_invalid")
    group = _proc_bytes(pid, "cgroup", 4096).decode("ascii").strip()
    if re.fullmatch(r"0::/system.slice/raspi-cloud-google-test-[0-9a-f]{32}\.service", group) is None:
        _fail("google_test_broker_worker_invalid")
    for path, readonly in (("/etc", True), ("/usr", True), (str(settings.backup_state), True),
                           (str(settings.operational_state), False)):
        observed = bool(os.statvfs("/proc/" + str(pid) + "/root" + path).f_flag & os.ST_RDONLY)
        if observed is not readonly:
            _fail("google_test_broker_worker_invalid")
    raw_stat = _proc_bytes(pid, "stat", 4096)
    start = raw_stat.rsplit(b") ", 1)[1].split()[19]
    return start, group


def _network(settings, deadline):
    def query(arguments):
        code, raw = _command(arguments, min(deadline, time.monotonic() + 3), limit=8192)
        if code:
            _fail("google_test_broker_wifi_unavailable")
        return raw.decode("ascii").strip()
    values = {}
    for name in ("GENERAL.TYPE", "GENERAL.STATE", "GENERAL.CON-UUID", "GENERAL.METERED", "IP4.DNS"):
        values[name] = query(["/usr/bin/nmcli", "--escape", "no", "-g", name, "device", "show", "wlan0"])
    metered = values["GENERAL.METERED"]
    if (values["GENERAL.TYPE"] != "wifi" or values["GENERAL.STATE"] != "100 (connected)"
            or values["GENERAL.CON-UUID"] != settings.wifi_profile_uuid
            or values["IP4.DNS"] != settings.dns_ipv4
            or metered not in ("yes", "no", "yes (guessed)", "no (guessed)")
            or (not settings.allow_metered and metered in ("yes", "yes (guessed)"))
            or query(["/usr/bin/nmcli", "-g", "CONNECTIVITY", "general"]) != "full"):
        _fail("google_test_broker_wifi_unavailable")
    route = json.loads(query(["/usr/sbin/ip", "-j", "-4", "route", "get", settings.dns_ipv4]), object_pairs_hook=_unique_object)
    if not isinstance(route, list) or len(route) != 1 or route[0].get("dev") != "wlan0":
        _fail("google_test_broker_wifi_unavailable")
    return {"interface_index": socket.if_nametoindex("wlan0"), "values": values, "route": route}


def _canary_ready(peer, worker, deadline):
    from .google_test_executor import canary_read_frame
    value = canary_read_frame(peer.fileno(), deadline)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "provider", "phase", "worker_pid"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["provider"] != "google_drive" or value["phase"] != "ready"
            or type(value["worker_pid"]) is not int or value["worker_pid"] != worker.pid):
        _fail("google_test_broker_canary_invalid")


def _canary(peer, binding, loopback, deadline):
    from .google_test_executor import canary_read_frame, canary_write_frame
    canary_write_frame(peer.fileno(), {**binding, "phase": "go"}, deadline)
    report = canary_read_frame(peer.fileno(), deadline)
    expected = {**binding, "phase": "report"}
    if (not isinstance(report, dict) or set(report) != set(expected) | _CANARY_FLAGS | {"official_https_bytes"}
            or any(report[key] != value or type(report[key]) is not type(value) for key, value in expected.items())
            or any(report[key] is not True for key in _CANARY_FLAGS)
            or type(report["official_https_bytes"]) is not int or report["official_https_bytes"] != 64):
        _fail("google_test_broker_canary_invalid")
    # REPORT must be the final worker->root canary frame, not a prefix of a
    # concatenated stream. Existing handoff reader enforces EOF independently.
    with selectors.DefaultSelector() as selector:
        selector.register(peer, selectors.EVENT_READ)
        if not selector.select(_remaining(deadline, _CANARY_TIMEOUT)) or peer.recv(1):
            _fail("google_test_broker_canary_invalid")
    loopback.assert_quiet()


def _ack(peer, binding, deadline):
    from .google_test_executor import canary_write_frame
    canary_write_frame(peer.fileno(), {**binding, "phase": "ack"}, deadline)
    peer.shutdown(socket.SHUT_WR)


def _release(peers, request, worker, deadline):
    destination._write_frame(peers["request"].fileno(), destination._canonical(request),
                             time.monotonic() + _remaining(deadline))
    peers["request"].shutdown(socket.SHUT_WR)
    release = {"schema_version": 1, "provider": "google_drive", "purpose": "google_test_wifi_egress",
               "request_id": request["request_id"], "worker_pid": worker.pid, "expires_at": time.time() + 20}
    destination._write_frame(peers["gate"].fileno(), destination._canonical(release),
                             time.monotonic() + _remaining(deadline))
    peers["gate"].shutdown(socket.SHUT_WR)


def _complete(peer, binding, deadline):
    value = {key: binding[key] for key in ("schema_version", "provider", "worker_pid", "request_id", "nonce")}
    value["phase"] = "complete"
    destination._write_frame(peer.fileno(), destination._canonical(value),
                             time.monotonic() + _remaining(deadline))
    peer.shutdown(socket.SHUT_WR)


def _failed_receipt(request):
    return {"schema_version": 1, "provider": "google_drive", "request_id": request["request_id"],
            "action": "test", "preparation": request["preparation"], "test": None,
            "completed_at": time.time(), "outcome": "failed", "enabled": False, "automatic_deletion": False,
            "result": {"generation_id": None, "folder_id": None, "operational_config_sha256": None,
                "operational_settings_sha256": None, "fixture_sha256": request["fixture"]["sha256"],
                "fixture_size": request["fixture"]["size"], "fixture_uploaded": False,
                "fixture_readback_verified": False, "token_refresh_verified": False}}


def _orchestrate(settings, source, backup, source_fd, deadline, manager, source_transport):
    request_id = _current_request(source_fd, source.pw_uid)
    if request_id is None:
        return _result("idle", "google_test_broker_no_request")
    exported = source_transport.export(request_id, deadline)
    if isinstance(exported, dict) and exported.get("action") in ("enable", "disable"):
        return _result("idle", "google_test_broker_no_test")
    request = _request(exported, request_id)
    network = _network(settings, deadline)
    with _runtime(settings) as (path, directory, identifier):
        unit = "raspi-cloud-google-test-" + identifier + ".service"
        description = "Private Google Test " + identifier
        resolver = path / "resolv.conf"
        # Non-secret DNS policy must remain readable after the bind mount by
        # the ordinary worker. The enclosing runtime remains root-only 0700.
        _write_private(directory, "resolv.conf", ("nameserver " + settings.dns_ipv4 + "\noptions timeout:2 attempts:1\n").encode("ascii"), mode=0o644)
        arguments = _argv(settings, request)
        properties = _unit_properties(settings, backup, path, resolver)
        # Retained before activation: cleanup owns only this fresh name/tag and
        # a matching manager-confirmed invocation. No adopted/global lifecycle.
        _write_private(directory, "plan.json", destination._canonical({"schema_version": 1, "unit": unit,
            "description": description, "arguments": arguments, "properties": properties,
            "cleanup": "stop only matching description and captured InvocationID; retain all state"}))
        invocation = None
        started = False
        peers = {}
        worker = None
        result = _result("failed", "google_test_broker_unavailable")
        cleanup_ok = True
        try:
            with _listeners(path) as listeners, _Loopback(secrets.token_hex(16)) as loopback:
                loopback.positive()
                started = True  # Even a timed-out start may have reached PID1.
                manager.start(unit, description, properties, arguments)
                invocation = manager.capture(unit, description)
                peers = _accept(listeners, min(deadline, time.monotonic() + 10))
                worker = manager.authenticate(unit, description, invocation, arguments)
                canary_deadline = min(deadline, time.monotonic() + _CANARY_TIMEOUT)
                _canary_ready(peers["canary"], worker, canary_deadline)
                from .wifi_confinement import hold_wifi_confinement
                with hold_wifi_confinement(worker.control_group, network["interface_index"]) as proof:
                    proof.recheck()
                    manager.recheck(unit, description, invocation, worker, arguments)
                    binding = {"schema_version": 1, "provider": "google_drive", "worker_pid": worker.pid,
                               "request_id": request_id, "nonce": loopback.nonce.decode("ascii"),
                               "dns_ipv4": settings.dns_ipv4, "loopback_port": loopback.port}
                    _canary(peers["canary"], binding, loopback, canary_deadline)
                    proof.recheck()
                    loopback.positive()
                    if _network(settings, deadline) != network or source_transport.export(request_id, deadline) != request:
                        _fail("google_test_broker_authority_changed")
                    proof.recheck()
                    manager.recheck(unit, description, invocation, worker, arguments)
                    _request(request, request_id)  # Check current wall-clock expiry again.
                    _ack(peers["canary"], binding, canary_deadline)
                    _release(peers, request, worker, deadline)
                    # The worker closes the receipt stream on completion. Root
                    # validates only private evidence; source recorder remains
                    # the independent stable-settings/source authority.
                    fd = os.dup(peers["receipt"].fileno())
                    received = preparation._receive(fd, deadline, 65536)
                    receipt = _receipt(received, request)
                    # Native PID1 cannot preserve the tested proof after exit.
                    # The bootstrap stays alive without cloud/config activity,
                    # retaining only the completion stream until this ACK.
                    loopback.assert_quiet()
                    loopback.positive()
                    if _network(settings, deadline) != network or source_transport.export(request_id, deadline) != request:
                        _fail("google_test_broker_authority_changed")
                    manager.complete_recheck(unit, description, invocation, worker, arguments)
                    proof.recheck()
                    _complete(peers["receipt"], binding, deadline)
                    manager.wait(unit, description, invocation, worker, deadline)
                    source_transport.record(request_id, receipt, deadline)
                    result = _result("tested", "google_test_broker_test_verified_not_enabled")
        except GoogleTestBrokerError as error:
            result = _result("failed", error.code)
        except Exception:
            result = _result("failed", "google_test_broker_unavailable")
        finally:
            for peer in peers.values():
                peer.close()
            if worker is not None:
                worker.close()
            if started:
                try:
                    manager.stop(unit, description, invocation)
                except Exception:
                    cleanup_ok = False
            if not cleanup_ok:
                result = _result("failed", "google_test_broker_cleanup_unconfirmed")
            elif result["status"] == "failed" and result["code"] != "google_test_broker_authority_changed":
                try:
                    # A failure is historical, never permission. Record only
                    # after confirmed cleanup and a fresh same live authority;
                    # the source independently refuses revocation/overwrite.
                    fresh = source_transport.export(request_id, deadline)
                    if fresh == request:
                        _request(fresh, request_id)
                        source_transport.record(request_id, _failed_receipt(request), deadline)
                except Exception:
                    pass
            _write_private(directory, "result.json", destination._canonical(result))
        return result


def _python_environment(settings):
    if (settings.interpreter != Path(sys.executable) or not sys.flags.isolated or not sys.dont_write_bytecode):
        _fail("google_test_broker_installation_unsafe")
    with preparation._root_directory(Path(sys.prefix)):
        pass
    package = Path(__file__).parent
    sites = []
    for value in sys.path:
        path = _path(value)
        if path.is_dir():
            with preparation._root_directory(path):
                pass
            if path.name == "site-packages" and Path(sys.prefix) in path.parents:
                sites.append(path)
        elif path.exists():
            with preparation._root_file(path):
                pass
        else:
            with preparation._root_directory(path.parent):
                pass
    if not any(site in package.parents for site in sites):
        _fail("google_test_broker_installation_unsafe")
    count = 0
    for site in sites:
        for directory, names, files in os.walk(site, followlinks=False):
            for name in names + files:
                count += 1
                info = (Path(directory) / name).lstat()
                if (count > 16384 or info.st_uid != 0 or info.st_mode & 0o022
                        or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                    _fail("google_test_broker_installation_unsafe")


def _gates(settings):
    preparation._platform_gate()
    if threading.active_count() != 1:
        _fail("google_test_broker_single_thread_required")
    if settings.config_path is None or GoogleTestBrokerSettings.load(settings.config_path) != settings:
        _fail("google_test_broker_configuration_changed")
    approval, _ = preparation._root_json(settings.approval_file, private=True)
    if approval != _APPROVAL or type(approval.get("schema_version")) is not int or approval.get("google_test_broker_approved") is not True:
        _fail("google_test_broker_approval_required")
    _python_environment(settings)
    preparation._installed_package_gate()
    for executable in (settings.interpreter, settings.rclone, Path("/usr/bin/systemd-run"),
                       Path("/usr/bin/systemctl"), Path("/usr/bin/nmcli"), Path("/usr/sbin/ip")):
        with preparation._root_file(executable, executable=True) as (fd, info):
            if stat.S_IMODE(info.st_mode) != 0o755:
                _fail("google_test_broker_installation_unsafe")
            if executable == settings.rclone:
                digest = hashlib.sha256()
                if not 0 < info.st_size <= 128 * 1024 * 1024:
                    _fail("google_test_broker_installation_unsafe")
                while True:
                    block = os.read(fd, 65536)
                    if not block:
                        break
                    digest.update(block)
                if (digest.hexdigest() != settings.rclone_sha256
                        or destination._file_identity(os.fstat(fd)) != destination._file_identity(info)):
                    _fail("google_test_broker_installation_unsafe")
    code, raw = _command(["/usr/bin/systemctl", "--version"], time.monotonic() + 3, limit=4096)
    if code or re.fullmatch(rb"systemd 257 \(257\.13[^\n]*\)", raw.splitlines()[0]) is None:
        _fail("google_test_broker_manager_unapproved")
    if not hasattr(os, "pidfd_open"):
        _fail("google_test_broker_pidfd_required")


def run_google_test_broker(settings):
    """One explicit root Test coordination; no public native-proof bypass flags."""
    if not isinstance(settings, GoogleTestBrokerSettings):
        return _result("failed", "google_test_broker_configuration_invalid")
    if not settings.enabled:
        return _result("disabled", "google_test_broker_disabled")
    try:
        _gates(settings)
        source = preparation._service_identity(settings.dashboard_user)
        backup = preparation._service_identity(settings.backup_user)
        if source.pw_uid == backup.pw_uid or source.pw_gid == backup.pw_gid:
            _fail("google_test_broker_identity_unsafe")
        deadline = time.monotonic() + _TIMEOUT
        with preparation._state_directory(settings.source_state, source.pw_uid) as source_fd, \
                preparation._state_directory(settings.backup_state, backup.pw_uid), \
                preparation._state_directory(settings.operational_state, backup.pw_uid), \
                preparation._root_directory(settings.runtime_directory) as runtime:
            preparation._directory_info(runtime, 0, private=True)
            with preparation._broker_lock(settings.lock_file):
                return _orchestrate(settings, source, backup, source_fd, deadline,
                                    _Systemd(settings, backup, deadline), _Source(settings, source))
    except GoogleTestBrokerError as error:
        return _result("failed", error.code)
    except Exception:
        return _result("failed", "google_test_broker_unavailable")
