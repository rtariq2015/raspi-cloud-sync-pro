import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.accounts import (
    _auth_lock, change_dashboard_password, initialize_dashboard, read_auth, reset_dashboard_password,
)
from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.errors import BackupError


@unittest.skipUnless(importlib.util.find_spec("werkzeug"), "optional dashboard dependencies not installed")
class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        root = Path(cls.fixture.name)
        initialize_dashboard(root, password="test-owner-password")
        initialize_dashboard(root, username="viewer", role="viewer", password="test-viewer-password")
        cls.auth = (root / "auth.json").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "auth.json"
        self.path.write_bytes(self.auth)
        self.path.chmod(0o600)

    def test_private_cli_recovery_preserves_roles_and_unrelated_credentials(self):
        before = read_auth(self.root)
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch("getpass.getpass", side_effect=["new-viewer-password", "new-viewer-password"]) as prompt, \
                redirect_stdout(output), redirect_stderr(errors):
            result = main(["dashboard-reset-password", "--state", str(self.root), "--username", "viewer"])
        self.assertEqual(result, 0)
        self.assertEqual(prompt.call_count, 2)
        self.assertNotIn("new-viewer-password", output.getvalue() + errors.getvalue())
        after = read_auth(self.root)
        self.assertEqual(after["secret"], before["secret"])
        self.assertEqual(after["users"]["owner"], before["users"]["owner"])
        self.assertEqual(after["users"]["viewer"]["role"], "viewer")
        self.assertNotEqual(after["users"]["viewer"]["hash"], before["users"]["viewer"]["hash"])
        self.assertTrue(after["users"]["viewer"]["hash"].startswith("scrypt:"))
        self.assertNotIn("new-viewer-password", self.path.read_text())
        backups = list(self.root.glob("auth-backup-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.auth)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_failed_recovery_confirmation_and_unknown_user_leave_auth_unchanged(self):
        for values, user in ((["new-password-long", "not-the-same-password"], "owner"),
                             (["new-password-long", "new-password-long"], "missing")):
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch("getpass.getpass", side_effect=values), redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main(["dashboard-reset-password", "--state", str(self.root), "--username", user]), 2)
            self.assertNotIn("new-password-long", output.getvalue() + errors.getvalue())
            self.assertEqual(self.path.read_bytes(), self.auth)
        self.assertEqual(list(self.root.glob("auth-backup-*.json")), [])

    def test_failed_atomic_publication_retains_old_auth_and_private_backup(self):
        with mock.patch("raspi_cloud_sync_pro.accounts.os.replace", side_effect=OSError("simulated storage failure")):
            with self.assertRaises(OSError):
                reset_dashboard_password(self.root, username="owner", password="new-owner-password")
        self.assertEqual(self.path.read_bytes(), self.auth)
        self.assertEqual(list(self.root.glob("auth-*.tmp")), [])
        backups = list(self.root.glob("auth-backup-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.auth)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_failed_backup_directory_flush_prevents_auth_replacement(self):
        with mock.patch("raspi_cloud_sync_pro.accounts._fsync_directory", side_effect=OSError("simulated flush failure")), \
                mock.patch("raspi_cloud_sync_pro.accounts.os.replace") as replace:
            with self.assertRaises(OSError):
                reset_dashboard_password(self.root, username="owner", password="new-owner-password")
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.auth)
        self.assertEqual(list(self.root.glob("auth-*.tmp")), [])

    def test_exclusive_lock_and_symlink_lock_block_changes(self):
        with _auth_lock(self.root):
            with self.assertRaisesRegex(BackupError, "account_update_busy"):
                reset_dashboard_password(self.root, username="owner", password="new-owner-password")
        self.assertEqual(self.path.read_bytes(), self.auth)
        lock = self.root / "auth.lock"
        lock.unlink()
        outside = self.root / "outside"
        outside.write_text("retained")
        lock.symlink_to(outside)
        with self.assertRaises(OSError):
            reset_dashboard_password(self.root, username="owner", password="new-owner-password")
        self.assertEqual(outside.read_text(), "retained")
        self.assertEqual(self.path.read_bytes(), self.auth)

    def test_bad_current_password_or_confirmation_does_not_modify_auth(self):
        for current, password, confirm in (("wrong-current-password", "new-owner-password", "new-owner-password"),
                                           ("test-owner-password", "new-owner-password", "different-password"),
                                           ("test-owner-password", "tiny", "tiny")):
            with self.assertRaises((BackupError, ValueError)):
                change_dashboard_password(self.root, "owner", current, password, confirm)
            self.assertEqual(self.path.read_bytes(), self.auth)
        self.assertEqual(list(self.root.glob("auth-backup-*.json")), [])

    def test_symlink_auth_is_refused_and_no_account_is_created_by_recovery(self):
        target = self.root / "copy.json"
        target.write_bytes(self.auth)
        target.chmod(0o600)
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            reset_dashboard_password(self.root, username="owner", password="new-owner-password")
        self.assertEqual(target.read_bytes(), self.auth)
        self.assertEqual(set(json.loads(target.read_text())["users"]), {"owner", "viewer"})
