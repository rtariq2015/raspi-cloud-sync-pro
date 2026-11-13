"""Opt-in, local-only broker for disabled Google destination preparation.

Only the two ordinary identities handle credentials. The root coordinator sees
bounded private metadata and receipts, never authorization bytes or child output.
There are no network, account, service, worker-enable or recovery-delete actions.
"""

import ctypes
import fcntl
import hashlib
import hmac
import json
import math
import os
import platform
import pwd
import re
import resource
import selectors
import signal
import stat
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import google_destination as destination
from .config import _unique_object
from .errors import BackupError

_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_MODEL = b"Raspberry Pi Zero 2 W Rev 1.0\x00"
_APPROVAL = {"schema_version": 1, "google_setup_broker_approved": True}
_COMMAND_TIMEOUT = 5.0
_BROKER_TIMEOUT = 20.0
_METADATA_LIMIT = 4096
_SETTINGS_FIELDS = {
    "schema_version", "enabled", "interpreter", "source_state", "backup_state",
    "dashboard_user", "backup_user", "public_origin", "approval_file", "lock_file",
}
_RECEIPT_FIELDS = {
    "schema_version", "provider", "request_id", "authorization_sha256", "folder_name",
    "rclone_config_sha256",
    "configuration_matches", "prepared_at", "enabled", "test_required",
    "account_verified", "folder_exists_verified", "automatic_deletion",
}
_FAILURES = (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError,
             OverflowError, RuntimeError, subprocess.SubprocessError)


class GoogleBrokerError(BackupError):
    """Fixed public codes; paths, subprocess output and credentials never attach."""


def _fail(code):
    raise GoogleBrokerError(code) from None


def _result(status, code):
    return {"status": status, "code": code, "uploads_enabled": False,
            "account_verified": False}


def _absolute(value):
    if not isinstance(value, (str, Path)):
        _fail("google_broker_configuration_invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        _fail("google_broker_configuration_invalid")
    return path


def _directory_info(fd, uid, *, private=False):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
            or (stat.S_IMODE(info.st_mode) != 0o700 if private else info.st_mode & 0o022)):
        _fail("google_broker_path_unsafe")
    return info


@contextmanager
def _root_directory(path):
    """Every ancestor is root-owned and nonwritable, including the leaf."""
    path = _absolute(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _directory_info(fd, 0)
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            os.close(fd)
            fd = child
            _directory_info(fd, 0)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _root_file(path, *, private=False, executable=False):
    path = _absolute(path)
    with _root_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or (stat.S_IMODE(info.st_mode) != 0o600 if private else info.st_mode & 0o022)
                    or (executable and not info.st_mode & 0o111)):
                _fail("google_broker_path_unsafe")
            yield fd, info
        finally:
            os.close(fd)


def _read_bytes(fd, info, limit, code):
    if not 0 < info.st_size <= limit:
        _fail(code)
    raw = bytearray()
    while len(raw) <= limit:
        block = os.read(fd, min(8192, limit + 1 - len(raw)))
        if not block:
            break
        raw.extend(block)
    if len(raw) > limit or destination._file_identity(os.fstat(fd)) != destination._file_identity(info):
        _fail(code)
    return bytes(raw)


def _root_json(path, *, private=False):
    with _root_file(path, private=private) as (fd, info):
        data = json.loads(_read_bytes(fd, info, 8192, "google_broker_configuration_invalid"),
                          object_pairs_hook=_unique_object)
        return data, destination._file_identity(info)


