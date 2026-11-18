"""Real private owner records/frames; explicitly mocked root/process boundaries.

The synthetic source runs as the ordinary test UID. No native worker, network,
service, root gate or cloud verification is produced by these tests.
"""

import configparser
import copy
import errno
import fcntl
import io
import json
import os
import shlex
import stat
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import cli
from raspi_cloud_sync_pro import google_broker as preparation
from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_control_runner as runner
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_setup as setup
from raspi_cloud_sync_pro import google_test_broker as broker
from raspi_cloud_sync_pro.errors import BackupError
import test_google_control as fixtures

PRIVATE = "PRIVATE-runner-path-token-folder-owner-exception"
RUNNER_ERROR = "Google control runner unavailable; no private details were printed.\n"
CONTROL_ERROR = "Private Google control operation unavailable; no private details were printed.\n"


@unittest.skipIf(os.geteuid() == 0, "Private source files belong to an ordinary service UID")
class GoogleControlRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GoogleControlTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.state
        self.settings = broker.GoogleTestBrokerSettings(enabled=True, source_state=self.state,
            public_origin=fixtures.ORIGIN, dashboard_user="synthetic-source")
        self.account = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        self.child = SimpleNamespace(wait=lambda **kwargs: 0, poll=lambda: 0)
        self.spawned = []
        self.closed_outputs = []
        self.child_report = None

    def snapshot(self):
        result = {}
        for path in [self.state] + sorted(self.state.rglob("*")):
            info = path.lstat()
            result[str(path.relative_to(self.state))] = (
                stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
                destination._file_identity(info), path.read_bytes() if path.is_file() else None)
        return result

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def probe(self, expected, pending=False, *, now=1001):
        before = self.snapshot()
        incoming, outgoing = preparation._private_pipe()
        try:
            report = control.export_google_control_pending(self.state, fixtures.ORIGIN, outgoing, now=now)
            self.assert_closed(outgoing)
            outgoing = None
            value = destination._read_frame(incoming, time.monotonic() + 1)
            self.assertEqual(value, {"schema_version": 1, "provider": "google_drive",
                "control_state": expected, "pending": pending, "checked_at": now})
            self.assertEqual(os.read(incoming, 1), b"")
            self.assertEqual(before, self.snapshot())
            self.fixture.assert_safe(value)
            self.fixture.assert_safe(report)
            self.assertIs(report["current_worker_state_checked"], False)
            self.assertIs(report["account_verified"], False)
            self.assertIs(report["automatic_deletion"], False)
            return value
        finally:
            os.close(incoming)
            if outgoing is not None:
                try:
                    os.close(outgoing)
                except OSError:
                    pass

    def spawn_source(self, settings, account, arguments, inherited):
        # Explicit ordinary-source/process boundary substitute. The producer,
        # frame, EOF, parser and both endpoint ownerships remain real.
        self.assertEqual(settings, self.settings)
        self.assertIs(account, self.account)
        self.assertEqual(arguments[:-1], ["google-control-pending", "--state", str(self.state),
                                        "--public-origin", fixtures.ORIGIN, "--output-fd"])
        self.assertEqual(inherited, (int(arguments[-1]),))
        self.spawned.append(tuple(arguments))
        duplicate = os.dup(inherited[0])
        self.closed_outputs.append(duplicate)
        self.child_report = control.export_google_control_pending(self.state, fixtures.ORIGIN, duplicate)
        self.assert_closed(duplicate)
        return self.child

    def run_actual_pending(self, *, now=1001, broker_result=None, broker_effect=None):
        before = self.snapshot()
        result = broker._result("applied", "google_test_broker_control_applied") if broker_result is None else broker_result
        with mock.patch.object(broker, "_gates") as gates, \
                mock.patch.object(preparation, "_service_identity", return_value=self.account) as identity, \
                mock.patch.object(preparation, "_spawn", side_effect=self.spawn_source), \
                mock.patch.object(runner.time, "time", return_value=now), \
                mock.patch.object(broker, "run_google_test_broker", return_value=result, side_effect=broker_effect) as base:
            actual = runner.run_google_control_runner(self.settings)
        self.assertEqual(before, self.snapshot())
        gates.assert_called_once_with(self.settings)
        identity.assert_called_once_with(self.settings.dashboard_user)
        self.fixture.assert_safe(actual)
        return actual, base

    def test_real_producer_no_request_and_all_pending_completed_transitions(self):
        self.probe("not_requested")
        self.assertFalse(self.fixture.workflow.exists())
        self.fixture.request()
        self.probe("test_requested", True)
        self.fixture.record()
        self.probe("test_verified", now=1003)
        self.fixture.request("enable", now=1003)
        self.probe("enable_requested", True, now=1004)
        self.fixture.record(self.fixture.receipt(completed_at=1004), now=1005)
        self.probe("enable_recorded", now=1006)
        self.fixture.request("disable", now=1006)
        self.probe("disable_requested", True, now=1007)
        self.fixture.record(self.fixture.receipt(completed_at=1007), now=1008)
        self.probe("disable_recorded", now=1009)
        self.assertEqual(len(list(self.fixture.workflow.glob("*.request.json"))), 3)
        self.assertEqual(len(list(self.fixture.workflow.glob("*.receipt.json"))), 3)

    def test_failed_and_expired_intents_are_not_pending(self):
        self.fixture.request()
        self.probe("expired", now=1600)
        self.fixture.record(self.fixture.receipt(outcome="failed"), now=1002)
        self.probe("test_failed", now=1003)
        self.fixture.request(now=1004)
        self.fixture.record(self.fixture.receipt(completed_at=1005), now=1006)
        self.probe("test_expired", now=1605)
        self.fixture.request("enable", now=1007)
        self.fixture.record(self.fixture.receipt(outcome="failed", completed_at=1008), now=1009)
        self.probe("enable_failed", now=1010)
        self.fixture.request("disable", now=1011)
        self.fixture.record(self.fixture.receipt(outcome="failed", completed_at=1012), now=1013)
        self.probe("disable_failed", now=1014)

    def test_stale_owner_and_changed_source_are_never_pending(self):
        self.fixture.request()
        original = copy.deepcopy(self.fixture.auth)
        self.fixture.auth["users"]["owner"]["role"] = "viewer"
        self.fixture.write("auth.json", self.fixture.auth)
        self.probe("stale_binding")
        self.fixture.write("auth.json", original)
        self.fixture.grant["refresh_token"] = PRIVATE
        self.fixture.write("oauth-google_drive.json", self.fixture.grant)
        self.probe("stale_binding")

    def test_missing_auth_control_and_preparation_locks_are_not_created(self):
        self.fixture.request()
        for path in (self.state / "auth.lock", self.fixture.workflow / "workflow.lock",
                     self.state / setup.WORKFLOW_DIRECTORY / "workflow.lock"):
            saved = path.read_bytes()
            path.unlink()
            self.probe("unavailable")
            self.assertFalse(path.exists())
            path.write_bytes(saved)
            path.chmod(0o600)

    def test_exclusive_busy_locks_defer_without_mutation_and_shared_locks_coexist(self):
        self.fixture.request()
        paths = (self.state / "auth.lock", self.fixture.workflow / "workflow.lock",
                 self.state / setup.WORKFLOW_DIRECTORY / "workflow.lock")
        for path in paths:
            fd = os.open(path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.probe("unavailable")
            finally:
                os.close(fd)
        held = []
        try:
            for path in paths:
                fd = os.open(path, os.O_RDONLY)
                held.append(fd)
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            original_open = os.open
            with mock.patch.object(control.os, "open", wraps=original_open) as opened:
                self.probe("test_requested", True)
            forbidden = os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_RDWR
            self.assertTrue(opened.call_args_list)
            self.assertTrue(all(call.args[1] & forbidden == 0 for call in opened.call_args_list))
        finally:
            for fd in held:
                os.close(fd)

    def test_invalid_private_pointer_or_lock_is_permanent_metadata_state(self):
        self.fixture.request()
        pointer = self.fixture.workflow / "current.json"
        original = pointer.read_bytes()
        pointer.write_bytes(b'{"schema_version":1,"request_id":"../PRIVATE"}')
        self.probe("invalid")
        pointer.write_bytes(original)
        lock = self.fixture.workflow / "workflow.lock"
        lock.chmod(0o644)
        self.probe("invalid")
        lock.chmod(0o600)
        alias = self.state / "retained-lock-alias"
        os.link(lock, alias)
        self.probe("invalid")
        alias.unlink()

    def test_disable_pending_requires_owner_but_no_cloud_preparation_readiness(self):
        self.fixture.request("disable")
        (self.state / "oauth-clients.json").unlink()
        (self.state / "oauth-google_drive.json").unlink()
        (self.state / setup.WORKFLOW_DIRECTORY / "workflow.lock").unlink()
        self.probe("disable_requested", True)
        self.assertFalse((self.state / setup.WORKFLOW_DIRECTORY / "workflow.lock").exists())
        self.fixture.auth["users"]["owner"]["hash"] = PRIVATE
        self.fixture.write("auth.json", self.fixture.auth)
        self.probe("stale_binding")

    def test_readonly_pending_does_not_change_legacy_status_lock_default(self):
        self.fixture.request()
        lock = self.state / setup.WORKFLOW_DIRECTORY / "workflow.lock"
        lock.unlink()
        self.probe("unavailable")
        self.assertFalse(lock.exists())
        status = control.google_control_status(self.state, fixtures.ORIGIN, now=1001)
        self.assertEqual(status["control_state"], "test_requested")
        self.assertTrue(lock.exists())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_real_pending_frame_calls_base_once_and_completed_work_is_not_repeated(self):
        self.fixture.request()
        result, base = self.run_actual_pending(broker_result=broker._result("tested", "google_test_broker_test_verified_not_enabled"))
        self.assertEqual(result["status"], "tested")
        base.assert_called_once_with(self.settings)
        self.fixture.record()
        result, base = self.run_actual_pending(now=1003)
        self.assertEqual(result, runner._result("idle", "google_control_runner_no_pending_request"))
        base.assert_not_called()
        self.fixture.request("enable", now=1003)
        result, base = self.run_actual_pending(now=1004)
        self.assertEqual(result["status"], "applied")
        base.assert_called_once_with(self.settings)
        self.fixture.record(self.fixture.receipt(completed_at=1004), now=1005)
        result, base = self.run_actual_pending(now=1006)
        self.assertEqual(result["status"], "idle")
        base.assert_not_called()
        self.fixture.request("disable", now=1006)
        result, base = self.run_actual_pending(now=1007)
        self.assertEqual(result["status"], "applied")
        base.assert_called_once_with(self.settings)
        self.fixture.record(self.fixture.receipt(completed_at=1007), now=1008)
        result, base = self.run_actual_pending(now=1009)
        self.assertEqual(result["status"], "idle")
        base.assert_not_called()
        self.assertEqual(len(self.spawned), 6)

    def test_runner_real_no_request_expired_stale_invalid_and_missing_lock_states(self):
        result, base = self.run_actual_pending()
        self.assertEqual(result["status"], "idle")
        base.assert_not_called()
        self.fixture.request()
        result, base = self.run_actual_pending(now=1600)
        self.assertEqual(result["status"], "idle")
        base.assert_not_called()
        self.fixture.auth["users"]["owner"]["role"] = "viewer"
        self.fixture.write("auth.json", self.fixture.auth)
        result, base = self.run_actual_pending()
        self.assertEqual(result["status"], "idle")
        base.assert_not_called()
        (self.fixture.workflow / "current.json").write_bytes(b'{"invalid":true}')
        result, base = self.run_actual_pending()
        self.assertEqual(result, runner._result("failed", "google_control_runner_source_invalid"))
        base.assert_not_called()
        (self.state / "auth.lock").unlink()
        result, base = self.run_actual_pending()
        self.assertEqual(result, runner._result("deferred", "google_control_runner_source_unavailable"))
        base.assert_not_called()
        self.assertFalse((self.state / "auth.lock").exists())

    def test_runner_busy_lock_is_deferred_and_no_broker_is_invoked(self):
        self.fixture.request()
        fd = os.open(self.state / setup.WORKFLOW_DIRECTORY / "workflow.lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, base = self.run_actual_pending()
        finally:
            os.close(fd)
        self.assertEqual(result, runner._result("deferred", "google_control_runner_source_unavailable"))
        base.assert_not_called()

    def test_child_nonzero_or_no_frame_is_unavailable_and_all_outputs_close(self):
        self.fixture.request()
        self.child.wait = lambda **kwargs: 2
        result, base = self.run_actual_pending()
        self.assertEqual(result["status"], "deferred")
        base.assert_not_called()
        for fd in self.closed_outputs:
            self.assert_closed(fd)
        def no_frame(*args):
            return self.child
        with mock.patch.object(broker, "_gates"), \
                mock.patch.object(preparation, "_service_identity", return_value=self.account), \
                mock.patch.object(preparation, "_spawn", side_effect=no_frame), \
                mock.patch.object(broker, "run_google_test_broker") as base:
            result = runner.run_google_control_runner(self.settings)
        self.assertEqual(result["status"], "deferred")
        base.assert_not_called()

    def test_configuration_disabled_or_gate_failure_never_checks_source(self):
        with mock.patch.object(broker, "_gates") as gates, mock.patch.object(runner, "_pending") as pending, \
                mock.patch.object(preparation, "_service_identity") as identities, \
                mock.patch.object(broker, "run_google_test_broker") as base:
            self.assertEqual(runner.run_google_control_runner(broker.GoogleTestBrokerSettings())["status"], "disabled")
            self.assertEqual(runner.run_google_control_runner({})["status"], "failed")
        for operation in (gates, pending, identities, base):
            operation.assert_not_called()
        with mock.patch.object(broker, "_gates", side_effect=RuntimeError(PRIVATE)), \
                mock.patch.object(runner, "_pending") as pending:
            result = runner.run_google_control_runner(self.settings)
        self.assertEqual(result, runner._result("failed", "google_control_runner_unavailable"))
        self.assertNotIn(PRIVATE, json.dumps(result))
        pending.assert_not_called()

    def test_retryable_broker_errors_and_cleanup_unconfirmed_have_distinct_outcomes(self):
        self.fixture.request()
        retryable = ("source_unavailable_before_worker", "wifi_unavailable_before_worker")
        for suffix in retryable + ("wifi_unavailable", "source_refused", "unavailable", "cleanup_unconfirmed", "canary_invalid"):
            result, base = self.run_actual_pending(broker_result=broker._result("failed", "google_test_broker_" + suffix))
            self.assertEqual(result["status"], "deferred" if suffix in retryable else "failed")
            base.assert_called_once_with(self.settings)
            if suffix == "cleanup_unconfirmed":
                self.assertEqual(result["code"], "google_test_broker_cleanup_unconfirmed")
        for error in (RuntimeError, MemoryError, ValueError):
            result, base = self.run_actual_pending(broker_effect=error(PRIVATE))
            self.assertEqual(result, runner._result("failed", "google_control_runner_unavailable"))
            self.assertNotIn(PRIVATE, json.dumps(result))
            base.assert_called_once_with(self.settings)

    def test_pending_reader_rejects_malformed_stale_oversize_and_nonfinal_frames(self):
        valid = {"schema_version": 1, "provider": "google_drive", "control_state": "test_requested",
                 "pending": True, "checked_at": 1001}
        cases = [{**valid, "pending": False}, {**valid, "pending": 1}, {**valid, "schema_version": True},
                 {**valid, "control_state": "not-a-state"}, {**valid, "checked_at": 971},
                 {**valid, "checked_at": 1002}, {**valid, "checked_at": float("nan")},
                 {**valid, "extra": PRIVATE}, []]
        packets = []
        for value in cases:
            raw = json.dumps(value).encode()
            packets.append(len(raw).to_bytes(4, "big") + raw)
        raw = destination._canonical(valid)
        packets.extend((len(raw).to_bytes(4, "big") + raw + b"x", (1025).to_bytes(4, "big"),
                        b'\x00\x00\x00\x10{"same":1,"same":2}'))
        for packet in packets:
            def raw_spawn(settings, account, arguments, inherited):
                duplicate = os.dup(inherited[0])
                try:
                    os.write(duplicate, packet)
                finally:
                    os.close(duplicate)
                return self.child
            with self.subTest(packet=packet[:4]), mock.patch.object(broker, "_gates"), \
                    mock.patch.object(preparation, "_service_identity", return_value=self.account), \
                    mock.patch.object(preparation, "_spawn", side_effect=raw_spawn), \
                    mock.patch.object(runner.time, "time", return_value=1001), \
                    mock.patch.object(broker, "run_google_test_broker") as base:
                result = runner.run_google_control_runner(self.settings)
            self.assertEqual(result, runner._result("deferred", "google_control_runner_source_unavailable"))
            base.assert_not_called()
            self.assertNotIn(PRIVATE, json.dumps(result))

    def test_pending_freshness_is_checked_after_the_frame_and_child_exit(self):
        self.fixture.request()
        clock = [1001]
        original = preparation._receive
        def receive(*args):
            value = original(*args)
            clock[0] = 1031
            return value
        with mock.patch.object(preparation, "_spawn", side_effect=self.spawn_source), \
                mock.patch.object(preparation, "_receive", side_effect=receive), \
                mock.patch.object(runner.time, "time", side_effect=lambda: clock[0]):
            with self.assertRaises(ValueError):
                runner._pending(self.settings, self.account)


class GoogleControlRunnerCliTests(unittest.TestCase):
    @staticmethod
    def invoke(arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def test_fixed_statuses_and_cleanup_failure_have_distinct_cli_exit_codes(self):
        cases = ((runner._result("disabled", "google_control_runner_disabled"), 0),
                 (runner._result("idle", "google_control_runner_no_pending_request"), 0),
                 (runner._result("deferred", "google_control_runner_source_unavailable"), 75),
                 (runner._result("deferred", "google_control_runner_request_deferred"), 75),
                 (runner._result("failed", "google_control_runner_source_invalid"), 2),
                 (broker._result("failed", "google_test_broker_cleanup_unconfirmed"), 2),
                 (broker._result("applied", "google_test_broker_control_applied"), 0),
                 (broker._result("tested", "google_test_broker_test_verified_not_enabled"), 0))
        settings = broker.GoogleTestBrokerSettings()
        for report, expected in cases:
            with self.subTest(report=report), \
                    mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=settings) as load, \
                    mock.patch.object(runner, "run_google_control_runner", return_value=report) as run:
                code, output, errors = self.invoke(["google-control-runner", "--config", "/protected/config.json"])
            self.assertEqual((code, json.loads(output), errors), (expected, report, ""))
            load.assert_called_once_with(Path("/protected/config.json"))
            run.assert_called_once_with(settings)

    def test_runner_cli_sanitizes_parser_loader_runtime_and_forged_reports(self):
        for arguments in ([], ["--config"], ["--config", PRIVATE, "--request-id", PRIVATE],
                          ["--config", PRIVATE, "--enable"], ["--config", PRIVATE, "--root-gate-fd", "3"]):
            self.assertEqual(self.invoke(["google-control-runner"] + arguments), (2, "", RUNNER_ERROR))
        for error in (RuntimeError, MemoryError, OSError, ValueError, BackupError):
            for target in ("loader", "runner"):
                with mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=broker.GoogleTestBrokerSettings()) as load, \
                        mock.patch.object(runner, "run_google_control_runner") as run:
                    (load if target == "loader" else run).side_effect = error(PRIVATE)
                    self.assertEqual(self.invoke(["google-control-runner", "--config", "/protected/" + PRIVATE]),
                                     (2, "", RUNNER_ERROR))
        for report in ({**runner._result("idle", "google_control_runner_no_pending_request"), "private": PRIVATE},
                       {**runner._result("idle", "google_control_runner_no_pending_request"), "uploads_enabled": True},
                       runner._result("deferred", "google_test_broker_cleanup_unconfirmed"),
                       runner._result("failed", PRIVATE), None):
            with mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=broker.GoogleTestBrokerSettings()), \
                    mock.patch.object(runner, "run_google_control_runner", return_value=report):
                self.assertEqual(self.invoke(["google-control-runner", "--config", "/protected/config"]),
                                 (2, "", RUNNER_ERROR))

    def test_disabled_real_runner_cli_never_probes_source_or_base_broker(self):
        with mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=broker.GoogleTestBrokerSettings()), \
                mock.patch.object(broker, "_gates") as gates, mock.patch.object(runner, "_pending") as source, \
                mock.patch.object(broker, "run_google_test_broker") as base:
            code, output, errors = self.invoke(["google-control-runner", "--config", "/protected/config"])
        self.assertEqual((code, json.loads(output), errors),
                         (0, runner._result("disabled", "google_control_runner_disabled"), ""))
        for operation in (gates, source, base):
            operation.assert_not_called()

    def test_pending_cli_generic_failures_are_fixed_and_accepted_output_is_closed(self):
        for error in (RuntimeError, MemoryError):
            incoming, outgoing = preparation._private_pipe()
            def fail(state, origin, output_fd):
                with destination._channel(output_fd, writing=True):
                    raise error(PRIVATE)
            try:
                with self.subTest(error=error), mock.patch.object(control, "export_google_control_pending", side_effect=fail):
                    self.assertEqual(self.invoke(["google-control-pending", "--state", "/private/" + PRIVATE,
                        "--public-origin", fixtures.ORIGIN, "--output-fd", str(outgoing)]), (2, "", CONTROL_ERROR))
                with self.assertRaises(OSError):
                    os.fstat(outgoing)
                outgoing = None
                self.assertEqual(os.read(incoming, 1), b"")
            finally:
                os.close(incoming)
                if outgoing is not None:
                    try:
                        os.close(outgoing)
                    except OSError:
                        pass

    def test_private_pending_parser_errors_never_echo_operator_values(self):
        for arguments in ([], ["--state", PRIVATE], ["--state", PRIVATE, "--public-origin", fixtures.ORIGIN,
                         "--output-fd", PRIVATE], ["--state", PRIVATE, "--enable"]):
            self.assertEqual(self.invoke(["google-control-pending"] + arguments), (2, "", CONTROL_ERROR))


