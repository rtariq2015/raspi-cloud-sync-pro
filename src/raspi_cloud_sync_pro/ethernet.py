"""Official external Raspberry Pi Ethernet gadget integration, offline and gated."""

from .device_modes import DeviceMode, validate_switch
from .errors import BackupError
from .linux_gadget import LEGACY_GADGET_MODULES, LiveLinuxIO

OFFICIAL_REPOSITORY = "https://github.com/raspberrypi/rpi-usb-gadget"
REVIEWED_COMMIT = "05ff0940af823868ff995808286bc0bcca8b86b7"


def bound_configfs_gadgets(io):
    # g_ether does not need libcomposite/configfs. An absent tree is normal for
    # the official provider; an existing but unreadable tree must still fail.
    root = "/sys/kernel/config/usb_gadget"
    if not io.exists(root):
        return ()
    return tuple(gadget for gadget in io.children(root) if io.read(gadget / "UDC"))


class OfficialEthernet:
    def __init__(self, io=None):
        self.io = LiveLinuxIO() if io is None else io

    def plan(self, policy):
        if policy.mode != DeviceMode.ETHERNET_SAMBA:
            raise BackupError("ethernet_mode_required")
        return {**policy.plan(), "reviewed_provider_commit": REVIEWED_COMMIT,
                "interface": "usb0", "transport": "official g_ether RNDIS/CDC-ECM",
                "local_address": "10.12.194.1/28", "local_dhcp": "10.12.194.2-10.12.194.14",
                "default_gateway_advertised": False, "samba_guest_access": False,
                "upstream_ics_auto_switcher": "must be disabled before serving files",
                "configuration_templates": ["deploy/NetworkManager/raspi-cloud-usb.nmconnection",
                                            "deploy/dnsmasq/raspi-cloud-usb.conf", "deploy/samba/smb.conf"],
                "activation": "approved offline provisioning, reviewed templates, then reboot"}

    def provision_offline(self, evidence):
        """Never invoke automatically during file transfers or dashboard use."""
        self.io.require_mutations()
        validate_switch(evidence)
        if any(self.io.exists("/sys/module/" + name) for name in LEGACY_GADGET_MODULES):
            raise BackupError("conflicting_gadget_driver")
        if bound_configfs_gadgets(self.io):
            raise BackupError("other_gadget_active")
        if not self.io.exists("/usr/bin/rpi-usb-gadget"):
            raise BackupError("official_ethernet_provider_missing")
        # Official `on` edits Pi boot/NM configuration and starts its ICS watcher.
        # Do not start Samba here. Install the reviewed local-only configuration
        # and disable the watcher before the subsequent approved reboot.
        for command in (["rpi-usb-gadget", "on"],
                        ["systemctl", "disable", "--now", "rpi-usb-gadget-ics.service"]):
            result = self.io.provision_command(command)
            if result.returncode != 0:
                raise BackupError("ethernet_provision_failed")
        return "offline_network_and_samba_configuration_then_reboot_required"
