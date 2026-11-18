import dataclasses
import unittest

from raspi_cloud_sync_pro.network import Connectivity, Security, WifiProfile, upload_allowed


class WifiTests(unittest.TestCase):
    def test_home_and_metered_hotspot_profiles(self):
        for metered in (False, True):
            profile = WifiProfile("Example Wi-Fi", Security.PERSONAL, metered=metered,
                                  password_ref="secret-store/home", priority=10)
            self.assertIs(profile.validate(), profile)

    def test_enterprise_peap_and_tls_require_server_validation(self):
        peap = WifiProfile("Office", Security.ENTERPRISE, identity="example-user",
                           eap="peap", phase2="mschapv2", password_ref="secret-store/office",
                           ca_certificate="/etc/example/ca.pem", server_domain="radius.example.org")
        tls = WifiProfile("Office", Security.ENTERPRISE, identity="example-user",
                          eap="tls", ca_certificate="/etc/example/ca.pem", server_domain="radius.example.org",
                          client_certificate="/etc/example/client.pem", private_key_ref="secret-store/key")
        for profile in (peap, tls):
            self.assertEqual(profile.validate(), profile)
            for field in ("ca_certificate", "server_domain", "identity"):
                with self.subTest(eap=profile.eap, missing=field), self.assertRaises(ValueError):
                    dataclasses.replace(profile, **{field: None}).validate()
            for domain in ("*", "https://radius.example.org", "a..b"):
                with self.subTest(domain=domain), self.assertRaises(ValueError):
                    dataclasses.replace(profile, server_domain=domain).validate()

    def test_missing_or_conflicting_authentication_fields_rejected(self):
        profiles = (
            WifiProfile("Home", Security.PERSONAL),
            WifiProfile("Guest", Security.OPEN, password_ref="unexpected"),
            WifiProfile("Home", Security.PERSONAL, password_ref="store", eap="peap"),
            WifiProfile("Office", Security.ENTERPRISE, identity="user", eap="peap",
                        ca_certificate="ca", server_domain="domain", password_ref="store"),
            WifiProfile("Office", Security.ENTERPRISE, identity="user", eap="tls",
                        ca_certificate="ca", server_domain="domain", client_certificate="cert"),
        )
        for profile in profiles:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                profile.validate()

    def test_ssid_limits_use_bytes_and_invalid_types_are_rejected(self):
        for ssid in ("", "x" * 33, "界" * 11, "a\0b", None):
            with self.subTest(ssid=ssid), self.assertRaises(ValueError):
                WifiProfile(ssid, Security.OPEN).validate()
        with self.assertRaises(ValueError):
            WifiProfile("valid", "open").validate()
        with self.assertRaises(ValueError):
            WifiProfile("valid", Security.OPEN, metered="false").validate()

    def test_portal_unknown_limited_and_offline_block_uploads(self):
        for state in Connectivity:
            self.assertEqual(upload_allowed(state), state == Connectivity.ONLINE)
        self.assertFalse(upload_allowed(Connectivity.ONLINE, metered=True))
        self.assertTrue(upload_allowed(Connectivity.ONLINE, metered=True, allow_metered=True))
        with self.assertRaises(ValueError):
            upload_allowed("online")
