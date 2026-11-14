"""One-shot root coordinator CLI; all native/cloud operations remain mocked."""

import ast
import configparser
import io
import json
import os
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import cli
from raspi_cloud_sync_pro import google_test_broker as broker
from raspi_cloud_sync_pro.errors import BackupError

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-root-config-token-folder-identifier-exception"
ERROR = "Google Test coordinator unavailable; no uploads were enabled and no private details were printed.\n"


class GoogleTestBrokerCliTests(unittest.TestCase):
    @staticmethod
    def invoke(arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def test_exact_config_dispatch_and_fixed_status_exit_codes(self):
        settings = broker.GoogleTestBrokerSettings()
        cases = (("disabled", "disabled", 0), ("idle", "no_request", 0), ("idle", "no_test", 0),
                 ("tested", "test_verified_not_enabled", 0), ("failed", "cleanup_unconfirmed", 2),
                 ("failed", "configuration_invalid", 2))
        for status, suffix, expected in cases:
            report = broker._result(status, "google_test_broker_" + suffix)
            with self.subTest(status=status, suffix=suffix), \
                    mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=settings) as load, \
                    mock.patch.object(broker, "run_google_test_broker", return_value=report) as run:
                code, output, errors = self.invoke(["google-test-broker", "--config", "/protected/coordinator.json"])
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(output), report)
            self.assertEqual(errors, "")
            load.assert_called_once_with(Path("/protected/coordinator.json"))
            run.assert_called_once_with(settings)

    def test_default_disabled_real_coordinator_skips_all_privileged_work(self):
        settings = broker.GoogleTestBrokerSettings()
        with mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=settings), \
                mock.patch.object(broker, "_gates") as gates, \
                mock.patch.object(broker, "_command") as command, \
                mock.patch.object(broker.preparation, "_state_directory") as state, \
                mock.patch.object(broker, "_orchestrate") as orchestrate:
            code, output, errors = self.invoke(["google-test-broker", "--config", "/protected/disabled.json"])
        self.assertEqual((code, json.loads(output), errors), (0, broker._result("disabled", "google_test_broker_disabled"), ""))
        for operation in (gates, command, state, orchestrate):
            operation.assert_not_called()

    def test_real_unprivileged_config_refuses_without_state_or_process_operations(self):
        if os.geteuid() == 0:
            self.skipTest("Requires a real ordinary-UID configuration file")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            path.write_text(json.dumps({"schema_version": 1, "enabled": False, "public_origin": PRIVATE}))
            path.chmod(0o600)
            with mock.patch.object(broker, "_gates") as gates, mock.patch.object(broker, "_command") as command:
                result = self.invoke(["google-test-broker", "--config", str(path)])
        self.assertEqual(result, (2, "", ERROR))
        gates.assert_not_called()
        command.assert_not_called()
        self.assertNotIn(PRIVATE, result[2])
        self.assertNotIn(directory, result[2])

    def test_loader_and_runtime_exceptions_never_print_private_context(self):
        for target in ("load", "run"):
            for exception in (BackupError, OSError, ValueError, TypeError, RuntimeError, MemoryError):
                with self.subTest(target=target, exception=exception), \
                        mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=broker.GoogleTestBrokerSettings()) as load, \
                        mock.patch.object(broker, "run_google_test_broker") as run:
                    (load if target == "load" else run).side_effect = exception(PRIVATE)
                    result = self.invoke(["google-test-broker", "--config", "/protected/" + PRIVATE])
                self.assertEqual(result, (2, "", ERROR))
                self.assertNotIn(PRIVATE, result[2])
                self.assertNotIn("Traceback", result[2])

    def test_private_parser_errors_are_fixed_and_cannot_add_mutation_options(self):
        options = ([], ["--config"], ["--config", "/protected/config", "--enable"],
                   ["--config", "/protected/config", "--action", "test"],
                   ["--config", "/protected/config", "--request-id", PRIVATE],
                   ["--config", "/protected/config", "--root-gate-fd", "3"],
                   ["--config", "/protected/config", "--rclone", PRIVATE],
                   ["--config", "/protected/config", PRIVATE])
        with mock.patch.object(broker.GoogleTestBrokerSettings, "load") as load, \
                mock.patch.object(broker, "run_google_test_broker") as run:
            for arguments in options:
                with self.subTest(arguments=arguments):
                    self.assertEqual(self.invoke(["google-test-broker", *arguments]), (2, "", ERROR))
        load.assert_not_called()
        run.assert_not_called()

    def test_help_and_sys_argv_path_do_not_mint_intent_or_root_release(self):
        with mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=broker.GoogleTestBrokerSettings()) as load, \
                mock.patch.object(broker.control, "request_google_control") as request, \
                mock.patch.object(broker, "_release") as release:
            code, output, errors = self.invoke(["google-test-broker", "--help"])
            self.assertEqual(code, 0)
            self.assertIn("--config", output)
            self.assertEqual(errors, "")
            load.assert_not_called()
            output = io.StringIO()
            with mock.patch.object(cli.sys, "argv", ["raspi-cloud-sync-pro", "google-test-broker", "--config", "/protected/config"]), \
                    redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "disabled")
        request.assert_not_called()
        release.assert_not_called()

    def test_unknown_private_fields_codes_or_enabled_flags_are_never_relayed(self):
        safe = broker._result("disabled", "google_test_broker_disabled")
        cases = [{**safe, "private": PRIVATE}, {**safe, "code": PRIVATE}, {**safe, "status": PRIVATE},
                 {**safe, "uploads_enabled": True}, {**safe, "account_verified": 0},
                 {**safe, "current_worker_state_checked": True}, {**safe, "status": "tested"},
                 {**safe, "status": "failed"}, None]
        for report in cases:
            with self.subTest(report_type=type(report).__name__), \
                    mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=broker.GoogleTestBrokerSettings()), \
                    mock.patch.object(broker, "run_google_test_broker", return_value=report):
                self.assertEqual(self.invoke(["google-test-broker", "--config", "/protected/config"]), (2, "", ERROR))

    def test_every_current_fixed_broker_failure_code_is_accepted_without_raw_payload(self):
        tree = ast.parse((ROOT / "src/raspi_cloud_sync_pro/google_test_broker.py").read_text())
        codes = {node.args[0].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_fail"
                 and node.args and isinstance(node.args[0], ast.Constant)}
        for code in codes:
            with self.subTest(code=code):
                report = broker._result("failed", code)
                self.assertEqual(cli._google_test_broker_projection(report), report)


