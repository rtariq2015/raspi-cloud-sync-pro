import json
import subprocess
import unittest
from unittest import mock

from raspi_cloud_sync_pro.device_service import observe_internet
from raspi_cloud_sync_pro.network import Connectivity


class InternetObservationTests(unittest.TestCase):
    def observe(self, connectivity="full", routes=None, kind="wifi", metered="no"):
        routes = [{"dev": "wlan0", "gateway": "192.0.2.1", "metric": 600}] if routes is None else routes
        outputs = [connectivity, json.dumps(routes), kind, metered]
        results = [subprocess.CompletedProcess([], 0, output, "") for output in outputs]
        with mock.patch("raspi_cloud_sync_pro.device_service.require_hardware_test_approval"), \
                mock.patch("subprocess.run", side_effect=results) as run:
            result = observe_internet()
        for call in run.call_args_list:
            self.assertNotIn("shell", call.kwargs)
            self.assertIn(call.args[0][0], ("nmcli", "ip"))
        return result

    def test_online_wifi_is_observed_without_changing_network(self):
        self.assertEqual(self.observe(), ("wifi", Connectivity.ONLINE, False))

    def test_portal_missing_route_ambiguous_route_and_unknown_metering_pause(self):
        for kwargs in ({"connectivity": "portal"}, {"routes": []},
                       {"routes": [{"dev": "wlan0"}, {"dev": "usb0"}]}, {"metered": "unknown"}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.observe(**kwargs)[1], Connectivity.UNKNOWN)

    def test_usb_ics_is_distinguished_from_wifi_and_metering_is_retained(self):
        self.assertEqual(self.observe(routes=[{"dev": "usb0", "gateway": "192.168.137.1"}],
                                      kind="ethernet", metered="yes"),
                         ("usb_host_ics", Connectivity.ONLINE, True))
