"""Read-only USB/power diagnostics and conservative Pi readiness checks."""

import re
import shutil
from pathlib import Path
from .errors import BackupError


def power_diagnostics(io):
    try:
        value = io.power()
    except (OSError, RuntimeError, BackupError):
        value = None
    match = re.fullmatch(r"throttled=0x([0-9a-fA-F]+)", value or "")
    if not match:
        return {"available": False, "current_ok": None, "faults": [], "historical_faults": [],
                "power_budget_verified": False}
    bits = int(match[1], 16)
    names = ("undervoltage", "frequency_capped", "throttled", "soft_temperature_limit")
    faults = [name for bit, name in enumerate(names) if bits & (1 << bit)]
    history = [name for bit, name in enumerate(names) if bits & (1 << (bit + 16))]
    return {"available": True, "current_ok": not faults, "faults": faults,
            "historical_faults": history, "power_budget_verified": False}


def readiness(io=None, settings=None, mode="ethernet_samba", policy=None):
    from .linux_gadget import HARDWARE_OPERATIONS_ENABLED, LiveLinuxIO
    io = LiveLinuxIO() if io is None else io

    def read(path):
        try:
            return io.read(path).replace("\0", "").strip()
        except (OSError, ValueError):
            return None

    def children(path):
        try:
            return io.children(path)
        except OSError:
            return []

    model = read("/proc/device-tree/model")
    checks = [{"name": "pi_zero_2_w", "ok": bool(model and "Raspberry Pi Zero 2 W" in model)}]
    udcs = []
    for entry in children("/sys/class/udc"):
        udcs.append({"name": entry.name, "state": read(entry / "state"),
                     "current_speed": read(entry / "current_speed"),
                     "maximum_speed": read(entry / "maximum_speed")})
    checks.append({"name": "usb_device_controller", "ok": len(udcs) == 1})
    if mode == "mass_storage":
        checks.append({"name": "configfs_available", "ok": io.exists("/sys/kernel/config/usb_gadget")})
        checks.append({"name": "ethernet_gadget_absent", "ok": not io.exists("/sys/module/g_ether")})
    else:
        checks.append({"name": "official_provider_available", "ok": io.exists("/usr/bin/rpi-usb-gadget")})
    checks.append({"name": "legacy_mass_storage_absent", "ok": not io.exists("/sys/module/g_mass_storage")})
    power = power_diagnostics(io)
    checks.append({"name": "firmware_current_power_status", "ok": power["current_ok"] is True})
    required_tools = ("losetup", "lsblk", "mount", "umount", "fsck.exfat") if mode == "mass_storage" \
        else ("nmcli", "smbd", "testparm", "dnsmasq", "findmnt")
    tools = {name: shutil.which(name) is not None for name in required_tools}
    checks.append({"name": "required_tools", "ok": all(tools.values())})
    if settings is not None:
        checks.append({"name": "selected_udc_present", "ok": settings.udc in {u["name"] for u in udcs}})
        try:
            io.regular_image(settings.image)
            image_ok = True
        except (OSError, ValueError, RuntimeError):
            image_ok = False
        except Exception:
            image_ok = False
        checks.append({"name": "protected_regular_test_image", "ok": image_ok})
        for name, path in (("dedicated_mountpoint", settings.mountpoint),
                           ("protected_state_directory", settings.state_directory),
                           ("protected_staging_directory", settings.staging_directory)):
            try:
                io.protected_directory(path)
                ok = True
            except Exception:
                ok = False
            checks.append({"name": name, "ok": ok})
    elif mode == "ethernet_samba" and policy is not None:
        checks.append({"name": "reviewed_mode_policy", "ok": policy.mode.value == mode})
        checks.append({"name": "usb_network_interface", "ok": io.exists("/sys/class/net/usb0")})
        checks.append({"name": "pi_share_directory", "ok": io.exists("/srv/raspi-cloud-sync-pro/share")})
    else:
        checks.append({"name": "reviewed_test_configuration", "ok": False})
    return {
        "mode": mode, "model": model, "usb_controllers": udcs, "power": power, "tools": tools,
        "checks": checks, "prerequisites_ok": all(c["ok"] for c in checks),
        "hardware_operations_enabled": HARDWARE_OPERATIONS_ENABLED,
        "ready_for_hardware_operations": False,
        "limitations": ["No cable current or voltage measurement", "No proof of host flush or ejection",
                        "No image mount, repair, formatting, USB changes or service activation performed",
                        "Physical tests require separate approval and reviewed code gate"],
    }