@unittest.skipIf(os.geteuid() == 0, "Synthetic CLI source belongs to an ordinary UID")
class GoogleControlPendingCliFrameTests(unittest.TestCase):
    def test_actual_private_frame_is_separate_from_public_stdout_and_source_is_unchanged(self):
        fixture = fixtures.GoogleControlTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.request()
        before = {path.relative_to(fixture.state): (destination._file_identity(path.stat()),
                  path.read_bytes() if path.is_file() else None)
                  for path in [fixture.state] + list(fixture.state.rglob("*"))}
        incoming, outgoing = preparation._private_pipe()
        try:
            with mock.patch.object(runner.time, "time", return_value=1001):
                code, output, errors = GoogleControlRunnerCliTests.invoke(["google-control-pending", "--state",
                    str(fixture.state), "--public-origin", fixtures.ORIGIN, "--output-fd", str(outgoing)])
            with self.assertRaises(OSError):
                os.fstat(outgoing)
            outgoing = None
            frame = destination._read_frame(incoming, time.monotonic() + 1)
            self.assertEqual(frame["control_state"], "test_requested")
            self.assertIs(frame["pending"], True)
            self.assertEqual(set(frame), {"schema_version", "provider", "control_state", "pending", "checked_at"})
            self.assertEqual(code, 0)
            self.assertEqual(errors, "")
            public = json.loads(output)
            self.assertIs(public["pending_exported"], True)
            self.assertNotIn("control_state", public)
            fixture.assert_safe(public)
            fixture.assert_safe(frame)
            self.assertNotIn("test_requested", output)
            self.assertEqual(before, {path.relative_to(fixture.state): (destination._file_identity(path.stat()),
                path.read_bytes() if path.is_file() else None)
                for path in [fixture.state] + list(fixture.state.rglob("*"))})
        finally:
            os.close(incoming)
            if outgoing is not None:
                try:
                    os.close(outgoing)
                except OSError:
                    pass


