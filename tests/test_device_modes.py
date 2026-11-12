import configparser
import dataclasses
import io
import ipaddress
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.device_modes import DeviceMode, DevicePolicy, SwitchEvidence, validate_switch
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.ethernet import OfficialEthernet, REVIEWED_COMMIT, bound_configfs_gadgets

ROOT = Path(__file__).resolve().parents[1]


class DeviceModeTests(unittest.TestCase):
    def test_ethernet_is_default_and_mode_plan_has_no_activation_side_effect(self):
        policy = DevicePolicy.load(ROOT / "config/device.example.json")
        self.assertEqual(policy.mode, DeviceMode.ETHERNET_SAMBA)
        self.assertEqual(policy.approved_uplinks, ("wifi",))
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(["mode-plan", str(ROOT / "config/device.example.json")]), 0)
        self.assertIn(REVIEWED_COMMIT, output.getvalue())
        self.assertFalse(policy.plan()["live_switch_supported"])

    def test_switch_rejects_active_or_unknown_transfers_and_requires_offline_reboot(self):
        evidence = SwitchEvidence(True, 0, 0, False, True, True, True)
        self.assertEqual(validate_switch(evidence), "offline_reboot_required")
        for field, value in (("ingress_frozen", False), ("active_smb_handles", 1),
                             ("pending_smb_writes", 1), ("staging_active", True),
                             ("gadget_detached", False), ("local_image_unmounted", False),
                             ("loop_detached", False), ("active_smb_handles", False)):
            with self.subTest(field=field), self.assertRaises(BackupError):
                validate_switch(dataclasses.replace(evidence, **{field: value}))

    def test_empty_uplink_policy_supports_explicit_offline_only_trial(self):
        policy = DevicePolicy(approved_uplinks=())
        self.assertEqual(policy.plan()["internet_approval"], [])

    def test_official_installer_is_never_run_with_default_gate(self):
        with self.assertRaisesRegex(BackupError, "hardware_operations_disabled"):
            OfficialEthernet().provision_offline(SwitchEvidence(True, 0, 0, False, True, True, True))

    def test_offline_provider_adapter_checks_busy_state_and_disables_auto_switcher(self):
        class FakeProviderIO:
            def __init__(self): self.commands = []
            def require_mutations(self): pass
            def exists(self, path): return str(path) == "/usr/bin/rpi-usb-gadget"
            def children(self, path): return []
            def provision_command(self, args):
                self.commands.append(args)
                return subprocess.CompletedProcess(args, 0)
        fixture = FakeProviderIO()
        adapter = OfficialEthernet(fixture)
        idle = SwitchEvidence(True, 0, 0, False, True, True, True)
        with self.assertRaises(BackupError):
            adapter.provision_offline(dataclasses.replace(idle, active_smb_handles=1))
        self.assertEqual(fixture.commands, [])
        adapter.provision_offline(idle)
        self.assertEqual(fixture.commands, [["rpi-usb-gadget", "on"],
                                           ["systemctl", "disable", "--now", "rpi-usb-gadget-ics.service"]])

    def test_official_g_ether_does_not_require_configfs_but_bound_profiles_are_rejected(self):
        from unittest import mock
        fixture = mock.Mock()
        fixture.exists.return_value = False
        self.assertEqual(bound_configfs_gadgets(fixture), ())
        fixture.children.assert_not_called()
        fixture.exists.return_value = True
        profile = Path("/sys/kernel/config/usb_gadget/other")
        fixture.children.return_value = [profile]
        fixture.read.return_value = "mock-udc"
        self.assertEqual(bound_configfs_gadgets(fixture), (profile,))
        fixture.children.side_effect = PermissionError()
        with self.assertRaises(PermissionError): bound_configfs_gadgets(fixture)

    def test_samba_templates_require_authentication_and_exclude_guest_smb1(self):
        samba = configparser.ConfigParser()
        samba.read(ROOT / "deploy/samba/smb.conf")
        self.assertEqual(samba["global"]["server min protocol"], "SMB3_00")
        self.assertEqual(samba["global"]["smb encrypt"], "required")
        self.assertEqual(samba["CloudSync"]["guest ok"], "no")
        self.assertEqual(samba["CloudSync"]["read only"], "yes")
        self.assertEqual(samba["CloudSync"]["write list"], "raspi-share")
        self.assertEqual(samba["CloudSync"]["follow symlinks"], "no")

    def test_usb_addressing_advertises_no_default_route_or_dns(self):
        nm = configparser.ConfigParser()
        nm.read(ROOT / "deploy/NetworkManager/raspi-cloud-usb.nmconnection")
        self.assertEqual(nm["connection"]["autoconnect"], "false")
        self.assertEqual(nm["ipv4"]["never-default"], "true")
        network = ipaddress.ip_interface(nm["ipv4"]["address1"]).network
        lines = (ROOT / "deploy/dnsmasq/raspi-cloud-usb.conf").read_text().splitlines()
        self.assertIn("dhcp-option=3", lines)
        self.assertIn("dhcp-option=6", lines)
        pool = next(line.split("=", 1)[1].split(",") for line in lines if line.startswith("dhcp-range="))
        self.assertIn(ipaddress.ip_address(pool[0]), network)
        self.assertIn(ipaddress.ip_address(pool[1]), network)
        self.assertEqual(ipaddress.IPv4Network("0.0.0.0/" + pool[2]).prefixlen, network.prefixlen)

    def test_systemd_templates_are_static_and_unguarded_device_units_are_not_enabled(self):
        for path in (ROOT / "deploy/systemd").glob("*.service"):
            unit = configparser.ConfigParser()
            unit.read(path)
            self.assertNotIn("Install", unit)
            if "diagnostics" not in path.name:
                self.assertIn("DISPOSABLE_TEST_APPROVED", unit["Unit"]["ConditionPathExists"])
                self.assertEqual(unit["Service"]["Restart"], "no")
