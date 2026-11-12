"""Linux configfs mass-storage backend. Production mutations are code-gated OFF.

Tests inject an in-memory I/O implementation; no environment variable, config
setting, service marker, or CLI argument enables LiveLinuxIO mutations.
"""

import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import BackupError
from .gadget import GadgetObservation

HARDWARE_OPERATIONS_ENABLED = False  # Separate reviewed disposable-data approval is required.
LEGACY_GADGET_MODULES = ("g_ether", "g_mass_storage", "g_multi", "g_acm_ms", "g_serial", "g_cdc",
                         "g_webcam", "g_zero", "g_printer", "g_hid", "g_audio", "g_file_storage")


def require_hardware_test_approval():
    if HARDWARE_OPERATIONS_ENABLED is not True:
        raise BackupError("hardware_operations_disabled")


@dataclass(frozen=True)
class GadgetSettings:
    image: Path
    mountpoint: Path
    state_directory: Path
    staging_directory: Path
    udc: str
    name: str = "raspi-cloud-sync-pro"
    serial: str = "REPLACE_WITH_UNIQUE_LAB_SERIAL"
    vendor_id: str = "0x1d6b"
    product_id: str = "0x0104"

    def __post_init__(self):
        paths = (self.image, self.mountpoint, self.state_directory, self.staging_directory)
        if any(not isinstance(p, Path) or not p.is_absolute() or ".." in p.parts
               or any(c.isspace() or ord(c) < 32 for c in str(p)) for p in paths):
            raise BackupError("invalid_gadget_paths")
        for i, path in enumerate(paths):
            if any(path == other or path in other.parents or other in path.parents for other in paths[i + 1:]):
                raise BackupError("overlapping_gadget_paths")
        for token in (self.udc, self.name, self.serial):
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", token):
                raise BackupError("invalid_gadget_identifier")
        for value in (self.vendor_id, self.product_id):
            if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{4}", value) \
                    or int(value, 16) in (0, 65535):
                raise BackupError("invalid_usb_id")

    @classmethod
    def load(cls, path: Path):
        try:
            data = json.loads(Path(path).read_text())
            expected = {"image", "mountpoint", "state_directory", "staging_directory", "udc",
                        "name", "serial", "vendor_id", "product_id"}
            if not isinstance(data, dict) or set(data) != expected:
                raise ValueError()
            for key in ("image", "mountpoint", "state_directory", "staging_directory"):
                data[key] = Path(data[key])
            return cls(**data)
        except (OSError, TypeError, ValueError) as exc:
            raise BackupError("invalid_gadget_config") from exc


