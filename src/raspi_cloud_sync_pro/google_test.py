"""Private first-Google-Test worker; no automatic enablement or deletion.

The default call refuses before credential/state I/O. A consumed, inherited Unix
stream from a kernel-authenticated root peer must release this exact worker and
request first. That proves root authority, NOT network confinement: the trusted
coordinator must hold actual per-run BPF attachment/canary evidence before sending
the release. Unsupported or unproved confinement must never release this gate.

Only the ordinary backup UID runs rclone. Source/dashboard files are never read.
Prepared files stay read-only; token refresh changes a separate operational copy.
Returned receipt/config objects are private and must never be printed or exposed
through an HTTP response. Their explicit projections contain fixed safe fields.
"""

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import selectors
import signal
import socket
import stat
import struct
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import google_control as control
from . import google_destination as destination
from . import google_operational as operational
from .config import _unique_object
from .engine import BackupEngine
from .errors import BackupError
from .google_broker import _validate_config as _validate_seed
from .history import History
from .network import Connectivity
from .providers import RcloneProvider
from .staging import ReadyBatch

_LIMIT = 65536
_TOKEN_KEYS = {"access_token", "refresh_token", "token_type", "expiry"}
_OPTIONS = {"type", "scope", "client_id", "client_secret", "root_folder_id", "token"}
_FAILURES = (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError)
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent",
        "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "GODEBUG": "netdns=go"}


class GoogleTestError(BackupError):
    """Fixed codes only, with private exception context suppressed."""


def _fail(code):
    raise GoogleTestError(code) from None


def _clock(now=None):
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        _fail("google_test_time_invalid")
    return now


def _expiry(value):
    if not isinstance(value, str):
        _fail("google_test_config_invalid")
    match = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
                         r"(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})", value)
    if match is None:
        _fail("google_test_config_invalid")
    base, fraction, offset = match.groups()
    parsed = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    if offset == "Z":
        zone = timezone.utc
    else:
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        if hours > 23 or minutes > 59:
            _fail("google_test_config_invalid")
        zone = timezone((1 if offset[0] == "+" else -1) * timedelta(hours=hours, minutes=minutes))
    return parsed.replace(microsecond=int(((fraction or "") + "000000")[:6]), tzinfo=zone).timestamp()


@dataclass(frozen=True, repr=False)
class GoogleTestConfig:
    settings: dict
    token: dict
    expires_at: float
    config_sha256: str

    def __repr__(self):
        return "<GoogleTestConfig private>"

    @property
    def settings_sha256(self):
        return control.google_operational_settings_sha256(self.settings)


def _parse(raw, *, root_required):
    if type(raw) is not bytes or not 0 < len(raw) <= _LIMIT:
        _fail("google_test_config_invalid")
    text = raw.decode("ascii")
    if "\r" in text or "\x00" in text or len(text.splitlines()) > 32:
        _fail("google_test_config_invalid")
    section = False
    options = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            if section or line != "[" + destination.REMOTE_NAME + "]":
                _fail("google_test_config_invalid")
            section = True
            continue
        match = re.fullmatch(r"([a-z_]+)\s*=\s*(.*)", line)
        if not section or match is None or match[1] in options:
            _fail("google_test_config_invalid")
        options[match[1]] = match[2]
    required = _OPTIONS if root_required else _OPTIONS - {"root_folder_id"}
    if (set(options) != required or options.get("type") != "drive"
            or options.get("scope") != "drive.file"
            or not destination._client_value(options.get("client_id"))
            or not destination._client_value(options.get("client_secret"))
            or (root_required and not control._folder(options.get("root_folder_id")))):
        _fail("google_test_config_invalid")
    token = json.loads(options["token"], object_pairs_hook=_unique_object)
    if (not isinstance(token, dict) or not _TOKEN_KEYS <= set(token)
            or not set(token) <= _TOKEN_KEYS | {"expires_in"}
            or token["token_type"] != "Bearer"
            or not destination._token(token["access_token"], 32768)
            or not destination._token(token["refresh_token"], 16384)
            or ("expires_in" in token and (type(token["expires_in"]) is not int
                or not 0 <= token["expires_in"] <= 86400))):
        _fail("google_test_config_invalid")
    settings = {"remote_name": destination.REMOTE_NAME,
                **{key: value for key, value in options.items() if key != "token"}}
    return GoogleTestConfig(settings, token, _expiry(token["expiry"]), hashlib.sha256(raw).hexdigest())


