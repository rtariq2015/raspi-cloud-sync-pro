"""Code-gated prototype runtimes; import and CLI planning never start devices."""

import json
import os
import stat
import subprocess
import time
from pathlib import Path

from .device_modes import DeviceMode, DevicePolicy
from .engine import BackupEngine
from .errors import BackupError
from .gadget import GadgetController
from .history import History
from .linux_gadget import (
    LEGACY_GADGET_MODULES, GadgetSettings, LinuxGadgetBackend, LiveLinuxIO, require_hardware_test_approval,
)
from .network import Connectivity
from .providers import RcloneProvider
from .smb_ingest import LocalSambaReader, NetworkBackupCoordinator, SambaIngestor


def run_controller(config, policy_path, *, recover=False, once=False):
    io = LiveLinuxIO()
    io.require_mutations()  # Must happen before config/state/device operations.
    if DevicePolicy.load(policy_path).mode != DeviceMode.MASS_STORAGE:
        raise BackupError("mass_storage_mode_required")
    settings = GadgetSettings.load(config)
    for path in (settings.mountpoint, settings.state_directory, settings.staging_directory):
        io.protected_directory(path)
    backend = LinuxGadgetBackend(settings, io)
    controller = GadgetController(backend, settings.state_directory)
    with controller.session():
        if not io.exists(backend.root):
            if controller.journal.data is not None or recover:
                raise BackupError("controller_recovery_required")
            backend.prepare()
        if recover:
            return controller.recover_resources()
        controller.start()
        while True:
            result = controller.poll(settings.staging_directory)
            if once:
                return str(result.root) if hasattr(result, "root") else result
            time.sleep(1)


def observe_internet():
    """Read cached NM connectivity and the selected IPv4 default route; never configure."""
    require_hardware_test_approval()

    def query(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False,
                                env={**os.environ, "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        if result.returncode:
            raise ValueError()
        return result.stdout.strip()

    try:
        if query(["nmcli", "-g", "CONNECTIVITY", "general"]) != "full":
            return "unknown", Connectivity.UNKNOWN, True
        routes = json.loads(query(["ip", "-json", "route", "show", "default"]))
        if not routes:
            return "unknown", Connectivity.UNKNOWN, True
        best = min(int(route.get("metric", 0)) for route in routes)
        routes = [route for route in routes if int(route.get("metric", 0)) == best]
        if len(routes) != 1 or "dev" not in routes[0] or "gateway" not in routes[0]:
            return "unknown", Connectivity.UNKNOWN, True
        interface = routes[0]["dev"]
        kind = query(["nmcli", "-g", "GENERAL.TYPE", "device", "show", interface])
        metered = query(["nmcli", "-g", "GENERAL.METERED", "device", "show", interface])
        if metered not in ("yes", "no", "guess-yes", "guess-no"):
            return "unknown", Connectivity.UNKNOWN, True
        uplink = "wifi" if kind in ("wifi", "802-11-wireless") else \
            "usb_host_ics" if interface == "usb0" and kind == "ethernet" else "unknown"
        return uplink, Connectivity.ONLINE, metered in ("yes", "guess-yes")
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return "unknown", Connectivity.UNKNOWN, True


def _secret(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise BackupError("insecure_smb_credentials")
    data = json.loads(path.read_text())
    if set(data) != {"username", "password"} or not all(isinstance(v, str) and v for v in data.values()):
        raise BackupError("invalid_smb_credentials")
    return data


def run_share_service(config, *, once=False):
    require_hardware_test_approval()
    data = json.loads(Path(config).read_text())
    required = {"device_policy", "share_root", "staging_directory", "history", "credentials_file",
                "rclone_config", "destinations"}
    if not isinstance(data, dict) or set(data) != required:
        raise BackupError("invalid_share_service_config")
    policy = DevicePolicy.load(Path(data["device_policy"]))
    if policy.mode != DeviceMode.ETHERNET_SAMBA:
        raise BackupError("ethernet_mode_required")
    if any(Path("/sys/module", name).exists() for name in LEGACY_GADGET_MODULES if name != "g_ether") \
            or not Path("/sys/module/g_ether").exists():
        raise BackupError("ethernet_provider_unverified")
    io = LiveLinuxIO()
    for gadget in io.children("/sys/kernel/config/usb_gadget"):
        if io.read(gadget / "UDC"):
            raise BackupError("conflicting_gadget_driver")
    # A profile flip can interrupt file copies. Do not run the official ICS
    # auto-switcher alongside the fixed local-only USB policy.
    def query(args):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False,
                                    env={**os.environ, "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
            return result.returncode, result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackupError("usb_network_policy_unverified") from exc
    active, _ = query(["systemctl", "is-active", "rpi-usb-gadget-ics.service"])
    address_status, address = query(["nmcli", "-g", "IP4.ADDRESS", "device", "show", "usb0"])
    if active != 3 or address_status != 0 or address != "10.12.194.1/28":
        raise BackupError("usb_network_policy_unverified")
    share, staging = Path(data["share_root"]), Path(data["staging_directory"])
    paths = (share, staging, Path(data["history"]), Path(data["credentials_file"]), Path(data["rclone_config"]))
    if any(not path.is_absolute() or path.is_symlink() for path in paths) \
            or any(path == share or share in path.parents for path in paths[1:]) \
            or staging == share or staging in share.parents:
        raise BackupError("unsafe_share_service_paths")
    mount_status, mount_json = query(["findmnt", "--json", "--target", str(share),
                                      "--output", "FSTYPE,SOURCE,OPTIONS"])
    mounts = json.loads(mount_json).get("filesystems", []) if mount_status == 0 else []
    if len(mounts) != 1 or mounts[0].get("fstype") != "ext4" \
            or mounts[0].get("source", "").startswith("/dev/loop") \
            or "rw" not in mounts[0].get("options", "").split(","):
        raise BackupError("pi_owned_share_filesystem_unverified")
    providers = [RcloneProvider(item["kind"], item["remote"], Path(data["rclone_config"]), enabled=True)
                 for item in data["destinations"]]
    if not providers:
        raise BackupError("backup_destination_required")
    reader = LocalSambaReader(**_secret(data["credentials_file"]))
    with History(Path(data["history"])) as history:
        ingestor = SambaIngestor(reader, staging, history, [p.destination_id for p in providers])
        coordinator = NetworkBackupCoordinator(ingestor, BackupEngine(history), providers, policy)
        while True:
            uplink, connectivity, metered = observe_internet()
            result = coordinator.tick(share, time.time(), observed_uplink=uplink,
                                      connectivity=connectivity, metered=metered)
            if once:
                return result
            time.sleep(5)