@dataclass(frozen=True, repr=False)
class GoogleBrokerSettings:
    enabled: bool = False
    interpreter: Path = None
    source_state: Path = None
    backup_state: Path = None
    dashboard_user: str = None
    backup_user: str = None
    public_origin: str = None
    approval_file: Path = None
    lock_file: Path = None
    config_path: Path = field(default=None, repr=False)
    _identity: tuple = field(default=None, repr=False)

    def __repr__(self):
        return "<GoogleBrokerSettings protected>"

    @classmethod
    def load(cls, root_config_path):
        try:
            path = _absolute(root_config_path)
            data, identity = _root_json(path)
            if (not isinstance(data, dict) or set(data) - _SETTINGS_FIELDS
                    or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                    or type(data.get("enabled", False)) is not bool):
                _fail("google_broker_configuration_invalid")
            if not data.get("enabled", False):
                return cls(config_path=path, _identity=identity)
            if set(data) != _SETTINGS_FIELDS:
                _fail("google_broker_configuration_invalid")
            values = {key: _absolute(data[key]) for key in
                      ("interpreter", "source_state", "backup_state", "approval_file", "lock_file")}
            for key in ("dashboard_user", "backup_user"):
                if not isinstance(data[key], str) or _USER.fullmatch(data[key]) is None:
                    _fail("google_broker_configuration_invalid")
                values[key] = data[key]
            if (values["source_state"] == values["backup_state"]
                    or values["dashboard_user"] == values["backup_user"]):
                _fail("google_broker_configuration_invalid")
            values["public_origin"] = destination._origin(data["public_origin"])
            if values["public_origin"] != data["public_origin"]:
                _fail("google_broker_configuration_invalid")
            return cls(enabled=True, config_path=path, _identity=identity, **values)
        except GoogleBrokerError:
            raise
        except (destination.GoogleDestinationError,) + _FAILURES:
            _fail("google_broker_configuration_invalid")


def _platform_gate():
    if os.getuid() != 0 or os.geteuid() != 0:
        _fail("google_broker_root_required")
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        _fail("google_broker_hardware_unapproved")
    with open("/proc/device-tree/model", "rb") as stream:
        if stream.read(128) != _MODEL:
            _fail("google_broker_hardware_unapproved")


def _service_identity(name):
    # Read local protected databases directly: NSS lookup may use a network, and
    # spwd is absent in Python 3.13+. No password/hash value leaves this helper.
    def records(path, columns):
        with _root_file(path) as (fd, info):
            raw = _read_bytes(fd, info, 1024 * 1024, "google_broker_identity_unsafe")
        rows = [line.split(":") for line in raw.decode("utf-8").splitlines()
                if line.split(":", 1)[0] == name]
        if len(rows) != 1 or len(rows[0]) != columns:
            _fail("google_broker_identity_unsafe")
        return rows[0]

    row = records(Path("/etc/passwd"), 7)
    account = pwd.struct_passwd((row[0], row[1], int(row[2]), int(row[3]), row[4], row[5], row[6]))
    locked = records(Path("/etc/shadow"), 9)[1]
    if (account.pw_uid <= 0 or account.pw_gid <= 0
            or account.pw_shell not in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false")
            or not isinstance(locked, str) or not locked.startswith(("!", "*"))):
        _fail("google_broker_identity_unsafe")
    return account


def _installed_package_gate():
    # -I excludes cwd/PYTHONPATH/user site; every installed module and package
    # ancestor must additionally be protected against either service identity.
    package = _absolute(Path(__file__).parent)
    with _root_directory(package):
        count = 0
        for directory, names, files in os.walk(package, followlinks=False):
            for name in names + files:
                count += 1
                if count > 4096:
                    _fail("google_broker_installation_unsafe")
                info = (Path(directory) / name).lstat()
                if (info.st_uid != 0 or info.st_mode & 0o022
                        or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
                    _fail("google_broker_installation_unsafe")


@contextmanager
def _state_directory(path, owner):
    """The private leaf is service-owned; its ancestors stay root-owned."""
    path = _absolute(path)
    with _root_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=parent)
        try:
            _directory_info(fd, owner, private=True)
            yield fd
        finally:
            os.close(fd)


@contextmanager
def _broker_lock(path):
    with _root_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                _fail("google_broker_lock_unsafe")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _fail("google_broker_busy")
            yield
        finally:
            os.close(fd)


def _current_request(source_fd, owner):
    try:
        fd = os.open("google-setup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=source_fd)
    except FileNotFoundError:
        return None
    try:
        _directory_info(fd, owner, private=True)
        try:
            pointer = _read_private_bytes(fd, "current.json", owner, 256)
        except FileNotFoundError:
            return None
        value = json.loads(pointer, object_pairs_hook=_unique_object)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "request_id"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["request_id"], str) or _ID.fullmatch(value["request_id"]) is None):
            _fail("google_broker_request_invalid")
        return value["request_id"]
    finally:
        os.close(fd)


