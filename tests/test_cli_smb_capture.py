"""One local capture scan; no Samba deployment, network or cloud access."""

import configparser
import io
import json
import os
import shlex
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import cli, smb_capture as capture
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.staging import ReadyBatch

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-password-path-account-folder-exception"
ERROR = "SMB capture unavailable; no cloud uploads were enabled and no private details were printed.\n"


class SmbCaptureCliTests(unittest.TestCase):
    @staticmethod
    def invoke(arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def test_exact_dispatch_one_scan_fixed_projection_and_exit_codes(self):
        settings = capture.SmbCaptureSettings()
        cases = (("disabled", 0, 0, 0, 0), ("idle", 0, 0, 0, 0), ("captured", 1, 2, 0, 0),
                 ("deferred", 0, 32, 0, 0), ("blocked", 0, 0, 0, 2), ("unavailable", 0, 0, 0, 2),
                 ("idle", 0, 0, 1, 2), ("captured", 1, 0, 1, 2))
        for state, staged, deferred, rejected, expected in cases:
            report = capture._report(state, staged, deferred, rejected)
            with self.subTest(state=state, rejected=rejected), \
                    mock.patch.object(capture.SmbCaptureSettings, "load", return_value=settings) as load, \
                    mock.patch.object(capture, "run_smb_capture", return_value=report) as run:
                code, output, errors = self.invoke(["smb-capture", "--config", "/protected/capture.json"])
            self.assertEqual((code, json.loads(output), errors), (expected, report, ""))
            load.assert_called_once_with(Path("/protected/capture.json"))
            run.assert_called_once_with(settings, once=True)

    def test_disabled_real_runtime_never_checks_host_or_credentials(self):
        with mock.patch.object(capture.SmbCaptureSettings, "load", return_value=capture.SmbCaptureSettings()), \
                mock.patch.object(capture, "_guard") as guard, mock.patch.object(capture, "_credentials") as credentials, \
                mock.patch.object(capture, "LocalSambaReader") as reader, mock.patch.object(capture, "_index") as index:
            code, output, errors = self.invoke(["smb-capture", "--config", "/protected/capture.json"])
        self.assertEqual((code, json.loads(output), errors), (0, capture._report("disabled"), ""))
        for operation in (guard, credentials, reader, index):
            operation.assert_not_called()

    def test_real_ordinary_owned_config_is_refused_before_host_or_network(self):
        if os.geteuid() == 0:
            self.skipTest("Requires ordinary-UID configuration refusal")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / (PRIVATE + ".json")
            path.write_text('{"schema_version":1,"enabled":false}')
            with mock.patch.object(capture, "_guard") as guard, mock.patch.object(capture, "LocalSambaReader") as reader:
                result = self.invoke(["smb-capture", "--config", str(path)])
        self.assertEqual(result, (2, "", ERROR))
        guard.assert_not_called(); reader.assert_not_called()
        self.assertNotIn(temporary, result[2])

    def test_loader_or_runtime_exceptions_suppress_private_context(self):
        for target in ("load", "run"):
            for kind in (BackupError, OSError, ValueError, TypeError, RuntimeError, MemoryError):
                with self.subTest(target=target, kind=kind), \
                        mock.patch.object(capture.SmbCaptureSettings, "load", return_value=capture.SmbCaptureSettings()) as load, \
                        mock.patch.object(capture, "run_smb_capture") as run:
                    (load if target == "load" else run).side_effect = kind(PRIVATE)
                    self.assertEqual(self.invoke(["smb-capture", "--config", "/protected/" + PRIVATE]), (2, "", ERROR))

    def test_private_parser_errors_do_not_echo_args_or_add_action_flags(self):
        cases = ([], ["--config"], ["--config", "/protected/config", "--password", PRIVATE],
                 ["--config", "/protected/config", "--enable"], ["--config", "/protected/config", "--once"],
                 ["--config", "/protected/config", "--provider", PRIVATE],
                 ["--config", "/protected/config", "--change-usb-mode", PRIVATE], [PRIVATE])
        with mock.patch.object(capture.SmbCaptureSettings, "load") as load, mock.patch.object(capture, "run_smb_capture") as run:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    self.assertEqual(self.invoke(["smb-capture", *arguments]), (2, "", ERROR))
        load.assert_not_called(); run.assert_not_called()

    def test_help_and_sys_argv_keep_one_scan_and_other_commands(self):
        with mock.patch.object(capture.SmbCaptureSettings, "load", return_value=capture.SmbCaptureSettings()) as load:
            code, output, errors = self.invoke(["smb-capture", "--help"])
            self.assertEqual((code, errors), (0, ""))
            self.assertIn("--config", output)
            self.assertNotIn("--password", output)
            load.assert_not_called()
            output = io.StringIO()
            with mock.patch.object(cli.sys, "argv", ["program", "smb-capture", "--config", "/protected/capture.json"]), \
                    redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(json.loads(output.getvalue())["capture_state"], "disabled")
        code, output, errors = self.invoke(["status"])
        self.assertEqual((code, errors), (0, ""))
        self.assertIsInstance(json.loads(output), dict)

    def test_invalid_or_private_runtime_reports_are_never_relayed(self):
        safe = capture._report("idle")
        cases = [None, {**safe, "private": PRIVATE}, {**safe, "capture_state": PRIVATE},
                 {**safe, "uploads_enabled": True}, {**safe, "cloud_accounts_verified": 0},
                 {**safe, "automatic_deletion": True}, {**safe, "staged_files": True},
                 {**safe, "deferred_files": -1}, {**safe, "rejected_files": 33},
                 {**safe, "capture_state": "captured"}, {**safe, "capture_state": "deferred"},
                 {**safe, "capture_state": "disabled", "rejected_files": 1}, {**safe, "staged_files": 1},
                 capture._report("captured", 32, 1)]
        for report in cases:
            with self.subTest(report_type=type(report).__name__), \
                    mock.patch.object(capture.SmbCaptureSettings, "load", return_value=capture.SmbCaptureSettings()), \
                    mock.patch.object(capture, "run_smb_capture", return_value=report):
                self.assertEqual(self.invoke(["smb-capture", "--config", "/protected/config"]), (2, "", ERROR))

    def test_real_temp_scan_publishes_only_after_close_and_retains_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            share = root / "share"; share.mkdir()
            (share / "closed.txt").write_bytes(b"completed synthetic file")
            staging = root / "staging"; staging.mkdir(mode=0o700)
            state = root / "state"; state.mkdir(mode=0o700)
            credentials = state / "smb.json"
            credentials.write_text(json.dumps({"username": "raspi-backup", "password": PRIVATE})); credentials.chmod(0o600)
            settings = capture.SmbCaptureSettings(enabled=True, share_name="CloudSync", share_root=share,
                staging_directory=staging, state_directory=state, credentials_file=credentials)
            test = self
            class Reader:
                def __init__(self, *args, approval_guard, **kwargs): self.guard = approval_guard
                @contextmanager
                def open_version(self, relative):
                    self.guard()
                    with (share / relative).open("rb") as stream:
                        yield stream
                        test.assertEqual(list(staging.glob("*.ready")), [])
            mask = os.umask(0o077)
            try:
                with mock.patch.object(capture.SmbCaptureSettings, "load", return_value=settings), \
                        mock.patch.object(capture, "_guard"), mock.patch.object(capture, "LocalSambaReader", Reader), \
                        mock.patch("raspi_cloud_sync_pro.providers.RcloneProvider", side_effect=AssertionError("no provider")):
                    code, output, errors = self.invoke(["smb-capture", "--config", "/protected/config"])
            finally:
                os.umask(mask)
            self.assertEqual((code, json.loads(output), errors), (0, capture._report("captured", 1), ""))
            batches = list(staging.glob("*.ready")); self.assertEqual(len(batches), 1)
            ReadyBatch.load(batches[0])
            self.assertEqual((batches[0] / "files/closed.txt").read_bytes(), b"completed synthetic file")
            self.assertEqual((share / "closed.txt").read_bytes(), b"completed synthetic file")
            self.assertNotIn(PRIVATE, output)
            self.assertNotIn(temporary, output)


class SmbCaptureExamplesTests(unittest.TestCase):
    def test_disabled_nonsecret_config_exact_schema_and_public_authority_split(self):
        example = json.loads((ROOT / "config/smb-capture.example.json").read_text())
        self.assertEqual(set(example), capture._FIELDS)
        self.assertIs(example["enabled"], False)
        self.assertNotIn("password", example)
        authority = Path("/etc/raspi-cloud-sync-pro-smb")
        self.assertEqual(Path(example["approval_file"]).parent, authority)
        self.assertEqual(Path(example["device_policy"]).parent, authority)
        self.assertEqual(Path(example["credentials_file"]).parent, Path(example["state_directory"]))
        self.assertEqual(example["usb_profile_uuid"], "00000000-0000-0000-0000-000000000000")
        with mock.patch.object(capture.private_files, "_root_json", return_value=(example, (1, 2))):
            self.assertFalse(capture.SmbCaptureSettings.load(authority / "capture.json").enabled)
        with mock.patch.object(capture.private_files, "_root_json", return_value=({**example, "enabled": True}, (1, 2))):
            self.assertTrue(capture.SmbCaptureSettings.load(authority / "capture.json").enabled)

    def test_uninstalled_service_has_cap_zero_and_preprovisioned_private_roots(self):
        path = ROOT / "deploy/systemd/raspi-cloud-smb-capture.service.example"
        text = path.read_text()
        unit = configparser.ConfigParser(interpolation=None, strict=False); unit.read_string(text)
        self.assertFalse(path.with_suffix("").exists())
        self.assertNotIn("Install", unit)
        process = unit["Service"]
        self.assertEqual(process["Type"], "oneshot")
        self.assertEqual(process["Restart"], "no")
        self.assertEqual(process["User"], "raspi-backup")
        self.assertEqual(process["Group"], "raspi-sync")
        self.assertEqual(process["UMask"], "0077")
        self.assertEqual(process["CapabilityBoundingSet"], "")
        self.assertEqual(process["AmbientCapabilities"], "")
        self.assertEqual(process["NoNewPrivileges"], "yes")
        self.assertEqual(process["ProtectSystem"], "strict")
        self.assertEqual(process["PrivateNetwork"], "no")
        self.assertNotIn("ReadOnlyPaths", process)
        self.assertEqual(set(process["ReadWritePaths"].split()), {"/srv/raspi-cloud-sync-pro/share",
            "/var/lib/raspi-cloud-sync-pro/smb-staging", "/var/lib/raspi-cloud-sync-pro/smb-capture"})
        self.assertEqual(shlex.split(process["ExecStart"]), ["/opt/raspi-cloud-sync-pro/releases/0.1.0.dev14/venv/bin/python3",
            "-I", "-B", "-m", "raspi_cloud_sync_pro", "smb-capture", "--config", "/etc/raspi-cloud-sync-pro-smb/capture.json"])
        self.assertNotIn("StateDirectory", process)
        self.assertNotIn("RuntimeDirectory", process)
        self.assertNotIn("ExecStartPre", process)
        for field in ("Wants", "Requires"):
            self.assertNotIn(field, unit["Unit"])
        self.assertEqual({line.split("=", 1)[1] for line in text.splitlines() if line.startswith("AssertPathIsDirectory=")},
            {"/srv/raspi-cloud-sync-pro/share", "/var/lib/raspi-cloud-sync-pro/smb-staging", "/var/lib/raspi-cloud-sync-pro/smb-capture"})

    def test_uninstalled_timer_schedules_nonoverlapping_one_scans_without_boot_enable(self):
        path = ROOT / "deploy/systemd/raspi-cloud-smb-capture.timer.example"
        unit = configparser.ConfigParser(interpolation=None); unit.read(path)
        self.assertFalse(path.with_suffix("").exists())
        self.assertNotIn("Install", unit)
        timer = unit["Timer"]
        self.assertEqual(timer["Unit"], "raspi-cloud-smb-capture.service")
        self.assertEqual(timer["OnUnitInactiveSec"], "15")
        self.assertNotIn("OnCalendar", timer)
        self.assertNotIn("Persistent", timer)


if __name__ == "__main__":
    unittest.main()
