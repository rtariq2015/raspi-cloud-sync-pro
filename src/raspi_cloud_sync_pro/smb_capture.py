"""Opt-in, capture-only SMB runtime. Published local batches are the queue.

No provider, cloud configuration, gadget mutation, service or network setup is
performed. An independently reviewed Samba server must map this named share to
the approved Pi-owned directory and enforce its writer/read-only roles. The
specific approval is rechecked before every SMB open; the global hardware gate
remains off. Source metadata is only a candidate hint, never completion proof.
"""

import hashlib
import ipaddress
import json
import os
import platform
import pwd
import re
import selectors
import signal
import sqlite3
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import google_broker as private_files
from . import google_destination as private
from .device_modes import DeviceMode, DevicePolicy
from .errors import BackupError
from .ethernet import bound_configfs_gadgets
from .linux_gadget import LEGACY_GADGET_MODULES, LiveLinuxIO
from .smb_ingest import LocalSambaReader, stage_smb_version
from .staging import ReadyBatch, open_regular, relative_path

_FIELDS = {"schema_version", "enabled", "approval_file", "device_policy", "share_name", "share_root",
           "staging_directory", "state_directory", "credentials_file", "usb_address", "usb_profile_uuid",
           "interval", "max_file_bytes", "capture_timeout"}