def _read_private_bytes(root, name, owner, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            _fail("google_preparation_receipt_invalid")
        raw = _read_bytes(fd, info, limit, "google_preparation_receipt_invalid")
        if destination._file_identity(os.stat(name, dir_fd=root, follow_symlinks=False)) != destination._file_identity(info):
            _fail("google_preparation_receipt_invalid")
        return raw
    finally:
        os.close(fd)


def _prctl(option, argument):
    if ctypes.CDLL(None, use_errno=True).prctl(option, argument, 0, 0, 0):
        raise OSError("privilege setup failed")


def _clear_capabilities():
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]
    header, data = Header(0x20080522, 0), (Data * 2)()
    if ctypes.CDLL(None, use_errno=True).capset(ctypes.byref(header), ctypes.byref(data)):
        raise OSError("privilege setup failed")


def _drop_identity(uid, gid):
    os.umask(0o077)
    _prctl(8, 0)  # PR_SET_KEEPCAPS: never retain root capabilities on UID drop.
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    _clear_capabilities()
    _prctl(38, 1)  # PR_SET_NO_NEW_PRIVS.
    _prctl(4, 0)  # PR_SET_DUMPABLE.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if (os.getresuid() != (uid, uid, uid) or os.getresgid() != (gid, gid, gid)
            or os.getgroups()):
        raise OSError("privilege setup failed")
    with open("/proc/self/status", encoding="ascii") as stream:
        status = dict(line.rstrip().split(":", 1) for line in stream if ":" in line)
    if (any(int(status[key].strip(), 16) != 0 for key in ("CapEff", "CapPrm", "CapInh", "CapAmb"))
            or status["NoNewPrivs"].strip() != "1"):
        raise OSError("privilege setup failed")


def _make_target(backup_fd, request_id, uid):
    """Runs only after UID drop: no root mkdir, path-following or chown race."""
    _directory_info(backup_fd, uid, private=True)
    try:
        os.mkdir(request_id, mode=0o700, dir_fd=backup_fd)
        os.fsync(backup_fd)
    except FileExistsError:
        pass
    fd = os.open(request_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                 dir_fd=backup_fd)
    try:
        _directory_info(fd, uid, private=True)
    finally:
        os.close(fd)


@contextmanager
def _backup_anchor(backup_state, root_fd):
    """Consume only a private directory FD matching the configured root inode."""
    accepted = False
    try:
        if type(root_fd) is not int or root_fd < 3:
            _fail("google_broker_anchor_invalid")
        info = _directory_info(root_fd, os.geteuid(), private=True)
        identity = destination._descriptor_identity(root_fd)
        for standard in (0, 1, 2):
            try:
                if destination._descriptor_identity(standard) == identity:
                    _fail("google_broker_anchor_invalid")
            except OSError:
                pass
        with destination._private_directory(backup_state) as configured:
            expected = os.fstat(configured)
            if (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
                _fail("google_broker_anchor_invalid")
        accepted = True
        yield root_fd
    finally:
        if accepted:
            os.close(root_fd)


def import_requested_google_setup(backup_state, request_id, root_fd,
                                  authorization_fd, folder_name):
    """Post-exec, ordinary-UID target creation followed by the existing importer.

    The parent already tracks this child before any target mkdir/fsync/open.
    Accepted descriptors are consumed; no ownership changes or root writes occur.
    """
    private_input = None
    try:
        destination._unprivileged()
        with _backup_anchor(backup_state, root_fd) as root:
            # Consume the supplied channel up front and keep one private local
            # duplicate until existing prepare_google_destination takes ownership.
            with destination._channel(authorization_fd, writing=False) as channel:
                private_input = fcntl.fcntl(channel, fcntl.F_DUPFD_CLOEXEC, 3)
            if not isinstance(request_id, str) or _ID.fullmatch(request_id) is None:
                _fail("google_broker_request_invalid")
            folder_name = destination._folder_name(folder_name)
            _make_target(root, request_id, os.geteuid())
        handed_off, private_input = private_input, None
        return destination.prepare_google_destination(
            Path(backup_state) / request_id, handed_off, folder_name)
    except GoogleBrokerError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_broker_import_failed")
    finally:
        if private_input is not None:
            os.close(private_input)


def _spawn(settings, account, arguments, pass_fds):
    if threading.active_count() != 1:
        _fail("google_broker_single_thread_required")
    def prepare():
        _drop_identity(account.pw_uid, account.pw_gid)

    return subprocess.Popen(
        [str(settings.interpreter), "-I", "-B", "-m", "raspi_cloud_sync_pro"] + arguments,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/nonexistent"},
        cwd="/", close_fds=True, pass_fds=tuple(pass_fds), start_new_session=True,
        preexec_fn=prepare,
    )


def _receive(fd, deadline, limit=_METADATA_LIMIT):
    with destination._channel(fd, writing=False):
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)

            def exact(length):
                raw = bytearray()
                while len(raw) < length:
                    destination._wait(selector, deadline)
                    try:
                        block = os.read(fd, length - len(raw))
                    except BlockingIOError:
                        continue
                    if not block:
                        _fail("google_broker_private_report_invalid")
                    raw.extend(block)
                return bytes(raw)

            length = int.from_bytes(exact(4), "big")
            if not 0 < length <= limit:
                _fail("google_broker_private_report_invalid")
            raw = exact(length)
            destination._wait(selector, deadline)
            if os.read(fd, 1):
                _fail("google_broker_private_report_invalid")
            return json.loads(raw, object_pairs_hook=_unique_object)


