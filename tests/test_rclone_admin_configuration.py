"""Policy checks for an inactive optional rclone GUI template; no rclone calls."""

import configparser
from pathlib import Path
import shlex
import unittest
from unittest.mock import Mock, patch

from raspi_cloud_sync_pro import rclone_admin


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/systemd/raspi-cloud-rclone-admin.service.example"


class RcloneAdminConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")
        cls.unit = configparser.ConfigParser(interpolation=None, strict=False)
        cls.unit.optionxform = str
        cls.unit.read_string(cls.text)
        cls.service = cls.unit["Service"]
        cls.command = shlex.split(cls.service["ExecStart"])

    def test_manual_opt_in_only_and_existing_executable(self):
        self.assertNotIn("Install", self.unit.sections())
        self.assertEqual(self.unit["Unit"]["ConditionPathExists"],
                         "/etc/raspi-cloud-rclone-admin/ADMIN_APPROVED")
        self.assertEqual(self.command[:2], [
            "/opt/raspi-cloud-sync-pro/tools/mass-storage-bin/rclone", "gui"])
        self.assertNotIn("--rc-web-gui", self.command)
        self.assertNotIn("--rc-web-gui-update", self.command)

    def test_both_listeners_are_explicitly_loopback_and_auth_required(self):
        self.assertIn("--addr=127.0.0.1:5573", self.command)
        self.assertIn("--api-addr=127.0.0.1:5572", self.command)
        self.assertEqual([x for x in self.command if x.startswith("--addr=")],
                         ["--addr=127.0.0.1:5573"])
        self.assertEqual([x for x in self.command if x.startswith("--api-addr=")],
                         ["--api-addr=127.0.0.1:5572"])
        self.assertIn("--no-auth=false", self.command)
        self.assertIn("--rc-no-auth=false", self.command)
        self.assertIn("--rc-htpasswd=%d/admin.htpasswd", self.command)
        self.assertIn("--rc-allow-origin=http://127.0.0.1:5573", self.command)

    def test_credentials_are_file_delivered_and_not_logged_or_in_argv(self):
        self.assertEqual(self.service["LoadCredential"],
                         "admin.htpasswd:/etc/raspi-cloud-rclone-admin/admin.htpasswd")
        for option in self.command:
            self.assertFalse(option.startswith(("--pass=", "--rc-pass=", "--user=")))
        self.assertNotIn("EnvironmentFile", self.service)
        self.assertEqual(self.service["StandardOutput"], "null")
        self.assertEqual(self.service["StandardError"], "null")
        self.assertEqual(self.service["UMask"], "0077")

    def test_admin_state_is_isolated_from_working_google_worker(self):
        self.assertEqual(self.service["User"], "raspi-rclone-admin")
        self.assertEqual(self.service["Group"], "raspi-rclone-admin")
        self.assertEqual(self.service["StateDirectoryMode"], "0700")
        self.assertIn("--config=/var/lib/raspi-cloud-rclone-admin/rclone.conf", self.command)
        self.assertNotIn("mass-storage-backup", self.service["ExecStart"])
        self.assertEqual(self.service["InaccessiblePaths"],
                         "-/var/lib/raspi-cloud-sync-pro -/etc/raspi-cloud-sync-pro")
        self.assertEqual(self.service["ProtectSystem"], "strict")
        self.assertEqual(self.service["ProtectHome"], "yes")
        self.assertEqual(self.service["NoNewPrivileges"], "yes")
        self.assertEqual(self.service["CapabilityBoundingSet"], "")

    def test_no_cloud_egress_or_core_service_restart_is_granted(self):
        self.assertEqual(self.service["IPAddressDeny"], "any")
        self.assertEqual(self.service["IPAddressAllow"], "localhost")
        self.assertEqual(self.service["Restart"], "no")
        self.assertNotIn("ExecStopPost", self.service)
        self.assertNotIn("Requires", self.unit["Unit"])
        self.assertNotIn("Wants", self.unit["Unit"])
        self.assertIn("--no-open-browser", self.command)


