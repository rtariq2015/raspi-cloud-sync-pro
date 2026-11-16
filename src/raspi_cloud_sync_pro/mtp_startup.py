"""Opt-in cold-boot setup for the physically tested MTP experiment.

No data, worker, grant, image or transfer logic lives here. Failed preparation
is deliberately retained, never detached/removed/replayed by an automatic retry.
The Linux adapter is used only by separately approved, root-owned deployment.
"""
from dataclasses import dataclass
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import time

from .errors import BackupError
from .mtp_broker import _read_configuration
from .config import _unique_object


class StartupError(BackupError):
    pass


def require(value, code="mtp_startup_refused"):
    if not value:
        raise StartupError(code)


def require_host_mount_namespace():
    """Host inventory must never be authenticated through a sandbox mount view."""
    require(Path("/proc/self/ns/mnt").readlink() == Path("/proc/1/ns/mnt").readlink(),
            "mtp_startup_host_namespace_required")


@dataclass(frozen=True)
class StartupSettings:
    source_uid: int
    source_gid: int
    max_power_ma: int
    udc: str = "3f980000.usb"

    def __post_init__(self):
        require(type(self.source_uid) is int and self.source_uid > 0
                and type(self.source_gid) is int and self.source_gid > 0
                and type(self.max_power_ma) is int and 2 <= self.max_power_ma <= 500
                and self.udc == "3f980000.usb", "mtp_startup_settings_invalid")


PROFILE = "raspi-cloud-mtp-disposable"
FALLBACK = "raspi-cloud-disposable-msc"
BROKER = "raspi-cloud-mtp-completion.service"
HOST_CHECK = "raspi-cloud-mtp-host-check.service"
RESPONDER = "raspi-cloud-mtp-responder-experiment.service"
CLAIMERS = ("raspi-cloud-mass-storage-appliance.service", "rpi-usb-gadget-ics.service")
ROOT = Path("/var/lib/raspi-cloud-sync-pro")
EXPORT = ROOT / "mtp-experiment/export"
WORK = ROOT / "mtp-experiment/work"
STAGING = ROOT / "mass-storage-staging"
PREFIX = Path("/opt/raspi-cloud-sync-pro/experimental/mtp")
CONFIG = Path("/etc/raspi-cloud-sync-pro/mtp-startup.json")
APPROVAL = CONFIG.with_name("MTP_STARTUP_APPROVED")
COMPLETION_CONFIG = CONFIG.with_name("mtp-experiment.json")
RECEIPT = Path("/run/raspi-cloud-mtp-startup.json")
FFS = Path("/run/raspi-cloud-mtp-ffs")
GADGETS = Path("/sys/kernel/config/usb_gadget")
UDCS = Path("/sys/class/udc")
SOCKET = Path("/run/raspi-cloud-mtp/completion.sock")


class MtpStartup:
    """Small injected-IO lifecycle; unit tests cannot touch USB or the cloud."""
    def __init__(self, settings, io):
        self.settings, self.io = settings, io

    def _unbound(self, expected=None):
        profiles = self.io.profiles()
        require(set(profiles) <= {PROFILE, FALLBACK}
                and all(value == "" for value in profiles.values()), "mtp_startup_usb_owned")
        if expected is not None:
            require(profiles == expected, "mtp_startup_profiles_changed")
        return profiles

    def prepare(self):
        before = self.io.preflight(self.settings)
        profiles = self._unbound()
        require(PROFILE not in profiles, "mtp_startup_partial_setup_requires_review")
        self.io.prepare_profile(self.settings)
        self.io.verify_profile(self.settings)
        self._unbound({**profiles, PROFILE: ""})
        require(self.io.preflight(self.settings) == before, "mtp_startup_identity_changed")
        self.io.seal(self.settings)

    def bind(self):
        before = self.io.preflight(self.settings)
        self.io.verify_receipt(self.settings)
        self.io.verify_profile(self.settings)
        profiles = self._unbound()
        require(PROFILE in profiles, "mtp_startup_profile_missing")
        deadline = self.io.monotonic() + 20
        while True:
            require(self.io.monotonic() < deadline, "mtp_startup_readiness_timeout")
            require(self.io.preflight(self.settings) == before, "mtp_startup_identity_changed")
            self.io.verify_receipt(self.settings)
            self.io.verify_profile(self.settings)
            self._unbound(profiles)
            if self.io.ready(self.settings):
                break
            require(self.io.monotonic() < deadline, "mtp_startup_readiness_timeout")
            self.io.sleep(0.1)
        # Recheck after readiness: no elapsed quiet period authorizes attachment.
        require(self.io.preflight(self.settings) == before, "mtp_startup_identity_changed")
        self.io.verify_receipt(self.settings)
        self.io.verify_profile(self.settings)
        self._unbound(profiles)
        require(self.io.ready(self.settings), "mtp_startup_readiness_lost")
        self.io.bind_udc(self.settings)
        require(self.io.profiles() == {**profiles, PROFILE: self.settings.udc},
                "mtp_startup_binding_changed")