def _metadata(value, request_id):
    if (not isinstance(value, dict) or set(value) != {
            "schema_version", "provider", "request_id", "folder_name", "authorization_sha256", "rclone_config_sha256"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["provider"] != "google_drive" or value["request_id"] != request_id
            or any(not isinstance(value[key], str) or _HASH.fullmatch(value[key]) is None
                   for key in ("authorization_sha256", "rclone_config_sha256"))):
        _fail("google_broker_private_report_invalid")
    destination._folder_name(value["folder_name"])
    return value


def _receipt_matches(value, metadata):
    if (not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS
            or any(value[key] != metadata[key] for key in
                   ("schema_version", "provider", "request_id", "folder_name", "authorization_sha256", "rclone_config_sha256"))
            or type(value["schema_version"]) is not int
            or value["configuration_matches"] is not True or value["enabled"] is not False
            or value["test_required"] is not True or value["account_verified"] is not False
            or value["folder_exists_verified"] is not False or value["automatic_deletion"] is not False
            or type(value["prepared_at"]) not in (int, float)
            or not math.isfinite(value["prepared_at"]) or not 0 <= value["prepared_at"] <= time.time()):
        _fail("google_broker_private_report_mismatch")
    return value


def _wait_child(child, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("google_broker_timeout")
    try:
        return child.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        _fail("google_broker_timeout")


def _stop_children(children):
    for child in children:
        if child.poll() is None:
            try:
                # Every child was started with its own new session/process group.
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


def _private_pipe():
    """Keep dedicated inherited endpoints above stdio, even for detached callers."""
    ends = list(os.pipe())
    try:
        for index, fd in enumerate(ends):
            if fd < 3:
                ends[index] = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
                os.close(fd)
        return tuple(ends)
    except BaseException:
        for fd in ends:
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _orchestrate(settings, source, backup, source_fd, backup_fd):
    request_id = _current_request(source_fd, source.pw_uid)
    if request_id is None:
        return _result("idle", "google_broker_no_request")
    opened, children = set(), []
    deadline = time.monotonic() + _BROKER_TIMEOUT

    def pipe():
        ends = _private_pipe()
        opened.update(ends)
        return ends

    def close(fd):
        opened.discard(fd)
        os.close(fd)

    def receive(fd, end):
        opened.discard(fd)  # _receive consumes its validated anonymous pipe.
        return _receive(fd, end)

    def start(account, args, fds, **kwargs):
        child = _spawn(settings, account, args, fds, **kwargs)
        children.append(child)
        return child

    try:
        meta_read, meta_write = pipe()
        auth_read, auth_write = pipe()
        pair_deadline = min(deadline, time.monotonic() + _COMMAND_TIMEOUT)
        exporter = start(source, ["google-setup-export", "--state", str(settings.source_state),
            "--public-origin", settings.public_origin, "--request-id", request_id,
            "--metadata-fd", str(meta_write), "--authorization-fd", str(auth_write)],
            (meta_write, auth_write))
        close(meta_write)
        close(auth_write)
        metadata = _metadata(receive(meta_read, pair_deadline), request_id)
        target = settings.backup_state / request_id
        importer = start(backup, ["google-setup-import", "--state", str(settings.backup_state),
            "--request-id", request_id, "--root-fd", str(backup_fd),
            "--input-fd", str(auth_read), "--folder-name", metadata["folder_name"]],
            (auth_read, backup_fd))
        close(auth_read)
        importer_code = _wait_child(importer, pair_deadline)
        exporter_code = _wait_child(exporter, pair_deadline)
        # A refused overwrite can break the export pipe. Recovery still requires
        # a fresh authoritative metadata frame and matching validated receipt.
        if not importer_code and exporter_code:
            _fail("google_broker_export_failed")
        read_fd, write_fd = pipe()
        receipt_deadline = min(deadline, time.monotonic() + _COMMAND_TIMEOUT)
        inspector = start(backup, ["google-setup-receipt", "--state", str(target),
            "--request-id", request_id, "--output-fd", str(write_fd)], (write_fd,))
        close(write_fd)
        receipt = _receipt_matches(receive(read_fd, receipt_deadline), metadata)
        if _wait_child(inspector, receipt_deadline):
            _fail("google_broker_receipt_failed")
        read_fd, write_fd = pipe()
        record_deadline = min(deadline, time.monotonic() + _COMMAND_TIMEOUT)
        recorder = start(source, ["google-setup-record", "--state", str(settings.source_state),
            "--public-origin", settings.public_origin, "--request-id", request_id,
            "--receipt-fd", str(read_fd)], (read_fd,))
        close(read_fd)
        opened.discard(write_fd)
        with destination._channel(write_fd, writing=True):
            destination._write_frame(write_fd, destination._canonical(receipt), record_deadline)
        if _wait_child(recorder, record_deadline):
            _fail("google_broker_record_failed")
        return _result("prepared", "google_broker_prepared_untested")
    finally:
        for fd in opened:
            os.close(fd)
        _stop_children(children)


def run_google_setup_broker(settings):
    """Run one bounded, opt-in local preparation; return a fixed public report."""
    if not isinstance(settings, GoogleBrokerSettings):
        return _result("failed", "google_broker_configuration_invalid")
    if not settings.enabled:
        return _result("disabled", "google_broker_disabled")
    try:
        _platform_gate()
        if settings.config_path is None or GoogleBrokerSettings.load(settings.config_path) != settings:
            _fail("google_broker_configuration_changed")
        if threading.active_count() != 1:
            _fail("google_broker_single_thread_required")
        approval, _ = _root_json(settings.approval_file, private=True)
        if (approval != _APPROVAL or type(approval.get("schema_version")) is not int
                or approval.get("google_setup_broker_approved") is not True):
            _fail("google_broker_approval_required")
        with _root_file(settings.interpreter, executable=True):
            pass
        _installed_package_gate()
        source = _service_identity(settings.dashboard_user)
        backup = _service_identity(settings.backup_user)
        if source.pw_uid == backup.pw_uid or source.pw_gid == backup.pw_gid:
            _fail("google_broker_identity_unsafe")
        with _state_directory(settings.source_state, source.pw_uid) as source_fd, \
                _state_directory(settings.backup_state, backup.pw_uid) as backup_fd:
            with _broker_lock(settings.lock_file):
                return _orchestrate(settings, source, backup, source_fd, backup_fd)
    except GoogleBrokerError as error:
        return _result("failed", error.code)
    except (destination.GoogleDestinationError,) + _FAILURES:
        return _result("failed", "google_broker_unavailable")


def _validate_config(raw):
    """Accept only the exact direct-drive configuration produced by dev9."""
    text = raw.decode("ascii")
    lines = text.splitlines()
    if (len(lines) != 6 or "\r" in text or not text.endswith("\n")
            or lines[:3] != ["[rcsp_google]", "type = drive", "scope = drive.file"]
            or not lines[3].startswith("client_id = ")
            or not lines[4].startswith("client_secret = ")
            or not lines[5].startswith("token = ")):
        _fail("google_preparation_receipt_invalid")
    client_id, secret = lines[3][12:], lines[4][16:]
    if not destination._client_value(client_id) or not destination._client_value(secret):
        _fail("google_preparation_receipt_invalid")
    token = json.loads(lines[5][8:], object_pairs_hook=_unique_object)
    if (not isinstance(token, dict) or set(token) != {"access_token", "refresh_token", "token_type", "expiry"}
            or token["token_type"] != "Bearer" or not destination._token(token["access_token"], 32768)
            or not destination._token(token["refresh_token"], 16384)
            or not isinstance(token["expiry"], str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", token["expiry"]) is None):
        _fail("google_preparation_receipt_invalid")
    expiry = datetime.fromisoformat(token["expiry"][:-1] + "+00:00")
    if expiry.tzinfo != timezone.utc:
        _fail("google_preparation_receipt_invalid")
    canonical = ("[rcsp_google]\ntype = drive\nscope = drive.file\nclient_id = " + client_id
                 + "\nclient_secret = " + secret + "\ntoken = "
                 + destination._canonical(token).decode("ascii") + "\n").encode("ascii")
    if canonical != raw:
        _fail("google_preparation_receipt_invalid")


def export_google_preparation_receipt(worker_request_state, request_id, output_fd, *, now=None):
    """Consume a private output FD; validate files without provider/network I/O.

    The broker compares both the stored authorization binding and actual config
    bytes' digest with fresh source-derived expected values. The source recorder
    then independently rechecks its current owner, grant and expected rendering.
    """
    try:
        destination._unprivileged()
        with destination._channel(output_fd, writing=True) as channel:
            deadline = destination._deadline(_COMMAND_TIMEOUT)
            now = time.time() if now is None else now
            if (not isinstance(request_id, str) or _ID.fullmatch(request_id) is None
                    or Path(worker_request_state).name != request_id
                    or type(now) not in (int, float) or not math.isfinite(now) or now < 0):
                _fail("google_preparation_receipt_invalid")
            with destination._private_directory(worker_request_state) as root:
                fd = os.open(destination.PREPARATION_DIRECTORY,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                try:
                    _directory_info(fd, os.geteuid(), private=True)
                    raw = _read_private_bytes(fd, "preparation.json", os.geteuid(), 8192)
                    manifest = json.loads(raw, object_pairs_hook=_unique_object)
                    expected = destination._projection()
                    if (not isinstance(manifest, dict)
                            or set(manifest) != set(expected) | {"remote_name", "folder_name", "authorization_sha256", "rclone_config_sha256"}
                            or any(manifest[key] != value or type(manifest[key]) is not type(value)
                                   for key, value in expected.items())
                            or manifest["remote_name"] != destination.REMOTE_NAME
                            or any(not isinstance(manifest[key], str) or _HASH.fullmatch(manifest[key]) is None
                                   for key in ("authorization_sha256", "rclone_config_sha256"))):
                        _fail("google_preparation_receipt_invalid")
                    destination._folder_name(manifest["folder_name"])
                    config = _read_private_bytes(fd, "rclone.conf", os.geteuid(), 65536)
                    if not hmac.compare_digest(hashlib.sha256(config).hexdigest(), manifest["rclone_config_sha256"]):
                        _fail("google_preparation_receipt_invalid")
                    _validate_config(config)
                    # Detect replacement during the multi-file validation too.
                    if (_read_private_bytes(fd, "preparation.json", os.geteuid(), 8192) != raw
                            or _read_private_bytes(fd, "rclone.conf", os.geteuid(), 65536) != config):
                        _fail("google_preparation_receipt_invalid")
                    receipt = {"schema_version": 1, "provider": "google_drive", "request_id": request_id,
                               "authorization_sha256": manifest["authorization_sha256"],
                               "rclone_config_sha256": hashlib.sha256(config).hexdigest(),
                               "folder_name": manifest["folder_name"], "configuration_matches": True,
                               "prepared_at": now, "enabled": False, "test_required": True,
                               "account_verified": False, "folder_exists_verified": False,
                               "automatic_deletion": False}
                    destination._write_frame(channel, destination._canonical(receipt), deadline)
                finally:
                    os.close(fd)
        return {"schema_version": 1, "provider": "google_drive", "receipt_exported": True,
                "uploads_enabled": False, "account_verified": False}
    except GoogleBrokerError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_preparation_receipt_invalid")
