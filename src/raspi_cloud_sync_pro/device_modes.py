"""USB mode selection and conservative future-dashboard switching policy."""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import BackupError


class DeviceMode(str, Enum):
    MASS_STORAGE = "mass_storage"
    ETHERNET_SAMBA = "ethernet_samba"


@dataclass(frozen=True)
class DevicePolicy:
    mode: DeviceMode = DeviceMode.ETHERNET_SAMBA
    approved_uplinks: tuple = ("wifi",)
    allow_metered: bool = False

    def __post_init__(self):
        if not isinstance(self.mode, DeviceMode) or type(self.allow_metered) is not bool \
                or not isinstance(self.approved_uplinks, tuple) \
                or any(value not in ("wifi", "usb_host_ics") for value in self.approved_uplinks):
            raise BackupError("invalid_device_policy")

    @classmethod
    def load(cls, path: Path):
        try:
            data = json.loads(Path(path).read_text())
            if not isinstance(data, dict) or set(data) != {"schema_version", "mode", "approved_uplinks", "allow_metered"} \
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1 \
                    or not isinstance(data["approved_uplinks"], list) \
                    or any(value not in ("wifi", "usb_host_ics") for value in data["approved_uplinks"]):
                raise ValueError()
            return cls(DeviceMode(data["mode"]), tuple(data["approved_uplinks"]), data["allow_metered"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise BackupError("invalid_device_policy") from exc

    def plan(self):
        return {"selected_mode": self.mode.value, "hardware_operations_enabled": False,
                "simultaneous_modes_supported": False, "live_switch_supported": False,
                "ethernet_provider": "raspberrypi/rpi-usb-gadget" if self.mode == DeviceMode.ETHERNET_SAMBA else None,
                "internet_approval": list(self.approved_uplinks),
                "allow_metered": self.allow_metered,
                "activation": "separate approved provisioning and reboot required"}


@dataclass(frozen=True)
class SwitchEvidence:
    ingress_frozen: bool
    active_smb_handles: int
    pending_smb_writes: int
    staging_active: bool
    gadget_detached: bool
    local_image_unmounted: bool
    loop_detached: bool


def validate_switch(evidence: SwitchEvidence):
    """Planning guard only. Trusted adapters must freeze ingress before counting handles.

    A future dashboard cannot pass its own booleans to authorize a live switch.
    This phase supports only a reviewed offline switch followed by a reboot.
    """
    if any(type(value) is not bool for value in (
        evidence.ingress_frozen, evidence.staging_active, evidence.gadget_detached,
        evidence.local_image_unmounted, evidence.loop_detached,
    )) or type(evidence.active_smb_handles) is not int or type(evidence.pending_smb_writes) is not int \
            or not evidence.ingress_frozen or evidence.active_smb_handles != 0 \
            or evidence.pending_smb_writes != 0 or evidence.staging_active \
            or not evidence.gadget_detached or not evidence.local_image_unmounted or not evidence.loop_detached:
        raise BackupError("usb_mode_switch_busy_or_unproven")
    return "offline_reboot_required"


def approved_internet(policy: DevicePolicy, observed_uplink: str, connectivity) -> bool:
    from .network import Connectivity
    return connectivity is Connectivity.ONLINE and observed_uplink in policy.approved_uplinks