def _metadata(path):
    value = Path(path).lstat()
    return [value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode]


def _protected(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def _digest(path):
    _protected(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
                and not before.st_mode & 0o022 and before.st_nlink == 1
                and before.st_size <= 16 * 1024 * 1024)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        value = digest.hexdigest()
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(signature(before) == signature(os.fstat(fd)) == signature(Path(path).lstat()))
        return value
    finally:
        os.close(fd)


def _startup_configuration():
    # This manifest pins both installed units and the bounded private parent
    # inventory; it can legitimately exceed the completion broker's 8 KiB cap.
    _protected(CONFIG)
    fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= 128 * 1024)
        raw = os.read(fd, 128 * 1024 + 1)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                               s.st_uid, s.st_gid, s.st_mode, s.st_nlink)
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(fd))
                == signature(CONFIG.lstat()), "mtp_startup_configuration_changed")
        return json.loads(raw, object_pairs_hook=_unique_object)
    finally:
        os.close(fd)


def _command(args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=15, check=False,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    require(result.returncode == 0, "mtp_startup_command_failed")
    return result.stdout.strip()


def _mounts(pid="self"):
    result = []
    for line in Path(f"/proc/{pid}/mountinfo").read_text().splitlines():
        fields = line.split()
        separator = fields.index("-")
        # Approved fixed paths have no mountinfo escapes. Refuse instead of
        # interpreting ambiguous escaped mount names as another directory.
        target = fields[4]
        if "\\" in target:
            continue
        result.append({"id": fields[0], "target": Path(target),
                       "options": fields[5].split(","), "fs": fields[separator + 1],
                       "source": fields[separator + 2]})
    return result


def _mount_at(path, mounts):
    matches = [item for item in mounts if item["target"] == path or item["target"] in path.parents]
    require(bool(matches), "mtp_startup_mount_unknown")
    depth = max(len(item["target"].parts) for item in matches)
    matches = [item for item in matches if len(item["target"].parts) == depth]
    require(len(matches) == 1, "mtp_startup_mount_ambiguous")
    return matches[0]


class LinuxStartupIO:
    """Fixed-path Linux adapter. It never stops a service or releases an owner."""
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def __init__(self):
        self.approved = _startup_configuration()
        self.configuration_hash = _digest(CONFIG)
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.prepared = None

    def _unit(self, unit):
        # --all includes empty properties, not service-only properties on a
        # target. Never synthesize a missing service PID to accommodate targets.
        required = {"ActiveState", "SubState", "UnitFileState", "FragmentPath", "DropInPaths"}
        properties = "ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths"
        if unit.endswith(".service"):
            required.add("MainPID")
            properties = "ActiveState,SubState,MainPID,UnitFileState,FragmentPath,DropInPaths"
        else:
            require(unit.endswith(".target"), "mtp_startup_unit_unverified")
        raw = _command(["/usr/bin/systemctl", "show", "--all", "--no-pager", unit,
                        "--property=" + properties])
        result = {}
        for line in raw.splitlines():
            key, separator, value = line.partition("=")
            require(separator and key not in result, "mtp_startup_unit_ambiguous")
            result[key] = value
        require(set(result) == required, "mtp_startup_unit_unverified")
        if "MainPID" in required:
            require(result["MainPID"].isascii() and result["MainPID"].isdecimal(),
                    "mtp_startup_unit_unverified")
        return result

    def preflight(self, settings):
        require(os.geteuid() == 0 and os.uname().machine == "aarch64"
                and Path("/proc/device-tree/model").read_text().startswith("Raspberry Pi Zero 2 W"),
                "mtp_startup_platform_required")
        require_host_mount_namespace()
        value = _startup_configuration()
        require(value == self.approved and _digest(CONFIG) == self.configuration_hash,
                "mtp_startup_configuration_changed")
        require(set(value) == {"schema_version", "enabled", "source_uid", "source_gid",
                              "max_power_ma", "udc", "files", "directories", "parent_entries"}
                and type(value["schema_version"]) is int and value["schema_version"] == 1
                and value["enabled"] is True, "mtp_startup_disabled")
        approval = _read_configuration(APPROVAL)
        require(type(approval) is dict and type(approval.get("schema_version")) is int
                and approval.get("mtp_startup_approved") is True and approval == {
            "schema_version": 1, "mtp_startup_approved": True,
            "configuration_sha256": self.configuration_hash}, "mtp_startup_approval_required")
        account = pwd.getpwnam("raspi-mtp")
        require(settings == StartupSettings(value["source_uid"], value["source_gid"],
                                           value["max_power_ma"], value["udc"])
                and (account.pw_uid, account.pw_gid) == (settings.source_uid, settings.source_gid))
        require(not any(path.name.startswith("g_") for path in Path("/sys/module").iterdir()),
                "mtp_startup_legacy_owner")
        require({p.name for p in UDCS.iterdir()} == {settings.udc}, "mtp_startup_udc_ambiguous")
        device = (UDCS / settings.udc / "device").resolve(strict=True)
        driver = device / "driver"
        require(driver.resolve(strict=True) == Path("/sys/bus/platform/drivers/dwc2")
                and (driver / device.name).resolve(strict=True) == device,
                "mtp_startup_dwc2_unverified")
        mounts = _mounts()
        require(_mount_at(GADGETS, mounts)["fs"] == "configfs", "mtp_startup_configfs_required")
        for unit in CLAIMERS:
            current = self._unit(unit)
            require(current["ActiveState"] == "inactive" and current["SubState"] == "dead"
                    and current["UnitFileState"] in {"disabled", "masked"},
                    "mtp_startup_competing_service")
        files, directories, entries = value["files"], value["directories"], value["parent_entries"]
        require(type(files) is dict and type(directories) is dict and type(entries) is dict)
        mandatory = {str(PREFIX / name) for name in ("umtprd", "umtprd.conf", "complete-object",
                     "serve-completions", "start-mtp")} | {str(COMPLETION_CONFIG)}
        mandatory |= {str(PREFIX / "site/raspi_cloud_sync_pro" / name)
                      for name in ("__init__.py", "config.py", "mtp_ingest.py", "mtp_broker.py",
                                   "mtp_startup.py", "errors.py")}
        require(mandatory <= set(files), "mtp_startup_runtime_unpinned")
        for unit in (BROKER, HOST_CHECK, RESPONDER, "raspi-cloud-mtp-prepare.service", "raspi-cloud-mtp-bind.service",
                     "raspi-cloud-mtp.target", CLAIMERS[0]):
            current = self._unit(unit)
            paths = [current["FragmentPath"]] + current["DropInPaths"].split()
            require(all(path and path in files for path in paths), "mtp_startup_unit_unpinned")
        for name, digest in files.items():
            require(isinstance(digest, str) and len(digest) == 64 and _digest(name) == digest,
                    "mtp_startup_runtime_changed")
        require(set(directories) == {str(p) for p in (ROOT, EXPORT.parent, EXPORT, WORK, STAGING)})
        for name, metadata in directories.items():
            _protected(Path(name))
            require(_metadata(name) == metadata and stat.S_ISDIR(metadata[4]),
                    "mtp_startup_directory_changed")
        require({p.name: _metadata(p) for p in ROOT.iterdir()} == entries,
                "mtp_startup_parent_inventory_changed")
        for path, uid in ((EXPORT, settings.source_uid), (WORK, 0),
                          (STAGING, pwd.getpwnam("raspi-backup").pw_uid)):
            info = path.lstat()
            require(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700)
        completion = _read_configuration(COMPLETION_CONFIG)
        require(completion.get("enabled") is True and completion.get("source") == str(EXPORT)
                and completion.get("work") == str(WORK) and completion.get("staging") == str(STAGING)
                and completion.get("source_uid") == settings.source_uid
                and completion.get("target_uid") == pwd.getpwnam("raspi-backup").pw_uid
                and completion.get("socket_path") == str(SOCKET), "mtp_startup_broker_config_changed")
        # Same host mount alone is insufficient: ready() also inspects the ACTUAL
        # running broker namespace, including all per-host credential masks.
        require(_mount_at(WORK, mounts)["id"] == _mount_at(STAGING, mounts)["id"],
                "mtp_startup_publication_mount_mismatch")
        return self.configuration_hash, self.boot_id

    def profiles(self):
        return {p.name: (p / "UDC").read_text().strip() for p in GADGETS.iterdir()}

    def prepare_profile(self, settings):
        require(not os.path.lexists(RECEIPT) and not os.path.lexists(FFS),
                "mtp_startup_partial_setup_requires_review")
        _command(["/usr/sbin/modprobe", "usb_f_fs"])
        gadget = GADGETS / PROFILE
        gadget.mkdir()
        for name, value in {"idVendor": "0x1d6b", "idProduct": "0x0100", "bcdUSB": "0x0200",
                            "bcdDevice": "0x0100", "bDeviceClass": "0", "bDeviceSubClass": "0",
                            "bDeviceProtocol": "0"}.items():
            (gadget / name).write_text(value + "\n")
        strings = gadget / "strings/0x409"
        strings.mkdir()
        for name, value in {"serialnumber": "RCSP-MTP-DISPOSABLE", "manufacturer": "Raspi Cloud Sync Pro",
                            "product": "CLOUDSTICK"}.items():
            (strings / name).write_text(value + "\n")
        config = gadget / "configs/c.1"
        config.mkdir()
        (config / "strings/0x409").mkdir()
        (config / "strings/0x409/configuration").write_text("CLOUDSTICK MTP\n")
        (config / "bmAttributes").write_text("0x80\n")
        (config / "MaxPower").write_text(str(settings.max_power_ma) + "\n")
        function = gadget / "functions/ffs.mtp"
        function.mkdir()
        (config / "ffs.mtp").symlink_to(function)
        FFS.mkdir(mode=0o700)
        _command(["/usr/bin/mount", "-t", "functionfs", "-o",
                  f"uid={settings.source_uid},gid={settings.source_gid},rmode=0500,fmode=0600", "mtp", str(FFS)])
        self.prepared = self._identity()

    def _identity(self):
        mount = _mount_at(FFS, _mounts())
        require(mount["target"] == FFS and mount["fs"] == "functionfs" and mount["source"] == "mtp",
                "mtp_startup_ffs_mount_unverified")
        return {"gadget": _metadata(GADGETS / PROFILE), "ffs": _metadata(FFS), "mount_id": mount["id"]}

    def verify_profile(self, settings):
        gadget = GADGETS / PROFILE
        values = {"idVendor": 0x1d6b, "idProduct": 0x0100, "bcdUSB": 0x0200, "bcdDevice": 0x0100,
                  "bDeviceClass": 0, "bDeviceSubClass": 0, "bDeviceProtocol": 0}
        for name, value in values.items():
            require(int((gadget / name).read_text().strip(), 0) == value, "mtp_startup_profile_changed")
        require({p.name for p in (gadget / "functions").iterdir()} == {"ffs.mtp"}
                and {p.name for p in (gadget / "configs").iterdir()} == {"c.1"})
        config = gadget / "configs/c.1"
        links = [p for p in config.iterdir() if p.is_symlink()]
        require(links == [config / "ffs.mtp"] and links[0].resolve(strict=True) == gadget / "functions/ffs.mtp")
        require(int((config / "MaxPower").read_text()) == settings.max_power_ma
                and int((config / "bmAttributes").read_text(), 0) == 0x80)
        for name, expected in {"serialnumber": "RCSP-MTP-DISPOSABLE", "manufacturer": "Raspi Cloud Sync Pro",
                               "product": "CLOUDSTICK"}.items():
            require((gadget / "strings/0x409" / name).read_text().strip() == expected)
        current = self._identity()
        require(current["ffs"][2:4] == [settings.source_uid, settings.source_gid]
                and stat.S_IMODE(current["ffs"][4]) == 0o500)
        if self.prepared is not None:
            require(current == self.prepared, "mtp_startup_runtime_identity_changed")

    def seal(self, settings):
        value = {"schema_version": 1, "boot_id": self.boot_id,
                 "configuration_sha256": self.configuration_hash, "runtime": self._identity()}
        fd = os.open(RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())

    def verify_receipt(self, settings):
        value = _read_configuration(RECEIPT)
        require(value == {"schema_version": 1, "boot_id": self.boot_id,
                          "configuration_sha256": self.configuration_hash, "runtime": self._identity()},
                "mtp_startup_receipt_changed")
        self.prepared = value["runtime"]

    def ready(self, settings):
        identities = []
        for unit, uid, executable in ((BROKER, 0, PREFIX / "serve-completions"),
                                       (RESPONDER, settings.source_uid, PREFIX / "umtprd")):
            current = self._unit(unit)
            require(current["ActiveState"] == "active" and current["SubState"] == "running",
                    "mtp_startup_component_not_running")
            pid = int(current["MainPID"])
            require(pid > 1)
            status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                          if ":" in line)
            require(all(int(part) == uid for part in status["Uid"].split()) and status["NoNewPrivs"].strip() == "1")
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            require(os.fsencode(executable) in args, "mtp_startup_process_unverified")
            if unit == RESPONDER:
                require(int(status["CapEff"].strip(), 16) == 0
                        and all(int(part) == settings.source_gid for part in status["Gid"].split()))
                require(_metadata(Path(f"/proc/{pid}/root") / FFS.relative_to("/")) == _metadata(FFS),
                        "mtp_startup_responder_ffs_changed")
            else:
                mounts = _mounts(pid)
                publication = _mount_at(WORK, mounts)
                require(publication["id"] == _mount_at(STAGING, mounts)["id"]
                        and "rw" in publication["options"], "mtp_startup_broker_mount_mismatch")
                require("ro" in _mount_at(EXPORT, mounts)["options"], "mtp_startup_export_not_readonly")
                for path in (WORK, STAGING, EXPORT):
                    require(_metadata(Path(f"/proc/{pid}/root") / path.relative_to("/")) == _metadata(path),
                            "mtp_startup_broker_directory_changed")
                for name in set(self.approved["parent_entries"]) - {"mtp-experiment", "mass-storage-staging"}:
                    target = ROOT / name
                    mask = _mount_at(target, mounts)
                    require(mask["target"] == target and mask["fs"] == "tmpfs"
                            and {"ro", "nosuid", "nodev", "noexec"} <= set(mask["options"]),
                            "mtp_startup_broker_mask_missing")
            identities.append((pid, Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]))
        if not SOCKET.exists():
            return False
        info = SOCKET.lstat()
        parent = SOCKET.parent.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0 and info.st_gid == settings.source_gid
                and stat.S_IMODE(info.st_mode) == 0o660 and parent.st_uid == 0
                and parent.st_gid == settings.source_gid and stat.S_IMODE(parent.st_mode) == 0o750)
        if (GADGETS / PROFILE / "functions/ffs.mtp/ready").read_text().strip() != "1":
            return False
        for name in ("ep0", "ep1", "ep2", "ep3"):
            info = (FFS / name).lstat()
            require(info.st_uid == settings.source_uid and info.st_gid == settings.source_gid
                    and stat.S_IMODE(info.st_mode) == 0o600, "mtp_startup_endpoint_unsafe")
        for pid, started in identities:
            require(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19] == started)
        return True

    def bind_udc(self, settings):
        (GADGETS / PROFILE / "UDC").write_text(settings.udc + "\n")


