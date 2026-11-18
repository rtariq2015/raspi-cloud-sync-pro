"""Read-only rclone metadata checks; real integration never initializes a cloud remote."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.cloud_readiness import _capture_inventory, cloud_readiness
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.staged_worker import WorkerSettings

RCLONE = os.environ.get("RASPI_TEST_RCLONE") or shutil.which("rclone")


class CloudReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "rclone.conf"
        self.config.write_text("[drive]\ntype=drive\n")
        self.config.chmod(0o600)
        self.binary = self.root / "rclone"
        self.binary.write_text("#!/bin/sh\nexit 1\n")
        self.binary.chmod(0o700)
        self.settings = WorkerSettings.load(Path(__file__).resolve().parents[1] / "config/worker.example.json")
        self.settings = replace(self.settings, rclone_config=self.config,
                                state=self.root / "state", staging=self.root / "staging")

    def metadata(self, data):
        with mock.patch("raspi_cloud_sync_pro.cloud_readiness._capture_inventory",
                        return_value=json.dumps(data).encode()) as capture:
            report = cloud_readiness(self.settings, executable=self.binary)
        command = capture.call_args.args[0]
        self.assertEqual(command[1:3], ["listremotes", "--json"])
        self.assertIn("--ask-password=false", command)
        self.assertNotIn("copy", command)
        return report

    def test_matching_backend_types_are_not_account_or_endpoint_verification(self):
        report = self.metadata([
            {"name": "koofr", "type": "webdav", "source": "file", "description": "PRIVATE-DESCRIPTION"},
            {"name": "gdrive", "type": "drive", "source": "file", "token": "PRIVATE-TOKEN"},
            {"name": "unrelated", "type": "sftp", "source": "file"},
        ])
        self.assertTrue(report["backend_types_match"])
        self.assertFalse(report["cloud_accounts_verified"])
        self.assertFalse(report["uploads_activated"])
        self.assertFalse(report["cloud_network_tested"])
        self.assertEqual(report["tls_and_server_identity"], "not_tested")
        self.assertEqual(report["koofr_endpoint"], "not_checked")
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertNotIn("unrelated", json.dumps(report))

    def test_missing_mismatched_and_alias_backends_are_distinct(self):
        for backend, expected in ((None, "missing_remote"), ("drive", "backend_mismatch"),
                                  ("alias", "alias_backend"), ("sftp", "backend_mismatch")):
            records = [{"name": "koofr", "type": backend, "source": "file"}] if backend else []
            report = self.metadata(records)
            self.assertFalse(report["backend_types_match"])
            self.assertEqual(report["destinations"][0]["status"], expected)
            self.assertEqual(report["destinations"][1]["status"], "missing_remote")

    def test_dropbox_requires_direct_dropbox_metadata_without_claiming_authorization(self):
        self.settings = replace(self.settings, destinations=(("dropbox_rclone", "dropbox:Backups"),))
        for backend, expected in (("dropbox", "backend_matches"), ("drive", "backend_mismatch"),
                                  ("webdav", "backend_mismatch"), ("alias", "alias_backend"),
                                  (None, "missing_remote")):
            records = [{"name": "dropbox", "type": backend, "source": "file"}] if backend else []
            report = self.metadata(records)
            self.assertEqual(report["backend_types_match"], backend == "dropbox")
            self.assertEqual(report["destinations"][0]["status"], expected)
            self.assertEqual(report["destinations"][0]["expected_backend"], "dropbox")
            self.assertFalse(report["cloud_accounts_verified"])
            self.assertFalse(report["uploads_activated"])

    def test_insecure_or_symlink_config_and_unprotected_executable_never_run(self):
        with mock.patch("raspi_cloud_sync_pro.cloud_readiness._capture_inventory") as capture:
            self.config.chmod(0o644)
            self.assertEqual(cloud_readiness(self.settings, executable=self.binary)["error"], "cloud_path_unprotected")
            self.config.chmod(0o600)
            link = self.root / "linked.conf"
            link.symlink_to(self.config)
            self.assertEqual(cloud_readiness(replace(self.settings, rclone_config=link), executable=self.binary)["error"],
                             "cloud_path_unprotected")
            self.binary.chmod(0o777)
            self.assertEqual(cloud_readiness(self.settings, executable=self.binary)["error"], "cloud_path_unprotected")
        capture.assert_not_called()
        self.assertFalse(self.settings.state.exists())

    def test_protected_version_symlink_is_resolved_before_execution(self):
        link = self.root / "rclone-current"
        link.symlink_to(self.binary)
        with mock.patch("raspi_cloud_sync_pro.cloud_readiness._capture_inventory", return_value=b"[]") as capture:
            cloud_readiness(self.settings, executable=link)
        self.assertEqual(capture.call_args.args[0][0], str(self.binary))

    def test_configuration_changed_during_probe_cannot_be_claimed_ready(self):
        def changed(command):
            self.config.write_text("changed synthetic configuration")
            return b"[]"
        with mock.patch("raspi_cloud_sync_pro.cloud_readiness._capture_inventory", side_effect=changed):
            report = cloud_readiness(self.settings, executable=self.binary)
        self.assertEqual(report["error"], "cloud_config_changed")
        self.assertFalse(report["backend_types_match"])

    def test_executable_symlink_loop_is_unavailable_without_running_a_probe(self):
        link = self.root / "loop"
        link.symlink_to(link)
        with mock.patch("raspi_cloud_sync_pro.cloud_readiness._capture_inventory") as capture:
            report = cloud_readiness(self.settings, executable=link)
        self.assertEqual(report["error"], "cloud_metadata_unavailable")
        capture.assert_not_called()

    def test_invalid_duplicate_and_environment_metadata_is_rejected_without_raw_output(self):
        for output in (b'{"private":"PRIVATE"}', b'not-json-PRIVATE', b'[' * 2000 + b']' * 2000,
                       b'[{"name":"koofr","type":"webdav","source":"environment"}]',
                       b'[{"name":"koofr","type":"webdav","source":"file"},'
                       b'{"name":"koofr","type":"drive","source":"file"}]'):
            with mock.patch("raspi_cloud_sync_pro.cloud_readiness._capture_inventory", return_value=output):
                report = cloud_readiness(self.settings, executable=self.binary)
            self.assertEqual(report["error"], "cloud_inventory_invalid")
            self.assertNotIn("PRIVATE", json.dumps(report))

    def test_probe_removes_environment_overrides_but_preserves_encrypted_config_secret(self):
        source = ("import json,os; print(json.dumps({k:v for k,v in os.environ.items() "
                  "if k.startswith('RCLONE_')}))")
        with mock.patch.dict(os.environ, {"RCLONE_CONFIG_BAD_TYPE": "drive", "RCLONE_DUMP": "auth",
                                        "RCLONE_CONFIG_PASS": "synthetic-private-secret"}):
            output = _capture_inventory([sys.executable, "-c", source])
        self.assertEqual(json.loads(output), {"RCLONE_CONFIG_PASS": "synthetic-private-secret"})

    def test_output_timeout_and_process_failure_are_bounded_and_sanitized(self):
        for source, code in (("import time;time.sleep(2)", "cloud_inventory_timeout"),
                             ("print('PRIVATE'*20000)", "cloud_inventory_too_large"),
                             ("import sys;sys.stderr.write('PRIVATE');sys.exit(3)", "cloud_inventory_unavailable")):
            with self.assertRaises(BackupError) as error:
                _capture_inventory([sys.executable, "-c", source], timeout=0.2)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_cli_reports_metadata_without_creating_a_worker_or_printing_errors(self):
        planning = json.loads((Path(__file__).resolve().parents[1] / "config/worker.example.json").read_text())
        planning["rclone_config"] = str(self.config)
        settings_file = self.root / "worker.json"
        settings_file.write_text(json.dumps(planning))
        for valid, expected_exit in ((True, 0), (False, 2)):
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch("raspi_cloud_sync_pro.cloud_readiness.cloud_readiness",
                            return_value={"backend_types_match": valid}), \
                    redirect_stdout(output), redirect_stderr(errors):
                result = main(["cloud-check", "--config", str(settings_file), "--rclone", str(self.binary)])
            self.assertEqual(result, expected_exit)
            self.assertEqual(json.loads(output.getvalue())["backend_types_match"], valid)
            self.assertEqual(errors.getvalue(), "")

    @unittest.skipUnless(RCLONE, "set RASPI_TEST_RCLONE for local-only metadata integration")
    def test_real_rclone_metadata_does_not_read_back_tokens_or_modify_config(self):
        self.config.write_text('[koofr]\ntype = webdav\nurl = https://example.invalid\n'
                               'user = synthetic-user\npass = synthetic-obscured-value\n'
                               'description = PRIVATE-DESCRIPTION\n\n'
                               '[gdrive]\ntype = drive\ntoken = {"access_token":"PRIVATE-TOKEN"}\n\n'
                               '[dropbox]\ntype = dropbox\ntoken = {"access_token":"PRIVATE-DROPBOX-TOKEN"}\n')
        self.settings = replace(self.settings, destinations=self.settings.destinations +
                                (("dropbox_rclone", "dropbox:Backups"),))
        before = self.config.read_bytes()
        report = cloud_readiness(self.settings, executable=str(Path(RCLONE).absolute()))
        self.assertTrue(report["backend_types_match"], report)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertFalse(report["cloud_accounts_verified"])
        self.assertFalse(report["uploads_activated"])
