import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.engine import BackupEngine
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.network import Connectivity
from raspi_cloud_sync_pro.providers import RcloneProvider
from raspi_cloud_sync_pro.staging import stage_snapshot
from test_storage import staging_owner


class RcloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "rclone.conf"
        self.config.write_text("[test]\ntype = webdav\n")
        self.config.chmod(0o600)
        source = self.base / "exclusive-fixture"
        source.mkdir()
        (source / "a file;$(touch never).txt").write_bytes(b"file contents")
        self.batch = stage_snapshot(source, self.base / "staging", staging_owner())
        self.item = self.batch.files[0]

    def provider(self, *, kind="koofr_webdav", **kwargs):
        return RcloneProvider(kind, "test:backup", self.config, **kwargs)

    def test_adapter_is_disabled_by_default(self):
        for kind in ("koofr_webdav", "google_drive_rclone", "dropbox_rclone"):
            with mock.patch("subprocess.run") as run:
                with self.assertRaisesRegex(BackupError, "provider_disabled"):
                    self.provider(kind=kind).upload(self.batch, self.item)
            run.assert_not_called()

    def test_all_provider_kinds_use_bounded_copy_without_shell_or_deletion(self):
        for kind in ("koofr_webdav", "google_drive_rclone", "dropbox_rclone"):
            provider = RcloneProvider(kind, "test:backup", self.config, enabled=True)
            with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
                provider.upload(self.batch, self.item)
            args, options = run.call_args
            command = args[0]
            self.assertEqual(command[:2], ["rclone", "copyto"])
            self.assertEqual(command[2], str(self.batch.root / "files" / self.item.relative))
            self.assertEqual(command[3], "test:backup/" + self.item.remote_key)
            self.assertIn("--immutable", command)
            self.assertIn("--checksum", command)
            self.assertEqual(command[command.index("--transfers") + 1], "1")
            self.assertNotIn("shell", options)
            self.assertEqual(options["timeout"], 900)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            self.assertNotIn(command[1], ("sync", "delete", "purge", "move"))

    def test_verification_downloads_and_hashes_against_manifest(self):
        def inspect(command, **kwargs):
            self.assertEqual(command[:3], ["rclone", "checksum", "sha256"])
            self.assertEqual(Path(command[3]).read_text(),
                             f"{self.item.sha256}  {self.item.remote_key}\n")
            self.assertIn("--download", command)
            self.assertIn("--one-way", command)
            return subprocess.CompletedProcess(command, 0)
        with mock.patch("subprocess.run", side_effect=inspect):
            self.provider(enabled=True).verify(self.batch, self.item)

    def test_dropbox_waits_for_commit_and_uses_independent_download_verification(self):
        provider = self.provider(kind="dropbox_rclone", enabled=True)
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            provider.upload(self.batch, self.item)
            provider.verify(self.batch, self.item)
        upload, verify = [call.args[0] for call in run.call_args_list]
        self.assertEqual(upload[upload.index("--dropbox-batch-mode") + 1], "sync")
        self.assertEqual(upload[upload.index("--dropbox-chunk-size") + 1], "8Mi")
        self.assertEqual(verify[:3], ["rclone", "checksum", "sha256"])
        self.assertIn("--download", verify)
        self.assertIn("--one-way", verify)
        self.assertEqual((self.batch.root / "files" / self.item.relative).read_bytes(), b"file contents")

    def test_dropbox_readback_failure_never_records_verified_or_removes_source(self):
        provider = self.provider(kind="dropbox_rclone", enabled=True)
        with History(self.base / "history.sqlite3") as history:
            history.enqueue(self.batch, provider.destination_id, 100)
            with mock.patch("subprocess.run", side_effect=[subprocess.CompletedProcess([], 0),
                                                           subprocess.CompletedProcess([], 1)]):
                result = BackupEngine(history).run_once(provider, now=100, connectivity=Connectivity.ONLINE)
            self.assertEqual(result, "retry")
            self.assertEqual(history.jobs()[0]["state"], "retry")
            self.assertEqual(history.jobs()[0]["error_code"], "rclone_command_failed")
        self.assertEqual((self.batch.root / "files" / self.item.relative).read_bytes(), b"file contents")

    def test_failure_timeout_and_missing_executable_have_sanitized_errors(self):
        for outcome, code, retryable in (
            (subprocess.CompletedProcess([], 1), "rclone_command_failed", True),
            (subprocess.CompletedProcess([], 2), "rclone_command_failed", False),
            (subprocess.CompletedProcess([], 5), "rclone_command_failed", True),
            (subprocess.CompletedProcess([], 7), "rclone_command_failed", False),
            (subprocess.TimeoutExpired("secret-url", 1), "rclone_timeout", True),
            (FileNotFoundError("secret-path"), "rclone_or_config_missing", False),
        ):
            with self.subTest(code=code):
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with mock.patch("subprocess.run", **kwargs), self.assertRaises(BackupError) as result:
                    self.provider(enabled=True).upload(self.batch, self.item)
                self.assertEqual(result.exception.code, code)
                self.assertEqual(result.exception.retryable, retryable)
                self.assertNotIn("secret", str(result.exception))

    def test_public_config_and_symlink_config_are_rejected_before_execution(self):
        self.config.chmod(0o644)
        for kind in ("koofr_webdav", "dropbox_rclone"):
            with mock.patch("subprocess.run") as run, self.assertRaisesRegex(BackupError, "insecure"):
                self.provider(kind=kind, enabled=True).upload(self.batch, self.item)
            run.assert_not_called()
        link = self.base / "link.conf"
        link.symlink_to(self.config)
        for kind in ("koofr_webdav", "dropbox_rclone"):
            with mock.patch("subprocess.run") as run, self.assertRaisesRegex(BackupError, "insecure"):
                RcloneProvider(kind, "test:backup", link, enabled=True).upload(self.batch, self.item)
            run.assert_not_called()

    def test_unsafe_remotes_and_coerced_enablement_rejected(self):
        for remote in (":local:/tmp", "-option:backup", "test:/absolute", "test:../escape",
                       "test:backup\nsecret", "test:back\\slash", "no-colon"):
            with self.subTest(remote=remote), self.assertRaises((ValueError, BackupError)):
                RcloneProvider("koofr_webdav", remote, self.config)
        with self.assertRaises(ValueError):
            self.provider(enabled="true")

    def test_environment_cannot_override_remote_or_enable_debug_or_remote_control(self):
        with mock.patch.dict(os.environ, {"RCLONE_CONFIG_TEST_TYPE": "drive", "RCLONE_DUMP": "auth",
                                          "RCLONE_RC": "true", "HTTPS_PROXY": "http://example.invalid"}), \
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.provider(enabled=True).upload(self.batch, self.item)
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("RCLONE_CONFIG_TEST_TYPE", environment)
        self.assertNotIn("RCLONE_DUMP", environment)
        self.assertNotIn("RCLONE_RC", environment)
        self.assertEqual(environment["HTTPS_PROXY"], "http://example.invalid")
