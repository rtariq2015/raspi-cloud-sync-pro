"""CLI preparation dispatch, parser boundaries and sanitized local failures."""

import errno
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import google_broker as broker
from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.errors import BackupError

REQUEST_ID = "a" * 32
ORIGIN = "https://synthetic.example"
SOURCE = Path("/private/synthetic-dashboard")
WORKER = Path("/private/synthetic-backup")
CONFIG = Path("/private/synthetic-broker.json")
PRIVATE = "PRIVATE-synthetic-token-client-secret-and-error-context"


class GoogleSetupCliTests(unittest.TestCase):
    def operations(self):
        return (
            (
                "raspi_cloud_sync_pro.google_setup.export_requested_google_setup",
                ["google-setup-export", "--state", str(SOURCE), "--public-origin", ORIGIN,
                 "--request-id", REQUEST_ID, "--metadata-fd", "3", "--authorization-fd", "4"],
                (SOURCE, ORIGIN, REQUEST_ID, 3, 4),
                {"schema_version": 1, "provider": "google_drive", "authorization_exported": True,
                 "uploads_enabled": False},
            ),
            (
                "raspi_cloud_sync_pro.google_setup.record_google_setup_receipt",
                ["google-setup-record", "--state", str(SOURCE), "--public-origin", ORIGIN,
                 "--request-id", REQUEST_ID, "--receipt-fd", "3"],
                (SOURCE, ORIGIN, REQUEST_ID, 3),
                {"provider": "google_drive", "preparation_state": "prepared_untested",
                 "historical": True, "enabled": False, "account_verified": False},
            ),
            (
                "raspi_cloud_sync_pro.google_broker.export_google_preparation_receipt",
                ["google-setup-receipt", "--state", str(WORKER / REQUEST_ID),
                 "--request-id", REQUEST_ID, "--output-fd", "3"],
                (WORKER / REQUEST_ID, REQUEST_ID, 3),
                {"schema_version": 1, "provider": "google_drive", "receipt_exported": True,
                 "uploads_enabled": False, "account_verified": False},
            ),
            (
                "raspi_cloud_sync_pro.google_broker.import_requested_google_setup",
                ["google-setup-import", "--state", str(WORKER), "--request-id", REQUEST_ID,
                 "--root-fd", "3", "--input-fd", "4", "--folder-name", "Synthetic Folder"],
                (WORKER, REQUEST_ID, 3, 4, "Synthetic Folder"),
                {"provider": "google_drive", "preparation_state": "prepared_untested",
                 "enabled": False, "test_required": True, "account_verified": False},
            ),
        )

    @staticmethod
    def invoke(arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = main(arguments)
        return result, output.getvalue(), errors.getvalue()

    def assert_safe_failure(self, result, output, errors):
        self.assertEqual(result, 2)
        self.assertEqual(output, "")
        self.assertEqual(errors, "Google preparation workflow unavailable; no uploads were enabled "
                                "and no private details were printed.\n")
        for private in (PRIVATE, REQUEST_ID, ORIGIN, str(SOURCE), str(WORKER), str(CONFIG)):
            self.assertNotIn(private, errors)
        self.assertNotIn("Traceback", errors)

    def test_all_private_child_commands_parse_paths_and_dispatch_exact_arguments(self):
        for target, arguments, expected, report in self.operations():
            with self.subTest(command=arguments[0]), mock.patch(target, return_value=report) as operation:
                result, output, errors = self.invoke(arguments)
            operation.assert_called_once_with(*expected)
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output), report)
            self.assertEqual(errors, "")
            self.assertTrue(output.endswith("\n"))

    def test_broker_dispatch_loads_config_path_and_passes_only_loaded_settings(self):
        settings = broker.GoogleBrokerSettings()
        report = {"status": "disabled", "code": "google_broker_disabled",
                  "uploads_enabled": False, "account_verified": False}
        with mock.patch.object(broker.GoogleBrokerSettings, "load", return_value=settings) as load, \
             mock.patch.object(broker, "run_google_setup_broker", return_value=report) as run:
            result, output, errors = self.invoke(["google-setup-broker", "--config", str(CONFIG)])
        load.assert_called_once_with(CONFIG)
        run.assert_called_once_with(settings)
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output), report)
        self.assertEqual(errors, "")

    def test_broker_failed_public_report_is_json_with_exit_two(self):
        report = {"status": "failed", "code": "google_broker_approval_required",
                  "uploads_enabled": False, "account_verified": False}
        with mock.patch.object(broker.GoogleBrokerSettings, "load", return_value=broker.GoogleBrokerSettings()), \
             mock.patch.object(broker, "run_google_setup_broker", return_value=report):
            result, output, errors = self.invoke(["google-setup-broker", "--config", str(CONFIG)])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output), report)
        self.assertEqual(errors, "")

    def test_private_child_exception_details_are_suppressed_for_each_boundary(self):
        for target, arguments, _, _ in self.operations():
            for error_type in (BackupError, OSError, ValueError, TypeError):
                with self.subTest(command=arguments[0], exception=error_type.__name__), \
                     mock.patch(target, side_effect=error_type(PRIVATE)):
                    self.assert_safe_failure(*self.invoke(arguments))

    def test_broker_configuration_and_execution_exception_details_are_suppressed(self):
        arguments = ["google-setup-broker", "--config", str(CONFIG)]
        for error_type in (BackupError, OSError, ValueError, TypeError):
            with self.subTest(boundary="configuration", exception=error_type.__name__), \
                 mock.patch.object(broker.GoogleBrokerSettings, "load", side_effect=error_type(PRIVATE)), \
                 mock.patch.object(broker, "run_google_setup_broker") as run:
                self.assert_safe_failure(*self.invoke(arguments))
                run.assert_not_called()
            with self.subTest(boundary="execution", exception=error_type.__name__), \
                 mock.patch.object(broker.GoogleBrokerSettings, "load", return_value=broker.GoogleBrokerSettings()), \
                 mock.patch.object(broker, "run_google_setup_broker", side_effect=error_type(PRIVATE)):
                self.assert_safe_failure(*self.invoke(arguments))

    def test_integer_parser_preserves_positive_and_negative_descriptor_arguments(self):
        for target, arguments, expected, report in self.operations():
            fd_flags = [flag for flag in arguments if flag.endswith("-fd")]
            for flag in fd_flags:
                for text, value in (("+7", 7), ("-7", -7), ("0007", 7)):
                    changed = list(arguments)
                    changed[changed.index(flag) + 1] = text
                    with self.subTest(command=arguments[0], flag=flag, text=text), \
                         mock.patch(target, return_value=report) as operation:
                        result, output, errors = self.invoke(changed)
                    self.assertEqual(result, 0)
                    self.assertEqual(errors, "")
                    actual = list(operation.call_args.args)
                    positions = {
                        "--metadata-fd": 3, "--authorization-fd": 4,
                        "--receipt-fd": 3, "--output-fd": 2,
                        "--root-fd": 2, "--input-fd": 3,
                    }
                    wanted = list(expected)
                    wanted[positions[flag]] = value
                    self.assertEqual(actual, wanted)
                    self.assertEqual(json.loads(output), report)

    def test_noninteger_descriptors_are_parser_errors_before_any_dispatch(self):
        for target, arguments, _, _ in self.operations():
            for flag in [argument for argument in arguments if argument.endswith("-fd")]:
                for value in ("not-an-integer", "3.5", "0x3"):
                    changed = list(arguments)
                    changed[changed.index(flag) + 1] = value
                    output, errors = io.StringIO(), io.StringIO()
                    with self.subTest(command=arguments[0], flag=flag, value=value), \
                         mock.patch(target) as operation, redirect_stdout(output), redirect_stderr(errors):
                        with self.assertRaises(SystemExit) as failure:
                            main(changed)
                    self.assertEqual(failure.exception.code, 2)
                    operation.assert_not_called()
                    self.assertEqual(output.getvalue(), "")
                    self.assertIn("invalid int value", errors.getvalue())
                    self.assertNotIn("Traceback", errors.getvalue())

    def test_every_declared_child_argument_is_required_before_dispatch(self):
        for target, arguments, _, _ in self.operations():
            for index in range(1, len(arguments), 2):
                changed = arguments[:index] + arguments[index + 2:]
                with self.subTest(command=arguments[0], missing=arguments[index]), \
                     mock.patch(target) as operation, redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as failure:
                        main(changed)
                self.assertEqual(failure.exception.code, 2)
                operation.assert_not_called()
        with mock.patch.object(broker.GoogleBrokerSettings, "load") as load, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                main(["google-setup-broker"])
        self.assertEqual(failure.exception.code, 2)
        load.assert_not_called()

    def test_parser_exposes_no_enable_test_upload_or_delete_option(self):
        for target, arguments, _, _ in self.operations():
            for option in ("--enable", "--test-account", "--upload", "--delete"):
                with self.subTest(command=arguments[0], option=option), \
                     mock.patch(target) as operation, redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as failure:
                        main(arguments + [option])
                self.assertEqual(failure.exception.code, 2)
                operation.assert_not_called()

    def test_actual_negative_descriptors_fail_safely_before_private_files_are_opened(self):
        for _, arguments, _, _ in self.operations():
            changed = list(arguments)
            for index, argument in enumerate(changed):
                if argument.endswith("-fd"):
                    changed[index + 1] = "-3"
            with self.subTest(command=arguments[0]), \
                 mock.patch("os.open", side_effect=AssertionError("filesystem forbidden")), \
                 mock.patch("subprocess.Popen", side_effect=AssertionError("process forbidden")), \
                 mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
                self.assert_safe_failure(*self.invoke(changed))

    def test_actual_stdio_descriptors_fail_without_closing_them(self):
        for _, arguments, _, _ in self.operations():
            for standard in (0, 1, 2):
                changed = list(arguments)
                for index, argument in enumerate(changed):
                    if argument.endswith("-fd"):
                        changed[index + 1] = str(standard)
                with self.subTest(command=arguments[0], standard=standard):
                    self.assert_safe_failure(*self.invoke(changed))
                os.fstat(standard)

    @unittest.skipIf(os.geteuid() == 0, "Accepted channels require an ordinary UID")
    def test_actual_valid_export_channels_close_on_missing_source_without_stdout_payload(self):
        metadata_read, metadata_write = os.pipe()
        authorization_read, authorization_write = os.pipe()
        try:
            arguments = ["google-setup-export", "--state", str(SOURCE), "--public-origin", ORIGIN,
                         "--request-id", REQUEST_ID, "--metadata-fd", str(metadata_write),
                         "--authorization-fd", str(authorization_write)]
            self.assert_safe_failure(*self.invoke(arguments))
            for descriptor in (metadata_write, authorization_write):
                with self.assertRaises(OSError) as failure:
                    os.fstat(descriptor)
                self.assertEqual(failure.exception.errno, errno.EBADF)
            self.assertEqual(os.read(metadata_read, 1), b"")
            self.assertEqual(os.read(authorization_read, 1), b"")
        finally:
            os.close(metadata_read)
            os.close(authorization_read)
            for descriptor in (metadata_write, authorization_write):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def test_disabled_broker_dispatch_reads_only_config_and_never_mutates_or_spawns(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sentinel = directory / "sentinel"
            sentinel.write_bytes(b"unchanged synthetic state")
            config = directory / "broker.json"
            data = {"schema_version": 1, "enabled": False,
                    "source_state": str(directory / "dashboard"),
                    "backup_state": str(directory / "worker")}
            before = sentinel.read_bytes()
            with mock.patch.object(broker, "_root_json", return_value=(data, (1,))) as read_config, \
                 mock.patch.object(broker, "_platform_gate", side_effect=AssertionError("hardware forbidden")), \
                 mock.patch.object(broker, "_state_directory", side_effect=AssertionError("state forbidden")), \
                 mock.patch.object(broker, "_broker_lock", side_effect=AssertionError("lock forbidden")), \
                 mock.patch("os.open", side_effect=AssertionError("open forbidden")), \
                 mock.patch("os.mkdir", side_effect=AssertionError("mkdir forbidden")), \
                 mock.patch("subprocess.Popen", side_effect=AssertionError("process forbidden")), \
                 mock.patch("os.system", side_effect=AssertionError("shell forbidden")), \
                 mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
                result, output, errors = self.invoke(["google-setup-broker", "--config", str(config)])
            read_config.assert_called_once_with(config)
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output), {
                "status": "disabled", "code": "google_broker_disabled",
                "uploads_enabled": False, "account_verified": False,
            })
            self.assertEqual(errors, "")
            self.assertEqual(sentinel.read_bytes(), before)
            self.assertEqual(list(directory.iterdir()), [sentinel])
            self.assertNotIn(str(directory), output)


if __name__ == "__main__":
    unittest.main()