# Only reviewed fixed codes may reach the journal; never exception text or paths.
_PUBLIC_FAILURE_CODES = frozenset({
    'mtp_startup_approval_required',
    'mtp_startup_binding_changed',
    'mtp_startup_broker_config_changed',
    'mtp_startup_broker_directory_changed',
    'mtp_startup_broker_mask_missing',
    'mtp_startup_broker_mount_mismatch',
    'mtp_startup_command_failed',
    'mtp_startup_competing_service',
    'mtp_startup_component_not_running',
    'mtp_startup_configfs_required',
    'mtp_startup_configuration_changed',
    'mtp_startup_directory_changed',
    'mtp_startup_disabled',
    'mtp_startup_dwc2_unverified',
    'mtp_startup_endpoint_unsafe',
    'mtp_startup_export_not_readonly',
    'mtp_startup_ffs_mount_unverified',
    'mtp_startup_host_namespace_required',
    'mtp_startup_identity_changed',
    'mtp_startup_legacy_owner',
    'mtp_startup_lock_busy',
    'mtp_startup_lock_unverified',
    'mtp_startup_mount_ambiguous',
    'mtp_startup_mount_unknown',
    'mtp_startup_parent_inventory_changed',
    'mtp_startup_partial_setup_requires_review',
    'mtp_startup_platform_required',
    'mtp_startup_process_unverified',
    'mtp_startup_profile_changed',
    'mtp_startup_profile_missing',
    'mtp_startup_profiles_changed',
    'mtp_startup_publication_mount_mismatch',
    'mtp_startup_readiness_lost',
    'mtp_startup_readiness_timeout',
    'mtp_startup_receipt_changed',
    'mtp_startup_refused',
    'mtp_startup_responder_ffs_changed',
    'mtp_startup_root_required',
    'mtp_startup_runtime_changed',
    'mtp_startup_runtime_identity_changed',
    'mtp_startup_runtime_unpinned',
    'mtp_startup_settings_invalid',
    'mtp_startup_udc_ambiguous',
    'mtp_startup_unit_ambiguous',
    'mtp_startup_unit_unpinned',
    'mtp_startup_unit_unverified',
    'mtp_startup_usb_owned',
})


