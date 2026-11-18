"""Startup journal output is fixed and secret-free; only disposable locks are used."""
import ast
from contextlib import ExitStack, redirect_stdout
import io
import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from raspi_cloud_sync_pro import mtp_startup as startup
from raspi_cloud_sync_pro.errors import BackupError


class StartupReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lock = Path(self.temp.name) / 'lock'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(startup.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(startup, '_protected'))
        original_open, original_fstat = os.open, os.fstat

        def open_lock(path, flags, mode):
            self.assertEqual(path, '/run/lock/raspi-cloud-mtp-startup.lock')
            return original_open(self.lock, flags, mode)

        def root_lock(fd):
            info = original_fstat(fd)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink)

        self.stack.enter_context(patch.object(startup.os, 'open', side_effect=open_lock))
        self.stack.enter_context(patch.object(startup.os, 'fstat', side_effect=root_lock))
        self.backend = Mock()
        self.backend.approved = dict(source_uid=1234, source_gid=1234,
                                     max_power_ma=100, udc='3f980000.usb')
        self.adapter = self.stack.enter_context(patch.object(startup, 'LinuxStartupIO', return_value=self.backend))
        self.lifecycle = self.stack.enter_context(patch.object(startup, 'MtpStartup'))

    def run_check(self, error=None):
        self.backend.preflight.side_effect = error
        output = io.StringIO()
        with redirect_stdout(output):
            result = startup.main(['check'])
        self.lifecycle.assert_not_called()
        return result, output.getvalue()

    def test_success_still_runs_the_same_readonly_preflight(self):
        self.assertEqual(self.run_check(), (0, 'MTP startup check passed\n'))
        self.backend.preflight.assert_called_once_with(startup.StartupSettings(1234, 1234, 100))

    def test_actual_namespace_refusal_is_reported_without_exception_text(self):
        error = startup.StartupError('mtp_startup_host_namespace_required')
        error.args = ('private/path token=never-print',)
        self.assertEqual(self.run_check(error),
                         (1, 'MTP startup refused: mtp_startup_host_namespace_required\n'))

    def test_unknown_codes_are_not_a_journal_data_channel(self):
        for code in ('private/path token=never-print', 'mtp_startup_private_token', '', None):
            with self.subTest(code=code):
                self.assertEqual(self.run_check(startup.StartupError(code)),
                                 (1, 'MTP startup refused: mtp_startup_refused\n'))

    def test_other_errors_do_not_expose_paths_credentials_or_subprocess_output(self):
        cases = [(BackupError('private_token'), 'mtp_startup_dependency_refused'),
                 (PermissionError(13, 'private_token', '/private/path'), 'mtp_startup_os_error'),
                 (ValueError('private_token'), 'mtp_startup_input_invalid'),
                 (TypeError('private_token'), 'mtp_startup_input_invalid'),
                 (KeyError('private_token'), 'mtp_startup_input_invalid'),
                 (subprocess.CalledProcessError(1, ['private_token'], output='private/path'),
                  'mtp_startup_command_failed')]
        for error, code in cases:
            with self.subTest(kind=type(error).__name__):
                self.assertEqual(self.run_check(error), (1, 'MTP startup refused: ' + code + '\n'))

    def test_busy_lock_refuses_before_loading_any_configuration(self):
        with patch.object(startup.fcntl, 'flock', side_effect=BlockingIOError(11, 'private/path')):
            self.assertEqual(self.run_check(), (1, 'MTP startup refused: mtp_startup_lock_busy\n'))
        self.adapter.assert_not_called()

    def test_unprotected_lock_refuses_before_loading_any_configuration(self):
        with patch.object(startup.os, 'fstat', return_value=SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644, st_uid=0, st_nlink=1)):
            self.assertEqual(self.run_check(), (1, 'MTP startup refused: mtp_startup_lock_unverified\n'))
        self.adapter.assert_not_called()

    def test_nonroot_refuses_before_opening_runtime_or_configuration(self):
        with patch.object(startup.os, 'geteuid', return_value=1234), patch.object(startup.os, 'open') as opened:
            self.assertEqual(self.run_check(), (1, 'MTP startup refused: mtp_startup_root_required\n'))
        opened.assert_not_called()
        self.adapter.assert_not_called()

    def test_all_literal_startup_guard_codes_have_reviewed_public_reporting(self):
        tree = ast.parse(Path(startup.__file__).read_text())
        codes = {node.args[1].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == 'require' and len(node.args) > 1
                 and isinstance(node.args[1], ast.Constant)}
        self.assertTrue(codes <= startup._PUBLIC_FAILURE_CODES)


if __name__ == '__main__':
    unittest.main()