_PATHS = ("approval_file", "device_policy", "share_root", "staging_directory", "state_directory", "credentials_file")
_APPROVAL = {"schema_version": 1, "smb_capture_approved": True}
_ID = re.compile(r"[0-9a-f]{32}\.ready\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}
_MAX_ENTRIES = 4096
_MAX_CAPTURES = 32
_PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class SmbCaptureError(BackupError):
    """Fixed codes only, without paths, credentials or raw SMB errors."""


def _fail(code="smb_capture_unavailable", *, retryable=True):
    raise SmbCaptureError(code, retryable=retryable) from None


def _path(value):
    if not isinstance(value, str):
        _fail("smb_capture_config_invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or "\0" in value:
        _fail("smb_capture_config_invalid")
    return path


@dataclass(frozen=True, repr=False)
class SmbCaptureSettings:
    enabled: bool = False
    approval_file: Path = None
    device_policy: Path = None
    share_name: str = None
    share_root: Path = None
    staging_directory: Path = None
    state_directory: Path = None
    credentials_file: Path = None
    usb_address: str = None
    usb_profile_uuid: str = None
    interval: int = 5
    max_file_bytes: int = 100 * 1024 * 1024
    capture_timeout: int = 300
    _config: Path = field(default=None, repr=False)
    _identity: tuple = field(default=None, repr=False)

    def __repr__(self):
        return "<SmbCaptureSettings protected>"

    @classmethod
    def load(cls, root_config_path):
        try:
            path = Path(root_config_path)
            value, identity = private_files._root_json(path)
            if (not isinstance(value, dict) or set(value) - _FIELDS
                    or type(value.get("schema_version")) is not int or value["schema_version"] != 1
                    or type(value.get("enabled", False)) is not bool):
                _fail("smb_capture_config_invalid")
            if not value.get("enabled", False):
                return cls(_config=path, _identity=identity)
            if set(value) != _FIELDS:
                _fail("smb_capture_config_invalid")
            paths = {name: _path(value[name]) for name in _PATHS}
            roots = [paths[name] for name in ("share_root", "staging_directory", "state_directory")]
            if any(a == b or a in b.parents or b in a.parents for index, a in enumerate(roots) for b in roots[index + 1:]):
                _fail("smb_capture_config_invalid")
            address = ipaddress.IPv4Interface(value["usb_address"])
            if (str(address) != value["usb_address"] or not any(address.ip in network for network in _PRIVATE_NETWORKS)
                    or address.ip.is_loopback
                    or address.ip.is_link_local or address.ip.is_unspecified or address.ip.is_multicast
                    or address.ip == address.network.network_address or address.ip == address.network.broadcast_address
                    or not 24 <= address.network.prefixlen <= 30
                    or not isinstance(value["usb_profile_uuid"], str) or not _UUID.fullmatch(value["usb_profile_uuid"])
                    or not isinstance(value["share_name"], str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", value["share_name"])
                    or paths["credentials_file"].parent != paths["state_directory"]):
                _fail("smb_capture_config_invalid")
            for name, low, high in (("interval", 5, 3600), ("max_file_bytes", 1, 16 * 1024 ** 3),
                                    ("capture_timeout", 1, 3600)):
                if type(value[name]) is not int or not low <= value[name] <= high:
                    _fail("smb_capture_config_invalid")
            return cls(enabled=True, **paths, **{name: value[name] for name in
                ("share_name", "usb_address", "usb_profile_uuid", "interval", "max_file_bytes", "capture_timeout")},
                _config=path, _identity=identity)
        except SmbCaptureError:
            raise
        except Exception:
            _fail("smb_capture_config_invalid")


def _check(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("smb_capture_timeout")
    return remaining


def _command(arguments, deadline):
    with private_files._root_file(Path(arguments[0]).resolve(strict=True), executable=True):
        pass
    end = min(deadline, time.monotonic() + 3)
    _check(end)
    raw = bytearray()
    with selectors.DefaultSelector() as selector:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=_ENV, cwd="/", close_fds=True, start_new_session=True)
        try:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if not selector.select(_check(end)):
                    _fail("smb_capture_timeout")
                block = os.read(process.stdout.fileno(), 8193 - len(raw))
                if not block:
                    selector.unregister(process.stdout)
                else:
                    raw.extend(block)
                    if len(raw) > 8192:
                        _fail()
            result = process.wait(timeout=_check(end))
            return result, raw.decode("ascii").strip()
        finally:
            # A trusted command may have exited while a descendant retains stdout.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.stdout.close()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _fail("smb_capture_probe_cleanup_unconfirmed")


def _ordinary():
    account = pwd.getpwnam("raspi-backup")
    if (account.pw_uid <= 0 or os.getresuid() != (account.pw_uid,) * 3 or os.getegid() <= 0
            or os.getresgid() != (os.getegid(),) * 3 or platform.system() != "Linux"
            or platform.machine() not in ("aarch64", "armv7l")):
        _fail("smb_capture_identity_invalid")
    with open("/proc/device-tree/model", "rb") as stream:
        if not stream.read(256).startswith(b"Raspberry Pi"):
            _fail("smb_capture_identity_invalid")
    with open("/proc/self/status", encoding="ascii") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        _fail("smb_capture_identity_invalid")
    status = dict(line.split(":", 1) for line in raw.splitlines() if ":" in line)
    if (status.get("Umask", "").strip() != "0077" or status.get("NoNewPrivs", "").strip() != "1"
            or any(int(status.get(name, "1").strip(), 16) != 0 for name in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"))):
        _fail("smb_capture_private_process_required")


def _guard(settings, deadline):
    _check(deadline)
    if settings._config is None or SmbCaptureSettings.load(settings._config) != settings:
        _fail("smb_capture_config_changed")
    marker, _ = private_files._root_json(settings.approval_file)
    if (marker != _APPROVAL or type(marker.get("schema_version")) is not int
            or marker.get("smb_capture_approved") is not True):
        _fail("smb_capture_approval_required")
    _ordinary()
    policy_data, _ = private_files._root_json(settings.device_policy)
    if (not isinstance(policy_data, dict) or set(policy_data) !=
            {"schema_version", "mode", "approved_uplinks", "allow_metered"}
            or type(policy_data["schema_version"]) is not int or policy_data["schema_version"] != 1):
        _fail("smb_capture_policy_invalid")
    policy = DevicePolicy(DeviceMode(policy_data["mode"]), tuple(policy_data["approved_uplinks"]), policy_data["allow_metered"])
    if policy.mode != DeviceMode.ETHERNET_SAMBA:
        _fail("smb_capture_policy_invalid")
    if (not Path("/sys/module/g_ether").exists()
            or any(Path("/sys/module", name).exists() for name in LEGACY_GADGET_MODULES if name != "g_ether")
            or bound_configfs_gadgets(LiveLinuxIO())):
        _fail("smb_capture_usb_unverified")
    code, state = _command(["/usr/bin/systemctl", "is-active", "rpi-usb-gadget-ics.service"], deadline)
    if code not in (3, 4) or state not in ("inactive", "unknown"):
        _fail("smb_capture_usb_unverified")
    code, state = _command(["/usr/bin/systemctl", "is-enabled", "rpi-usb-gadget-ics.service"], deadline)
    if code not in (1, 4) or state not in ("disabled", "masked", "not-found"):
        _fail("smb_capture_usb_unverified")

    def nm(field, target="usb0"):
        code, raw = _command(["/usr/bin/nmcli", "--escape", "no", "-g", field, "device", "show", target], deadline)
        if code:
            _fail("smb_capture_usb_unverified")
        return raw

    if (nm("GENERAL.CON-UUID") != settings.usb_profile_uuid or nm("IP4.ADDRESS") != settings.usb_address
            or nm("IP4.GATEWAY") or nm("IP4.DNS")):
        _fail("smb_capture_usb_unverified")
    code, mode = _command(["/usr/bin/nmcli", "--escape", "no", "-g", "ipv4.method,ipv4.never-default,connection.interface-name",
                           "connection", "show", settings.usb_profile_uuid], deadline)
    if code or mode.splitlines() != ["manual", "yes", "usb0"]:
        _fail("smb_capture_usb_unverified")
    code, wifi = _command(["/usr/sbin/ip", "-j", "-4", "address", "show", "dev", "wlan0"], deadline)
    if code:
        _fail("smb_capture_usb_unverified")
    usb = ipaddress.IPv4Interface(settings.usb_address).network
    for interface in json.loads(wifi):
        for address in interface.get("addr_info", []):
            if address.get("family") == "inet" and usb.overlaps(ipaddress.IPv4Interface(
                    str(address["local"]) + "/" + str(address["prefixlen"])).network):
                _fail("smb_capture_subnet_overlap")
    with private_files._root_directory(settings.share_root.parent):
        info = settings.share_root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid in (0, os.geteuid()) or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) != 0o750):
        _fail("smb_capture_share_ownership_invalid")
    code, raw = _command(["/usr/bin/findmnt", "-J", "-T", str(settings.share_root), "-o", "FSTYPE,SOURCE,OPTIONS"], deadline)
    mounts = json.loads(raw).get("filesystems", []) if not code else []
    if (len(mounts) != 1 or mounts[0].get("fstype") != "ext4" or not isinstance(mounts[0].get("source"), str)
            or mounts[0]["source"].startswith("/dev/loop") or "rw" not in mounts[0].get("options", "").split(",")):
        _fail("smb_capture_filesystem_unverified")


def _credentials(settings):
    with private._private_directory(settings.state_directory) as root:
        value, _ = private._read_private(root, settings.credentials_file.name, 4096)
        if os.stat(settings.credentials_file.name, dir_fd=root, follow_symlinks=False).st_nlink != 1:
            _fail("smb_capture_credentials_invalid")
    if (not isinstance(value, dict) or set(value) != {"username", "password"} or value["username"] != "raspi-backup"
            or not isinstance(value["password"], str) or not 1 <= len(value["password"]) <= 2048
            or "\0" in value["password"]):
        _fail("smb_capture_credentials_invalid")
    return value


def _inventory(root, deadline):
    files = []; visited = 0
    def walk(directory, prefix, depth):
        nonlocal visited
        _check(deadline)
        if depth > 8:
            _fail("smb_capture_inventory_limit")
        with os.scandir(directory) as entries:
            for entry in entries:
                _check(deadline)
                visited += 1
                if visited > _MAX_ENTRIES:
                    _fail("smb_capture_inventory_limit")
                info = entry.stat(follow_symlinks=False)
                relative = relative_path(prefix + entry.name)
                if ":" in relative or len(relative.encode("utf-8")) > 4096:
                    _fail("smb_capture_unsupported_entry")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        current = os.fstat(child)
                        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                            _fail("smb_capture_inventory_changed")
                        walk(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    files.append((relative, private._canonical(list(private._file_identity(info))).decode("ascii")))
                else:
                    _fail("smb_capture_unsupported_entry")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(descriptor, "", 0)
    finally:
        os.close(descriptor)
    return sorted(files)


class _BoundedReader:
    def __init__(self, reader, maximum, deadline):
        self.reader, self.maximum, self.deadline = reader, maximum, deadline

    @contextmanager
    def open_version(self, relative):
        with self.reader.open_version(relative) as stream:
            outer = self
            class Bounded:
                total = 0
                def seek(self, offset, whence=0):
                    _check(outer.deadline); value = stream.seek(offset, whence); _check(outer.deadline)
                    if type(value) is not int or not 0 <= value <= outer.maximum:
                        _fail("smb_capture_file_limit", retryable=False)
                    return value
                def read(self, count):
                    _check(outer.deadline); data = stream.read(count); _check(outer.deadline)
                    if not isinstance(data, bytes):
                        _fail("smb_capture_stream_invalid")
                    self.total += len(data)
                    if self.total > outer.maximum:
                        _fail("smb_capture_file_limit", retryable=False)
                    return data
            yield Bounded()


@contextmanager
def _index(state):
    with private._private_directory(state) as root:
        lock = os.open("smb-capture.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root)
        database = None
        try:
            import fcntl
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                _fail("smb_capture_state_invalid")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open("capture.sqlite3", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                    _fail("smb_capture_state_invalid")
            finally:
                os.close(fd)
            database = sqlite3.connect(str(state / "capture.sqlite3"))
            database.execute("PRAGMA synchronous=FULL")
            database.execute("CREATE TABLE IF NOT EXISTS captured(relative TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, batch TEXT NOT NULL, manifest_sha256 TEXT NOT NULL)")
            database.execute("CREATE TABLE IF NOT EXISTS scan_cursor(singleton INTEGER PRIMARY KEY CHECK(singleton=1), relative TEXT NOT NULL)")
            database.commit(); os.fsync(root)
            yield database, root
        finally:
            if database is not None:
                database.close()
            os.close(lock)


def _manifest(root):
    with open_regular(root, "manifest.json") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        _fail("smb_capture_state_invalid")
    return hashlib.sha256(raw).hexdigest()


def _report(state, staged=0, deferred=0, rejected=0):
    return {"capture_state": state, "staged_files": staged, "deferred_files": deferred, "rejected_files": rejected,
            "uploads_enabled": False, "cloud_accounts_verified": False, "automatic_deletion": False}


def _tick(settings, verified):
    if len(verified) >= _MAX_ENTRIES:
        verified.clear()  # Bound the optimization; clearing forces independent revalidation.
    deadline = time.monotonic() + settings.capture_timeout
    _guard(settings, deadline)
    with private._private_directory(settings.staging_directory), _index(settings.state_directory) as (index, root):
        reader = LocalSambaReader(settings.share_name, **_credentials(settings), approval_guard=lambda: _guard(settings, deadline))
        bounded = _BoundedReader(reader, settings.max_file_bytes, deadline)
        staged = deferred = rejected = attempts = 0
        candidates = _inventory(settings.share_root, deadline)
        cursor = index.execute("SELECT relative FROM scan_cursor WHERE singleton=1").fetchone()
        if cursor:
            candidates = ([item for item in candidates if item[0] > cursor[0]]
                          + [item for item in candidates if item[0] <= cursor[0]])
        for relative, fingerprint in candidates:
            previous = index.execute("SELECT fingerprint,batch,manifest_sha256 FROM captured WHERE relative=?", (relative,)).fetchone()
            if previous and previous[0] == fingerprint and _ID.fullmatch(previous[1]):
                try:
                    path = settings.staging_directory / previous[1]
                    if previous[1] not in verified:
                        if _manifest(path) != previous[2]:
                            _fail("smb_capture_state_invalid")
                        ReadyBatch.load(path); verified.add(previous[1])
                    continue
                except (BackupError, OSError):
                    pass  # Retain damaged snapshots; recapture a stable source version.
            if attempts >= _MAX_CAPTURES:
                break
            attempts += 1
            # Persist progress before opening: busy, rejected or interrupted files
            # must not indefinitely hide later completed files, including on restart.
            with index:
                index.execute("INSERT OR REPLACE INTO scan_cursor VALUES (1,?)", (relative,))
            os.fsync(root)
            try:
                batch = stage_smb_version(bounded, relative, settings.staging_directory)
                digest = _manifest(batch.root)
                with index:
                    index.execute("INSERT OR REPLACE INTO captured VALUES (?,?,?,?)", (relative, fingerprint, batch.root.name, digest))
                os.fsync(root); verified.add(batch.root.name); staged += 1
            except BackupError as error:
                if error.retryable:
                    deferred += 1
                else:
                    rejected += 1
        return _report("captured" if staged else "deferred" if deferred else "idle", staged, deferred, rejected)


def run_smb_capture(settings, *, once=False):
    """Capture offline into a private local queue, with no provider construction."""
    if not isinstance(settings, SmbCaptureSettings):
        return _report("unavailable")
    if not settings.enabled:
        return _report("disabled")
    verified = set()
    while True:
        try:
            report = _tick(settings, verified)
        except Exception:
            report = _report("blocked")
        if once:
            return report
        time.sleep(settings.interval)