class GoogleControlRunnerTemplateTests(unittest.TestCase):
    @staticmethod
    def template(name):
        path = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / name
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read_string(path.read_text())
        return parser

    def test_only_normal_exit75_forces_retry_and_oneshot_can_be_triggered_again(self):
        template = self.template("raspi-cloud-google-control.service.example")
        service = template["Service"]
        self.assertEqual(service["Type"], "oneshot")
        self.assertIn(service.get("RemainAfterExit", "no"), ("no", "false"))
        self.assertEqual(service["Restart"], "no")
        self.assertEqual(set(service["RestartForceExitStatus"].split()), {"75"})
        self.assertNotIn("75", service.get("SuccessExitStatus", "").split())
        self.assertNotIn("75", service.get("RestartPreventExitStatus", "").split())
        self.assertEqual(service["RestartSec"], "30")
        self.assertNotIn("User", service)
        self.assertNotIn("Group", service)
        self.assertEqual(service["KillMode"], "control-group")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["AmbientCapabilities"], "")
        arguments = shlex.split(service["ExecStart"])
        self.assertEqual(arguments, ["/opt/raspi-cloud-sync-pro/releases/0.1.0.dev17/venv/bin/python3",
            "-I", "-B", "-m", "raspi_cloud_sync_pro", "google-control-runner", "--config",
            "/etc/raspi-cloud-sync-pro/google-test-broker.json"])
        self.assertEqual(template["Install"]["WantedBy"], "multi-user.target")

    def test_atomic_control_pointer_watch_has_no_existence_trigger_loop(self):
        template = self.template("raspi-cloud-google-control.path.example")
        path = template["Path"]
        self.assertEqual(set(path), {"PathChanged", "Unit"})
        self.assertEqual(path["PathChanged"], "/var/lib/raspi-cloud-sync-pro/dashboard/google-control/current.json")
        self.assertEqual(path["Unit"], "raspi-cloud-google-control.service")
        self.assertEqual(template["Install"]["WantedBy"], "multi-user.target")
