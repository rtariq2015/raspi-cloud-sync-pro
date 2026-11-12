import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from raspi_cloud_sync_pro.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_status_reports_disabled_capabilities(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["status"]), 0)
        status = json.loads(output.getvalue())
        self.assertEqual(status["hardware_access"], "disabled")
        self.assertEqual(status["cloud_sync"], "not_implemented")
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