class GoogleTestBrokerExamplesTests(unittest.TestCase):
    def test_disabled_config_preserves_existing_private_schema_and_placeholder_gates(self):
        example = json.loads((ROOT / "config/google-test-broker.example.json").read_text())
        self.assertEqual(set(example), broker._FIELDS)
        self.assertIs(example["enabled"], False)
        self.assertIs(example["allow_metered"], False)
        with mock.patch.object(broker.preparation, "_root_json", return_value=(example, (1, 2))):
            loaded = broker.GoogleTestBrokerSettings.load("/protected/config.json")
        self.assertFalse(loaded.enabled)
        self.assertEqual(example["rclone_sha256"], "0" * 64)
        self.assertEqual(example["wifi_profile_uuid"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(example["interpreter"], "/opt/raspi-cloud-sync-pro/releases/0.1.0.dev12/venv/bin/python3")
        self.assertEqual(Path(example["lock_file"]).parent, Path(example["runtime_directory"]))

    def test_service_example_is_uninstalled_oneshot_and_matches_disabled_config(self):
        example = json.loads((ROOT / "config/google-test-broker.example.json").read_text())
        unit = configparser.ConfigParser(interpolation=None)
        path = ROOT / "deploy/systemd/raspi-cloud-google-test-broker.service.example"
        unit.read(path)
        self.assertFalse((path.parent / "raspi-cloud-google-test-broker.service").exists())
        self.assertNotIn("Install", unit)
        self.assertEqual(unit["Unit"]["ConditionPathExists"], example["approval_file"])
        process = unit["Service"]
        self.assertEqual(process["Type"], "oneshot")
        self.assertEqual(process["Restart"], "no")
        self.assertNotIn("User", process)
        self.assertNotIn("Group", process)
        self.assertEqual(shlex.split(process["ExecStart"]), [example["interpreter"], "-I", "-B", "-m",
            "raspi_cloud_sync_pro", "google-test-broker", "--config", "/etc/raspi-cloud-sync-pro/google-test-broker.json"])
        self.assertNotIn("/current/", process["ExecStart"])
        self.assertEqual(process["RuntimeDirectoryMode"], "0700")
        self.assertEqual(process["RuntimeDirectoryPreserve"], "yes")
        self.assertEqual(process["NoNewPrivileges"], "yes")
        self.assertEqual(process["PrivateNetwork"], "no")
        self.assertEqual(process["AmbientCapabilities"], "")
        self.assertEqual(process["RestrictAddressFamilies"].split(), ["AF_UNIX", "AF_INET", "AF_NETLINK"])
        self.assertEqual(set(process["ReadWritePaths"].split()), {example["source_state"], example["runtime_directory"]})
        self.assertIn("CAP_SYS_ADMIN", process["CapabilityBoundingSet"].split())
        self.assertIn("CAP_SYS_PTRACE", process["CapabilityBoundingSet"].split())
        self.assertIn("mount", process["SystemCallFilter"].split()[0])
        self.assertGreater(int(process["TimeoutStartSec"]), broker._TIMEOUT)
        self.assertFalse(list(path.parent.glob("raspi-cloud-google-test-broker.path*")))