def validate_google_operational_config(raw, expected_settings, *, now=None, require_fresh=False):
    """Privately parse actual mutable bytes; expected settings come from bound seed.

    This is intentionally separate from immutable preparation's exact canonical
    validator. Rclone may rewrite whitespace, RFC3339 precision and token fields.
    No extra remote, credential source, endpoint, scope or other option is allowed.
    """
    try:
        if type(require_fresh) is not bool:
            _fail("google_test_config_invalid")
        expected = control.google_operational_settings_sha256(expected_settings)
        config = _parse(raw, root_required=True)
        if not hmac.compare_digest(config.settings_sha256, expected):
            _fail("google_test_settings_changed")
        if require_fresh and config.expires_at <= _clock(now) + 10:
            _fail("google_test_refresh_unverified")
        return config
    except GoogleTestError:
        raise
    except (BackupError,) + _FAILURES:
        _fail("google_test_config_invalid")


def _root_peer(fd):
    if not stat.S_ISSOCK(os.fstat(fd).st_mode):
        _fail("google_test_gate_invalid")
    duplicate = os.dup(fd)
    try:
        probe = socket.socket(fileno=duplicate)
    except BaseException:
        os.close(duplicate)
        raise
    try:
        if probe.family != socket.AF_UNIX or probe.type != socket.SOCK_STREAM:
            _fail("google_test_gate_invalid")
        probe.getpeername()
        pid, uid, gid = struct.unpack("3i", probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if pid <= 1 or uid != 0 or gid != 0:
            _fail("google_test_gate_invalid")
    finally:
        probe.close()


def _gate(fd, request_id, now):
    if fd is None:
        _fail("google_test_network_disabled")
    try:
        with destination._channel(fd, writing=False) as channel:
            _root_peer(channel)
            value = destination._read_frame(channel, destination._deadline(5))
            current = _clock(now)
            if (not isinstance(value, dict) or set(value) != {
                    "schema_version", "provider", "purpose", "request_id", "worker_pid", "expires_at"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or value["provider"] != "google_drive" or value["purpose"] != "google_test_wifi_egress"
                    or value["request_id"] != request_id or type(value["worker_pid"]) is not int
                    or value["worker_pid"] != os.getpid() or not control.setup._timestamp(value["expires_at"])
                    or not current < value["expires_at"] <= current + 30):
                _fail("google_test_gate_invalid")
    except GoogleTestError:
        raise
    except (BackupError,) + _FAILURES:
        _fail("google_test_gate_invalid")


class _Runner:
    def __init__(self, executable, digest, deadline, config):
        self.executable, self.deadline, self.config = str(executable), deadline, Path(config)
        path = Path(executable)
        if (not path.is_absolute() or ".." in path.parts or not control._hash(digest)):
            _fail("google_test_executable_invalid")
        parent = Path("/")
        for name in path.parent.parts[1:]:
            parent /= name
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                _fail("google_test_executable_invalid")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o755 or not 0 < info.st_size <= 128 * 1024 * 1024):
                _fail("google_test_executable_invalid")
            hashed = hashlib.sha256()
            while True:
                self.check()
                block = os.read(fd, 65536)
                if not block:
                    break
                hashed.update(block)
            if (hashed.hexdigest() != digest or destination._file_identity(os.fstat(fd)) != destination._file_identity(info)
                    or destination._file_identity(path.lstat()) != destination._file_identity(info)):
                _fail("google_test_executable_invalid")
            self.executable_identity = destination._file_identity(info)
        finally:
            os.close(fd)

    def check(self):
        if time.monotonic() >= self.deadline:
            _fail("google_test_timeout")

    def run(self, arguments, *, capture=False):
        self.check()
        if destination._file_identity(Path(self.executable).lstat()) != self.executable_identity:
            _fail("google_test_executable_invalid")
        command = [self.executable, *arguments, "--config", str(self.config), "--retries", "1",
                   "--low-level-retries", "1", "--transfers", "1", "--checkers", "1", "--contimeout", "10s",
                   "--timeout", "30s", "--buffer-size", "1Mi", "--multi-thread-streams", "0", "--log-level", "ERROR"]
        out, err = bytearray(), bytearray()
        complete = False
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=dict(_ENV), close_fds=True, start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                for stream, target in ((process.stdout, out), (process.stderr, err)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, target)
                while selector.get_map():
                    self.check()
                    events = selector.select(max(0, self.deadline - time.monotonic()))
                    for key, _ in events:
                        try:
                            block = os.read(key.fd, 8192)
                        except BlockingIOError:
                            continue
                        if not block:
                            selector.unregister(key.fileobj)
                        else:
                            key.data.extend(block)
                            if len(key.data) > _LIMIT:
                                _fail("google_test_output_invalid")
            self.check()
            process.wait(timeout=max(.001, self.deadline - time.monotonic()))
            if process.returncode or err:
                _fail("google_test_command_failed")
            complete = True
            return bytes(out) if capture else None
        finally:
            if not complete:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()


def _read_config(path):
    with destination._private_directory(path.parent) as root:
        return operational._read(root, path.name, _LIMIT)[0]


def _write_config(path, settings, token):
    text = "[" + destination.REMOTE_NAME + "]\n"
    for name in ("type", "scope", "client_id", "client_secret", "root_folder_id"):
        if name in settings:
            text += name + " = " + settings[name] + "\n"
    raw = (text + "token = " + destination._canonical(token).decode("ascii") + "\n").encode("ascii")
    with destination._private_directory(path.parent) as root:
        partial = ".config-" + os.urandom(16).hex() + ".incomplete"
        destination._write_private(root, partial, raw)
        os.replace(partial, path.name, src_dir_fd=root, dst_dir_fd=root)
        os.fsync(root)


def _folder_matches(raw, name):
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 2:
        _fail("google_test_folder_invalid")
    for entry in value:
        if (not isinstance(entry, dict) or entry.get("name") != name or not control._folder(entry.get("id"))
                or entry.get("mimeType") != "application/vnd.google-apps.folder"
                or entry.get("trashed", False) is not False or "shortcutDetails" in entry
                or not isinstance(entry.get("parents"), list) or len(entry["parents"]) != 1
                or not control._folder(entry["parents"][0])):
            _fail("google_test_folder_invalid")
    return value


def _fixture(root, request_id):
    data = control.google_test_fixture(request_id)
    with destination._private_directory(root) as parent:
        partial = ".test-" + os.urandom(16).hex() + ".incomplete"
        os.mkdir(partial, mode=0o700, dir_fd=parent)
        with operational._child(parent, partial) as working:
            os.mkdir("files", mode=0o700, dir_fd=working)
            with operational._child(working, "files") as files:
                destination._write_private(files, "fixture.txt", data)
            manifest = {"schema_version": 1, "files": [{"relative": "fixture.txt", "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()}]}
            destination._write_private(working, "manifest.json", destination._canonical(manifest))
            os.fsync(working)
        operational._publish(parent, partial, request_id + ".ready")
        os.fsync(parent)
    return ReadyBatch.load(root / (request_id + ".ready"))


class _TestProvider(RcloneProvider):
    def __init__(self, generation, runner, expected, now):
        super().__init__("google_drive_rclone", destination.REMOTE_NAME + ":", generation.config_path,
                         enabled=True, executable=runner.executable)
        self.destination_id = generation.destination_id
        self.runner, self.expected, self.now = runner, expected, now
        self.uploaded = False
        self.refreshed = False

    def _run(self, arguments):
        validate_google_operational_config(_read_config(self.config), self.expected)
        self.runner.run(arguments)
        validate_google_operational_config(_read_config(self.config), self.expected)

    def upload(self, batch, item):
        super().upload(batch, item)
        self.uploaded = True

    def verify(self, batch, item):
        current = validate_google_operational_config(_read_config(self.config), self.expected)
        token = dict(current.token)
        token["expiry"] = datetime.fromtimestamp(_clock(self.now) - 3600, timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z")
        _write_config(self.config, self.expected, token)
        super().verify(batch, item)
        validate_google_operational_config(_read_config(self.config), self.expected,
                                          now=_clock(self.now), require_fresh=True)
        self.refreshed = True


@dataclass(frozen=True, repr=False)
class GoogleTestOutcome:
    receipt: dict
    error_code: str = None

    def __repr__(self):
        return "<GoogleTestOutcome private>"

    def projection(self):
        return {"schema_version": 1, "provider": "google_drive",
                "test_state": "verified" if self.receipt.get("outcome") == "verified" else "failed",
                "error_code": "google_test_failed" if self.error_code is not None else None,
                "enabled": False, "current_worker_state_checked": False,
                "account_verified": False, "automatic_deletion": False}


@contextmanager
def _lock(root):
    fd = os.open("google-test.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            _fail("google_test_storage_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _fail("google_test_busy")
        yield
    finally:
        os.close(fd)


def run_google_test(prepared_state, operational_state, request, *, root_gate_fd=None,
                    executable=None, executable_sha256=None, timeout=120, now=None):
    """One bounded owner Test; a private root release is mandatory before state I/O.

    request is the exact private google-control-export frame, independently
    selected by the coordinator. The result receipt must use a private FD to the
    source recorder; only projection() may be shown publicly. No Enable occurs.
    """
    try:
        request_id = request.get("request_id") if isinstance(request, dict) else None
        _gate(root_gate_fd, request_id, now)
        operational._ordinary_identity()
        start = _clock(now)
        if (not isinstance(request, dict) or set(request) != {"schema_version", "provider", "request_id", "action",
                "requested_at", "expires_at", "preparation", "test", "fixture"}
                or not control.setup._valid_header(request) or not control._id(request_id)
                or request["action"] != "test" or request["test"] is not None
                or not control.setup._timestamp(request["requested_at"])
                or not control.setup._timestamp(request["expires_at"])
                or request["expires_at"] != request["requested_at"] + control.REQUEST_LIFETIME
                or not request["requested_at"] <= start < request["expires_at"]
                or not control._preparation_valid(request["preparation"])
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300
                or executable is None or not control._hash(executable_sha256)):
            _fail("google_test_request_invalid")
        fixture = control.google_test_fixture(request_id)
        if (not isinstance(request["fixture"], dict) or type(request["fixture"].get("size")) is not int
                or request["fixture"] != {"sha256": hashlib.sha256(fixture).hexdigest(), "size": len(fixture)}):
            _fail("google_test_request_invalid")
        prepared = request["preparation"]
        deadline = time.monotonic() + min(timeout, request["expires_at"] - start)
        # Validate the pinned public executable BEFORE credential/generation I/O.
        runner = _Runner(executable, executable_sha256, deadline, Path("/nonexistent"))
        with destination._private_directory(operational_state) as root, _lock(root):
            generation = operational.create_google_operational_generation(
                prepared_state, operational_state, prepared["request_id"], prepared["folder_name"],
                prepared["authorization_sha256"], prepared["rclone_config_sha256"], now=start)
            runner.config = generation.config_path
            seed = _read_config(generation.config_path)
            _validate_seed(seed)
            bootstrap = _parse(seed, root_required=False)
            base = bootstrap.settings
            def bootstrap_run(arguments, *, capture=False):
                if _parse(_read_config(generation.config_path), root_required=False).settings != base:
                    _fail("google_test_settings_changed")
                output = runner.run(arguments, capture=capture)
                if _parse(_read_config(generation.config_path), root_required=False).settings != base:
                    _fail("google_test_settings_changed")
                return output
            # This is the future backup generation's folder, not a disposable
            # test-only root. Keep the approved label and add private uniqueness.
            name = prepared["folder_name"] + "-" + generation.generation_id
            query = "name = '" + name + "' and trashed = false and 'root' in parents"
            matches = _folder_matches(bootstrap_run(["backend", "query", destination.REMOTE_NAME + ":", query], capture=True), name)
            if matches:
                _fail("google_test_folder_conflict")
            bootstrap_run(["mkdir", destination.REMOTE_NAME + ":" + name])
            matches = _folder_matches(bootstrap_run(["backend", "query", destination.REMOTE_NAME + ":", query], capture=True), name)
            if len(matches) != 1:
                _fail("google_test_folder_invalid")
            folder_id = matches[0]["id"]
            # Bootstrap rclone may already have refreshed the operational token.
            current = _parse(_read_config(generation.config_path), root_required=False)
            if current.settings != base:
                _fail("google_test_settings_changed")
            expected = {**base, "root_folder_id": folder_id}
            with destination._private_directory(generation.config_path.parent.parent) as generation_fd:
                destination._write_private(generation_fd, "test-folder.json", destination._canonical({
                    "schema_version": 1, "generation_id": generation.generation_id,
                    "request_id": request_id, "folder_id": folder_id, "folder_name": name}))
                os.fsync(generation_fd)
            _write_config(generation.config_path, expected, current.token)
            validate_google_operational_config(_read_config(generation.config_path), expected)
            batch = _fixture(generation.config_path.parent.parent, request_id)
            provider = _TestProvider(generation, runner, expected, now)
            with History(generation.config_path.parent.parent / "test-history.sqlite3") as history:
                history.enqueue(batch, provider.destination_id, start)
                state = BackupEngine(history, max_attempts=1).run_once(provider, connectivity=Connectivity.ONLINE)
                if state != "verified" or not provider.uploaded or not provider.refreshed:
                    _fail("google_test_verification_failed")
            runner.check()
            finished = _clock(now)
            if finished >= request["expires_at"]:
                _fail("google_test_request_expired")
            operational.inspect_google_operational_generation(prepared_state, operational_state,
                generation.generation_id, prepared["request_id"], prepared["folder_name"],
                prepared["authorization_sha256"], prepared["rclone_config_sha256"])
            config = validate_google_operational_config(_read_config(generation.config_path), expected,
                                                         now=finished, require_fresh=True)
            runner.check()
            finished = _clock(now)
            if finished >= request["expires_at"]:
                _fail("google_test_request_expired")
            receipt = {"schema_version": 1, "provider": "google_drive", "request_id": request_id,
                "action": "test", "preparation": prepared, "test": None, "completed_at": finished,
                "outcome": "verified", "enabled": False, "automatic_deletion": False,
                "result": {"generation_id": generation.generation_id, "folder_id": folder_id,
                    "operational_config_sha256": config.config_sha256,
                    "operational_settings_sha256": config.settings_sha256,
                    "fixture_sha256": hashlib.sha256(fixture).hexdigest(), "fixture_size": len(fixture),
                    "fixture_uploaded": True, "fixture_readback_verified": True, "token_refresh_verified": True}}
            return GoogleTestOutcome(receipt)
    except GoogleTestError:
        raise
    except (BackupError, subprocess.SubprocessError) + _FAILURES:
        _fail("google_test_failed")
