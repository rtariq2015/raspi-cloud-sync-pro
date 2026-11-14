import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.network_diagnostics import network_status


class NetworkDiagnosticsTests(unittest.TestCase):
    def observe(self, routes=None, usb="10.12.194.1/28", watcher="inactive", connectivity="full"):
        if routes is None:
            routes = [{"dev": "wlan0", "gateway": "192.0.2.1", "metric": 600}]
        def execute(args, **kwargs):
            self.assertFalse(kwargs["check"])
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["timeout"], 5)
            if args[:3] == ["nmcli", "-g", "GENERAL.TYPE,GENERAL.STATE,IP4.ADDRESS"]:
                self.assertEqual(args[3:5], ["device", "show"])
                output = "ethernet\n100 (connected)\n" + usb if args[-1] == "usb0" else \
                    "wifi\n100 (connected)\n192.0.2.22/24"
                return subprocess.CompletedProcess(args, 0, output, "")
            if args == ["ip", "-json", "-4", "route", "show", "default"]:
                return subprocess.CompletedProcess(args, 0, json.dumps(routes), "")
            if args == ["nmcli", "-g", "CONNECTIVITY", "general"]:
                return subprocess.CompletedProcess(args, 0, connectivity, "")
            self.assertEqual(args, ["systemctl", "is-active", "rpi-usb-gadget-ics.service"])
            return subprocess.CompletedProcess(args, 0 if watcher == "active" else 3, watcher, "")
        with mock.patch("subprocess.run", side_effect=execute):
            return network_status()

    def test_matching_local_link_is_only_a_prerequisite_not_authorization(self):
        report = self.observe()
        self.assertTrue(report["share_network_prerequisites_match"])
        self.assertFalse(report["hardware_operations_authorized"])
        self.assertEqual(report["interfaces"]["wlan0"]["ipv4_addresses"], ["192.0.2.22/24"])
        self.assertIn("does not prove Wi-Fi", report["limitations"][0])

    def test_working_usb_client_is_preserved_and_route_preference_is_reported(self):
        report = self.observe(usb="198.51.100.44/24", watcher="active", routes=[
            {"dev": "wlan0", "gateway": "192.0.2.1", "metric": 600},
            {"dev": "usb0", "gateway": "198.51.100.1", "metric": 100},
        ])
        self.assertFalse(report["share_network_prerequisites_match"])
        self.assertEqual(report["preferred_ipv4_route"]["interface"], "usb0")
        self.assertEqual(len(report["notices"]), 3)

    def test_missing_tools_and_timeouts_report_unknown_without_raw_errors(self):
        for error in (FileNotFoundError("private command error"),
                      subprocess.TimeoutExpired("private command", 5, output="private output")):
            with self.subTest(error=type(error)), mock.patch("subprocess.run", side_effect=error):
                report = network_status()
            self.assertIsNone(report["interfaces"]["usb0"]["ipv4_addresses"])
            self.assertFalse(report["share_network_prerequisites_match"])
            self.assertIsNone(report["preferred_ipv4_route"])
            self.assertNotIn("private", json.dumps(report))

    def test_tied_malformed_and_multipath_routes_do_not_claim_a_preferred_uplink(self):
        routes = [{"dev": "wlan0", "gateway": "192.0.2.1", "metric": 100},
                  {"dev": "usb0", "gateway": "198.51.100.1", "metric": 100}]
        for data in (routes, [], {}, [None], [{"dev": "wlan0", "gateway": "invalid"}],
                     [{"dev": "wlan0", "gateway": "192.0.2.1", "metric": True}],
                     [{"dev": "wlan0", "gateway": "192.0.2.1", "nexthops": []}]):
            with self.subTest(routes=data):
                self.assertIsNone(self.observe(routes=data)["preferred_ipv4_route"])

    def test_unrecognized_states_and_invalid_addresses_are_not_ready(self):
        report = self.observe(usb="invalid", watcher="activating", connectivity="unexpected")
        self.assertIsNone(report["share_network_checks"]["usb_share_address_matches"])
        self.assertEqual(report["official_auto_switcher"], "unknown")
        self.assertEqual(report["cached_global_connectivity"], "unknown")
        self.assertFalse(report["share_network_prerequisites_match"])

    def test_cli_emits_read_only_report_even_when_hardware_is_disabled(self):
        output = io.StringIO()
        with mock.patch("raspi_cloud_sync_pro.network_diagnostics.network_status",
                        return_value={"hardware_operations_authorized": False}), redirect_stdout(output):
            self.assertEqual(main(["network-status"]), 0)
        self.assertFalse(json.loads(output.getvalue())["hardware_operations_authorized"])
