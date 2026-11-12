"""Regression tests use temporary files and mocks, never real images or providers."""

import configparser
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "src/home/pi"


def load_legacy(name):
    spec = importlib.util.spec_from_file_location(name, LEGACY / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    # Importing must not start a service, create log handlers or launch commands.
    with mock.patch("subprocess.run", side_effect=AssertionError("unexpected subprocess")), \
            mock.patch("logging.handlers.RotatingFileHandler", create=True,
                       side_effect=AssertionError("unexpected log creation")):
        spec.loader.exec_module(module)
    return module


class LegacySafetyTests(unittest.TestCase):
    def test_imports_and_entry_points_have_no_hardware_or_upload_side_effects(self):
        for name in ("copy_new_usb_files", "upload_new_files"):
            with self.subTest(script=name):
                module = load_legacy(name)
                with mock.patch.object(module, "setup_logging") as setup, \
                        mock.patch.object(module.os, "stat", side_effect=AssertionError("image read")), \
                        mock.patch.object(module.subprocess, "run") as run, \
                        self.assertLogs(level="ERROR"):
                    self.assertEqual(module.main(), 2)
                setup.assert_not_called()
                run.assert_not_called()

    def test_legacy_mount_refuses_even_direct_calls(self):
        module = load_legacy("copy_new_usb_files")
        with mock.patch.object(module.subprocess, "call") as call:
            with self.assertRaisesRegex(RuntimeError, "exclusive storage ownership"):
                module.mount("/dev/fake", "/mnt/fake")
        call.assert_not_called()

    def test_retention_settings_cannot_delete_usb_files(self):
        module = load_legacy("copy_new_usb_files")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.txt"
            original.write_bytes(b"retain original")
            empty = root / "empty"
            empty.mkdir()
            with mock.patch.object(module, "SOURCE_DIR", directory), \
                    mock.patch.object(module, "mount") as mount, self.assertLogs(level="WARNING"):
                module.check_and_delete_on_usb([100000], "/dev/fake", {
                    "KeepMaxFilesOnUSB": 0,
                    "DeleteOnUSBCycleTime": 0,
                    "CopyCheckCycleTime": 1,
                    "AutomaticDeletionEnabled": True,
                })
            mount.assert_not_called()
            entries = module.get_all_file_entries_delete_empty_dirs(directory)
            self.assertEqual([Path(entry.path) for entry in entries], [original])
            self.assertEqual(original.read_bytes(), b"retain original")
            self.assertTrue(empty.is_dir())

    def test_staging_survives_disabled_successful_and_failed_providers(self):
        module = load_legacy("upload_new_files")
        for mode in ("disabled", "success", "failure"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                staging = base / "transfer"
                batch = staging / "batch"
                batch.mkdir(parents=True)
                original = batch / "original.txt"
                original.write_bytes(b"retain staged bytes")
                (base / "config.json").write_text(json.dumps({
                    "ActivateDropboxSync": False,
                    "ActivateScpSync": mode != "disabled",
                }))
                # Test the retained loop in isolation; this is not a deployment bypass.
                with mock.patch.object(module, "LEGACY_RUNTIME_DISABLED", False), \
                        mock.patch.object(module, "BASE_PATH", str(base)), \
                        mock.patch.object(module, "SOURCE_BASE_DIR", str(staging)), \
                        mock.patch.object(module, "COPYING_ACTIVE_FILE", str(staging / "copyingActive")), \
                        mock.patch.object(module, "setup_logging"), \
                        mock.patch.object(module, "transfer_via_scp") as transfer, \
                        mock.patch.object(module.time, "sleep", side_effect=RuntimeError("stop test loop")), \
                        mock.patch.object(module.logging, "exception"):
                    if mode == "failure":
                        transfer.side_effect = subprocess.CalledProcessError(1, ["fake-scp"])
                    module.main()
                self.assertEqual(original.read_bytes(), b"retain staged bytes")
                self.assertEqual(transfer.call_count, int(mode != "disabled"))

    def test_failed_scp_process_is_not_reported_as_success(self):
        module = load_legacy("upload_new_files")
        result = subprocess.CompletedProcess(["fake-expect"], 1, "", "failure")
        with mock.patch.object(module.subprocess, "run", return_value=result):
            with self.assertRaises(subprocess.CalledProcessError):
                module.transfer_via_scp("/fake/batch", {"ScpPath": "fake:/backup"})

    def test_systemd_units_cannot_activate_hardware_or_legacy_loops(self):
        for path in LEGACY.glob("*.service"):
            with self.subTest(unit=path.name):
                unit = configparser.ConfigParser()
                unit.read(path)
                self.assertEqual(unit["Service"]["ExecStart"], "/usr/bin/false")
                self.assertEqual(unit["Service"].get("Restart", "no"), "no")

    def test_legacy_cloud_config_is_disabled(self):
        config = json.loads((LEGACY / "config.json").read_text())
        self.assertEqual(config["ActivateDropboxSync"], 0)
        self.assertEqual(config["ActivateScpSync"], 0)

    @unittest.skipUnless(shutil.which("tclsh"), "requires Tcl")
    def test_scp_helper_exits_before_transfer_commands(self):
        # Tcl has no expect/spawn commands: only the quarantine may run.
        result = subprocess.run(
            [shutil.which("tclsh"), str(LEGACY / "scp-copy.exp")],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Legacy SCP disabled", result.stderr)

    def test_setup_guards_precede_all_commands_without_executing_scripts(self):
        # Installation scripts must not be executed on the development computer,
        # even to exercise their quarantine. Shell syntax is checked with sh -n.
        for name in ("execute_setup_bullseye.sh", "execute_setup_bookworm.sh"):
            with self.subTest(script=name):
                lines = (LEGACY / name).read_text().splitlines()
                commands = [line.strip() for line in lines
                            if line.strip() and not line.lstrip().startswith("#")]
                self.assertTrue(commands[0].startswith('echo "Setup disabled:'))
                self.assertEqual(commands[1], "exit 2")