class RcloneAdminReadinessTests(unittest.TestCase):
    def response(self, status, *, html=False, challenge=None):
        response = Mock(status=status)
        response.getheader.side_effect = lambda name, default="": {
            "Content-Type": "text/html; charset=utf-8" if html else "text/plain",
            "WWW-Authenticate": challenge or "",
        }.get(name, default)
        response.read.return_value = b"<!doctype html><html>local assets</html>"
        return response

    def test_default_disabled_does_no_probes_or_io(self):
        with patch.object(rclone_admin.http.client, "HTTPConnection") as connect:
            self.assertEqual(rclone_admin.status(), {
                "state": "disabled", "open_url": None, "client_tunnel_verified": False})
            self.assertEqual(rclone_admin.status(False)["state"], "disabled")
            connect.assert_not_called()

    def test_non_boolean_opt_in_is_refused_without_probes(self):
        with patch.object(rclone_admin.http.client, "HTTPConnection") as connect:
            for enabled in (1, "true", None, [], {}):
                self.assertEqual(rclone_admin.status(enabled)["state"], "unavailable")
            connect.assert_not_called()

    def test_ready_needs_local_html_and_authentication_challenge(self):
        connections = [Mock(), Mock()]
        connections[0].getresponse.return_value = self.response(200, html=True)
        connections[1].getresponse.return_value = self.response(401, challenge='Basic realm="rclone"')
        with patch.object(rclone_admin.http.client, "HTTPConnection", side_effect=connections) as connect:
            report = rclone_admin.status(True)
        self.assertEqual(report, {"state": "ready", "open_url": "http://127.0.0.1:5573/",
                                  "client_tunnel_verified": False})
        self.assertEqual(connect.call_args_list[0].args, ("127.0.0.1", 5573))
        self.assertEqual(connect.call_args_list[1].args, ("127.0.0.1", 5572))
        self.assertLessEqual(connect.call_args_list[0].kwargs["timeout"], 1)
        self.assertEqual(connections[0].request.call_args.args, ("GET", "/"))
        self.assertEqual(connections[1].request.call_args.args, ("GET", "/core/version"))
        for connection in connections:
            self.assertEqual(connection.request.call_args.kwargs["headers"], {"Connection": "close"})
            connection.close.assert_called_once()
        self.assertNotIn("5572", str(report))

    def test_api_without_authentication_or_wrong_challenge_is_unavailable(self):
        for code, challenge in ((200, ""), (404, ""), (401, ""),
                                (401, "Bearer realm=example"),
                                (401, 'Basic realm="bad\r\nheader"')):
            with self.subTest(code=code, challenge=challenge):
                connections = [Mock(), Mock()]
                connections[0].getresponse.return_value = self.response(200, html=True)
                connections[1].getresponse.return_value = self.response(code, challenge=challenge)
                with patch.object(rclone_admin.http.client, "HTTPConnection", side_effect=connections):
                    self.assertEqual(rclone_admin.status(True)["state"], "unavailable")

    def test_redirects_and_non_html_are_not_accepted_or_followed(self):
        for response in (self.response(302, html=True), self.response(200),
                         self.response(401, html=True)):
            connection = Mock()
            connection.getresponse.return_value = response
            with patch.object(rclone_admin.http.client, "HTTPConnection", return_value=connection) as connect:
                self.assertEqual(rclone_admin.status(True)["state"], "unavailable")
            connect.assert_called_once()
            connection.request.assert_called_once()
            connection.close.assert_called_once()

    def test_network_failures_and_malformed_http_do_not_escape(self):
        for error in (TimeoutError(), ConnectionRefusedError(),
                      rclone_admin.http.client.HTTPException("private diagnostic")):
            connection = Mock()
            connection.request.side_effect = error
            with patch.object(rclone_admin.http.client, "HTTPConnection", return_value=connection):
                self.assertEqual(rclone_admin.status(True), {
                    "state": "unavailable", "open_url": None, "client_tunnel_verified": False})
            connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