def _failure_code(error):
    if isinstance(error, StartupError):
        if isinstance(error.code, str) and error.code in _PUBLIC_FAILURE_CODES:
            return error.code
        return "mtp_startup_refused"
    if isinstance(error, BackupError):
        return "mtp_startup_dependency_refused"
    if isinstance(error, OSError):
        return "mtp_startup_os_error"
    if isinstance(error, subprocess.SubprocessError):
        return "mtp_startup_command_failed"
    return "mtp_startup_input_invalid"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Approved MTP cold-boot setup; never releases another owner")
    parser.add_argument("action", choices=("check", "prepare", "bind"))
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0, "mtp_startup_root_required")
        _protected(Path("/run/lock"))
        fd = os.open("/run/lock/raspi-cloud-mtp-startup.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                    "mtp_startup_lock_unverified")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StartupError("mtp_startup_lock_busy") from error
            io = LinuxStartupIO()
            v = io.approved
            settings = StartupSettings(v["source_uid"], v["source_gid"], v["max_power_ma"], v["udc"])
            if args.action == "check":
                io.preflight(settings)
            else:
                getattr(MtpStartup(settings, io), args.action)()
        print("MTP startup check passed")
        return 0
    except (BackupError, OSError, ValueError, TypeError, KeyError, UnicodeError, subprocess.SubprocessError) as error:
        print("MTP startup refused: " + _failure_code(error))
        return 1