class LiveLinuxIO:
    """Only this class can execute privileged commands or write device controls."""

    def require_mutations(self):
        require_hardware_test_approval()
        if os.geteuid() != 0:
            raise BackupError("controller_requires_root")
        if os.stat("/proc/self/ns/mnt").st_ino != os.stat("/proc/1/ns/mnt").st_ino:
            raise BackupError("controller_requires_host_mount_namespace")

    def read(self, path):
        return Path(path).read_text().strip()

    def power(self):
        result = self._command(["vcgencmd", "get_throttled"], {"vcgencmd"})
        return result.stdout.strip() if result.returncode == 0 else None

    def protected_directory(self, path):
        for parent in (Path(path), *Path(path).parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise BackupError("controller_directory_not_protected")

    def exists(self, path):
        return Path(path).exists()

    def children(self, path):
        return sorted(Path(path).iterdir())

    def link_matches(self, link, target):
        return Path(link).resolve(strict=True) == Path(target).resolve(strict=True)

    def regular_image(self, path):
        info = Path(path).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size < 1024 * 1024:
            raise BackupError("unsafe_image_file")
        # All ancestors must be root-owned and not writable by group/others.
        for parent in (Path(path), *Path(path).parents):
            info_parent = parent.lstat()
            if stat.S_ISLNK(info_parent.st_mode) or info_parent.st_uid != 0 or info_parent.st_mode & 0o022:
                raise BackupError("image_path_not_protected")
        return (info.st_dev, info.st_ino, info.st_size)

    def _command(self, arguments, allowed):
        if arguments[0] not in allowed:
            raise BackupError("unsupported_linux_command")
        try:
            return subprocess.run(arguments, capture_output=True, text=True, timeout=60, check=False,
                                  env={**os.environ, "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackupError("linux_command_failed") from exc

    def query(self, arguments):
        if arguments[:3] != ["losetup", "--list", "--json"] and arguments[:3] != ["lsblk", "--json", "--paths"]:
            raise BackupError("unsupported_linux_query")
        return self._command(arguments, {"losetup", "lsblk"})

    def command(self, arguments):
        self.require_mutations()
        return self._command(arguments, {"losetup", "fsck.exfat", "mount", "umount"})

    def provision_command(self, arguments):
        self.require_mutations()
        if arguments not in (["rpi-usb-gadget", "on"],
                             ["systemctl", "disable", "--now", "rpi-usb-gadget-ics.service"]):
            raise BackupError("unsupported_provision_command")
        return self._command(arguments, {"rpi-usb-gadget", "systemctl"})

    def write(self, path, value):
        self.require_mutations()
        Path(path).write_text(value + "\n")

    def mkdir(self, path):
        self.require_mutations()
        Path(path).mkdir()

    def symlink(self, source, destination):
        self.require_mutations()
        Path(destination).symlink_to(source)


class LinuxGadgetBackend:
    def __init__(self, settings: GadgetSettings, io=None):
        self.settings = settings
        self.io = LiveLinuxIO() if io is None else io
        self.image = settings.image
        self.mountpoint = settings.mountpoint
        self.root = Path("/sys/kernel/config/usb_gadget") / settings.name
        self.function = self.root / "functions/mass_storage.0"
        self.lun = self.function / "lun.0"
        self.image_identity = None

    def _pin_image(self):
        identity = self.io.regular_image(self.image)
        if self.image_identity is not None and identity != self.image_identity:
            raise BackupError("image_identity_changed")
        self.image_identity = identity

    def _result(self, arguments, *, query=False):
        result = self.io.query(arguments) if query else self.io.command(arguments)
        if result.returncode != 0:
            raise BackupError("linux_command_failed")
        return result.stdout.strip()

    def _loops(self):
        data = json.loads(self._result(
            ["losetup", "--list", "--json", "--output", "NAME,BACK-FILE,RO"], query=True))
        if not isinstance(data.get("loopdevices"), list):
            raise BackupError("loop_inventory_unverified")
        return [entry for entry in data["loopdevices"] if entry.get("back-file") == str(self.image)]

    @staticmethod
    def _device(loop):
        if not isinstance(loop, str) or not re.fullmatch(r"/dev/loop[0-9]+", loop):
            raise BackupError("invalid_loop_device")
        return loop

    def _block_devices(self, loop):
        data = json.loads(self._result(
            ["lsblk", "--json", "--paths", "--output", "NAME,MAJ:MIN,RO", self._device(loop)], query=True))
        devices = []

        def visit(entries):
            for entry in entries:
                devices.append(entry)
                visit(entry.get("children", []))

        visit(data["blockdevices"])
        if not devices or devices[0]["name"] != loop:
            raise BackupError("loop_inventory_unverified")
        return devices

    def _mounts(self, loops):
        devices = {entry["maj:min"] for loop in loops for entry in self._block_devices(loop)}
        mounts = []
        for line in self.io.read("/proc/self/mountinfo").splitlines():
            parts = line.split()
            if len(parts) < 10 or "-" not in parts:
                raise BackupError("mount_inventory_unverified")
            # Keep escaped mount paths intact; configured paths cannot contain whitespace.
            if parts[2] in devices or parts[4] == str(self.mountpoint):
                separator = parts.index("-")
                mounts.append((parts[4], parts[2], parts[5], parts[separator + 1]))
        return tuple(mounts)

    def observe(self):
        from .hardware import power_diagnostics
        self._pin_image()
        loops = tuple(entry["name"] for entry in self._loops())
        udc = self.io.read(self.root / "UDC")
        backing = self.io.read(self.lun / "file")
        profile_ok = (
            udc in ("", self.settings.udc)
            and self.io.children(self.root / "functions") == [self.function]
            and self.io.children(self.root / "configs") == [self.root / "configs/c.1"]
            and [path for path in self.io.children(self.function) if path.name.startswith("lun.")] == [self.lun]
            and self.io.link_matches(self.root / "configs/c.1/mass_storage.0", self.function)
            and self.io.read(self.lun / "removable") == "1"
            and self.io.read(self.lun / "ro") == "0"
            and self.io.read(self.lun / "nofua") == "0"
            and not any(self.io.exists("/sys/module/" + name) for name in LEGACY_GADGET_MODULES)
            and self.io.read(self.root / "configs/c.1/mass_storage.0/lun.0/ro") == "0"
        )
        # A second mass-storage export is unsupported even when it names another image.
        for gadget in self.io.children("/sys/kernel/config/usb_gadget"):
            if gadget != self.root and self.io.read(gadget / "UDC"):
                profile_ok = False
        power = power_diagnostics(self.io)
        return GadgetObservation(bool(udc), backing,
                                 self.io.read(Path("/sys/class/udc") / self.settings.udc / "state") == "configured",
                                 loops, self._mounts(loops), power["current_ok"] is True, profile_ok)

    def prepare(self):
        """Create one detached configfs profile; do not mount configfs or load modules."""
        if self.io.exists(self.root):
            raise BackupError("existing_gadget_requires_review")
        if any(self.io.exists("/sys/module/" + name) for name in LEGACY_GADGET_MODULES):
            raise BackupError("conflicting_gadget_driver")
        self._pin_image()
        for gadget in self.io.children("/sys/kernel/config/usb_gadget"):
            if self.io.read(gadget / "UDC"):
                raise BackupError("other_gadget_active")
        self.io.mkdir(self.root)
        for attribute, value in (("idVendor", self.settings.vendor_id), ("idProduct", self.settings.product_id),
                                 ("bcdUSB", "0x0200"), ("bcdDevice", "0x0100")):
            self.io.write(self.root / attribute, value)
        self.io.mkdir(self.root / "strings/0x409")
        for attribute, value in (("serialnumber", self.settings.serial), ("manufacturer", "Raspi Cloud Sync Pro"),
                                 ("product", "Raspi Cloud Sync Pro - disposable test")):
            self.io.write(self.root / "strings/0x409" / attribute, value)
        self.io.mkdir(self.root / "configs/c.1")
        self.io.write(self.root / "configs/c.1/MaxPower", "500")
        self.io.mkdir(self.function)
        for attribute, value in (("removable", "1"), ("ro", "0"), ("nofua", "0")):
            self.io.write(self.lun / attribute, value)
        self.io.symlink(self.function, self.root / "configs/c.1/mass_storage.0")

    def attach_gadget(self):
        observation = self.observe()
        if observation.bound or observation.backing or observation.loops or observation.mounts \
                or not observation.profile_ok or not observation.power_ok:
            raise BackupError("image_not_exclusive")
        self.io.write(self.lun / "file", str(self.image))
        self.io.write(self.root / "UDC", self.settings.udc)

    def detach_gadget(self):
        if self.io.read(self.lun / "file"):
            raise BackupError("host_release_unproven")
        self.io.write(self.root / "UDC", "")
        if self.io.read(self.root / "UDC") or self.io.read(self.lun / "file"):
            raise BackupError("gadget_detach_unverified")

    def attach_loop_read_only(self):
        self._pin_image()
        observation = self.observe()
        if observation.bound or observation.backing or observation.loops or observation.mounts:
            raise BackupError("image_not_exclusive")
        loop = self._result(["losetup", "--find", "--show", "--read-only", "--partscan", str(self.image)])
        return self._device(loop)

    def verify_loop_read_only(self, loop):
        entries = self._loops()
        if len(entries) != 1 or entries[0]["name"] != self._device(loop) or entries[0].get("ro") not in (True, 1):
            raise BackupError("loop_not_read_only")
        devices = self._block_devices(loop)
        if not all(entry.get("ro") in (True, 1) for entry in devices) \
                or loop + "p1" not in {entry["name"] for entry in devices}:
            raise BackupError("loop_partition_unverified")

    def check_filesystem(self, loop):
        self.verify_loop_read_only(loop)
        self._result(["fsck.exfat", "-n", self._device(loop) + "p1"])

    def mount_read_only(self, loop):
        self.verify_loop_read_only(loop)
        observation = self.observe()
        if observation.bound or observation.backing or observation.loops != (loop,) or observation.mounts:
            raise BackupError("image_not_exclusive")
        if self.io.children(self.mountpoint):
            raise BackupError("mountpoint_not_empty")
        self._result(["mount", "-t", "exfat", "-o", "ro,nosuid,nodev,noexec", self._device(loop) + "p1",
                      str(self.mountpoint)])

    def verify_mount_read_only(self, loop):
        self.verify_loop_read_only(loop)
        mounts = self._mounts((loop,))
        devices = self._block_devices(loop)
        partition = next(entry for entry in devices if entry["name"] == loop + "p1")
        if len(mounts) != 1 or mounts[0][0] != str(self.mountpoint) or mounts[0][1] != partition["maj:min"] \
                or mounts[0][3] != "exfat" or not {"ro", "nosuid", "nodev", "noexec"}.issubset(set(mounts[0][2].split(","))):
            raise BackupError("mount_not_read_only")

    def unmount(self):
        self._result(["umount", str(self.mountpoint)])

    def detach_loop(self, loop):
        self.verify_loop_read_only(loop)
        if self._mounts((loop,)):
            raise BackupError("loop_still_mounted")
        self._result(["losetup", "--detach", self._device(loop)])
