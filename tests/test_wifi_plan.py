import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path

from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.network import Security, WifiProfile
from raspi_cloud_sync_pro.wifi_plan import load_wifi_profile, wifi_plan


class WifiPlanTests(unittest.TestCase):
    def test_enterprise_mapping_keeps_ca_domain_and_anonymous_identity(self):
        path = Path(__file__).resolve().parents[1] / "config/wifi-enterprise.example.json"
        profile = load_wifi_profile(path)
        plan = wifi_plan(profile)
        self.assertFalse(plan["changes_applied"])
        self.assertTrue(plan["captive_portal_possible"])
        self.assertEqual(plan["properties"]["802-1x.phase2-auth"], "mschapv2")
        self.assertEqual(plan["properties"]["802-1x.domain-suffix-match"], "radius.example.org")
        self.assertEqual(plan["properties"]["802-1x.anonymous-identity"], "anonymous@example.org")
        self.assertEqual(plan["properties"]["connection.autoconnect"], "no")
        self.assertEqual(plan["secret_references"]["802-1x.password"], "os-secret:work-wifi")
        for field in ("ca_certificate", "server_domain"):
            with self.assertRaises(ValueError):
                wifi_plan(replace(profile, **{field: None}))

    def test_open_network_has_no_secret_and_can_require_portal_sign_in(self):
        plan = wifi_plan(WifiProfile("Guest", Security.OPEN, metered=True))
        self.assertEqual(plan["secret_references"], {})
        self.assertTrue(plan["captive_portal_possible"])
        self.assertEqual(plan["properties"]["connection.metered"], "yes")
        self.assertNotIn("802-11-wireless-security.key-mgmt", plan["properties"])

    def test_identity_and_reference_control_characters_are_rejected(self):
        for profile in (WifiProfile("Home", Security.PERSONAL, password_ref="ref\nsecret"),
                        WifiProfile("Guest", Security.OPEN, anonymous_identity="user")):
            with self.assertRaises(ValueError):
                wifi_plan(profile)

    def test_eap_tls_uses_key_reference_and_client_certificate(self):
        plan = wifi_plan(WifiProfile("Office", Security.ENTERPRISE, identity="user",
                         eap="tls", ca_certificate="/etc/example/ca.pem", server_domain="radius.example.org",
                         client_certificate="/etc/example/client.pem", private_key_ref="os-secret:client-key"))
        self.assertEqual(plan["secret_references"], {"802-1x.private-key": "os-secret:client-key"})
        self.assertEqual(plan["properties"]["802-1x.eap"], "tls")

    def test_literal_password_and_unknown_fields_are_rejected_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wifi.json"
            path.write_text(json.dumps({"schema_version": 1, "profile": {
                "ssid": "Example", "security": "wpa2_personal", "password": "must-not-appear"}}))
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(main(["wifi-plan", str(path)]), 2)
            self.assertNotIn("must-not-appear", output.getvalue())
