import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_status_reports_disabled_capabilities(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["status"]), 0)
        status = json.loads(output.getvalue())
        self.assertEqual(status["hardware_access"], "disabled")
        self.assertEqual(status["cloud_sync"], "adapters_available_service_disabled")
        self.assertEqual(status["backup_engine"], "staged_files_only")
        self.assertFalse(status["automatic_deletion"])

    def test_config_check_explains_limits(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["check-config", str(ROOT / "config/config.example.json")])
        self.assertEqual(result, 0)
        self.assertIn("remain disabled", output.getvalue())

    def test_invalid_config_returns_error_without_traceback(self):
        output = io.StringIO()
        with redirect_stderr(output):
            result = main(["check-config", str(ROOT / "config/does-not-exist.json")])
        self.assertEqual(result, 2)
        self.assertIn("Invalid configuration", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_no_hardware_or_sync_command_is_exposed(self):
        for command in ("setup", "mount", "sync", "delete", "flash"):
            with self.subTest(command=command), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main([command])
                self.assertEqual(result.exception.code, 2)

    def test_google_handoff_errors_do_not_disclose_private_exception_context(self):
        operations = (
            ("export_google_authorization", ["google-export-authorization", "--state", "/private/dashboard",
             "--public-origin", "https://device.example", "--output-fd", "3"]),
            ("prepare_google_destination", ["google-prepare-destination", "--state", "/private/backup",
             "--input-fd", "3", "--folder-name", "Raspi-Cloud-Sync-Pro"]),
        )
        for function, arguments in operations:
            with self.subTest(function=function):
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch("raspi_cloud_sync_pro.google_destination." + function,
                                side_effect=ValueError("private-test-token-and-client-secret")), \
                        redirect_stdout(output), redirect_stderr(errors):
                    self.assertEqual(main(arguments), 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("no uploads were enabled", errors.getvalue())
                self.assertNotIn("private-test-token", errors.getvalue())
                self.assertNotIn("Traceback", errors.getvalue())

    def test_google_handoff_refuses_standard_streams_without_prompting(self):
        operations = (
            ["google-export-authorization", "--state", "/not-present",
             "--public-origin", "https://device.example", "--output-fd", "1"],
            ["google-prepare-destination", "--state", "/not-present",
             "--input-fd", "0", "--folder-name", "Raspi-Cloud-Sync-Pro"],
        )
        for arguments in operations:
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(command=arguments[0]), redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main(arguments), 2)
            self.assertEqual(output.getvalue(), "")
            self.assertNotIn("Traceback", errors.getvalue())
