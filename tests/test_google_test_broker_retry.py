"""Retry classification using actual private Source frames and injected PID1.

No privileged/native operation or cloud request is made. The existing broker
fixture supplies explicit source/PID1/confinement substitutes for later phases.
"""

import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import google_broker as preparation
from raspi_cloud_sync_pro import google_control_runner as runner
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_test_broker as broker
import test_google_test_broker as fixtures


class GoogleTestBrokerRetryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GoogleTestBrokerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.child = SimpleNamespace(wait=lambda **kwargs: 2, poll=lambda: 0)

    def runner_result(self, result):
        pending = {"schema_version": 1, "provider": "google_drive", "control_state": "test_requested",
                   "pending": True, "checked_at": time.time()}
        with mock.patch.object(broker, "_gates"), \
                mock.patch.object(preparation, "_service_identity", return_value=self.fixture.source), \
                mock.patch.object(runner, "_pending", return_value=pending), \
                mock.patch.object(broker, "run_google_test_broker", return_value=result) as base:
            actual = runner.run_google_control_runner(self.fixture.settings)
        base.assert_called_once_with(self.fixture.settings)
        self.fixture.safe(actual)
        return actual

    def initial(self, source, network):
        manager = mock.Mock()
        with mock.patch.object(broker, "_current_request", return_value=fixtures.REQUEST), \
                mock.patch.object(broker, "_network", side_effect=network) as queried, \
                mock.patch.object(broker, "_runtime") as runtime:
            with self.assertRaises(broker.GoogleTestBrokerError) as error:
                broker._orchestrate(self.fixture.settings, self.fixture.source, self.fixture.backup, 99,
                                    time.monotonic() + 3, manager, source)
        runtime.assert_not_called()
        manager.start.assert_not_called()
        self.assertEqual(list(self.fixture.runtime.iterdir()), [])
        return error.exception.code, queried

    def test_actual_source_child_exit2_without_frame_is_retryable_before_runtime(self):
        source = broker._Source(self.fixture.settings, self.fixture.source)
        def unavailable(settings, account, arguments, inherited):
            self.assertEqual(arguments[0], "google-control-export")
            self.assertEqual(arguments[arguments.index("--request-id") + 1], fixtures.REQUEST)
            self.assertEqual(inherited, (int(arguments[-1]),))
            return self.child
        with mock.patch.object(preparation, "_spawn", side_effect=unavailable):
            code, network = self.initial(source, AssertionError("No initial Wi-Fi query"))
        self.assertEqual(code, "google_test_broker_source_unavailable_before_worker")
        network.assert_not_called()
        report = self.runner_result(broker._result("failed", code))
        self.assertEqual(report, runner._result("deferred", "google_control_runner_request_deferred"))

    def test_actual_source_nonzero_with_valid_frame_is_distinct_from_bad_request(self):
        source = broker._Source(self.fixture.settings, self.fixture.source)
        outputs = []
        def exporter(settings, account, arguments, inherited):
            duplicate = os.dup(inherited[0])
            outputs.append(duplicate)
            with destination._channel(duplicate, writing=True) as channel:
                destination._write_frame(channel, destination._canonical(self.fixture.value), time.monotonic() + 1)
            return self.child
        with mock.patch.object(preparation, "_spawn", side_effect=exporter):
            code, network = self.initial(source, AssertionError("No Wi-Fi query"))
        self.assertEqual(code, "google_test_broker_source_unavailable_before_worker")
        network.assert_not_called()
        for fd in outputs:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.child.wait = lambda **kwargs: 0
        original = self.fixture.value
        self.fixture.value = {**original, "extra": fixtures.PRIVATE}
        with mock.patch.object(preparation, "_spawn", side_effect=exporter):
            code, network = self.initial(source, AssertionError("Invalid request must not query Wi-Fi"))
        self.assertEqual(code, "google_test_broker_request_invalid")
        network.assert_not_called()
        self.assertEqual(self.runner_result(broker._result("failed", code))["status"], "failed")

    def test_only_initial_wifi_availability_is_retryable_before_runtime(self):
        source = fixtures.FakeSource(self.fixture.value, self.fixture.events)
        failure = broker.GoogleTestBrokerError("google_test_broker_wifi_unavailable")
        code, network = self.initial(source, failure)
        self.assertEqual(code, "google_test_broker_wifi_unavailable_before_worker")
        self.assertEqual(source.exports, 1)
        network.assert_called_once_with(self.fixture.settings, mock.ANY)
        self.assertEqual(self.runner_result(broker._result("failed", code))["status"], "deferred")
        for other in ("google_test_broker_installation_unsafe", "google_test_broker_request_invalid"):
            code, _ = self.initial(source, broker.GoogleTestBrokerError(other))
            self.assertEqual(code, other)
            self.assertEqual(self.runner_result(broker._result("failed", code))["status"], "failed")

    def test_postrelease_wifi_failure_and_failed_recording_remain_terminal(self):
        network = {"interface_index": 7, "profile": fixtures.UUID}
        result, manager, source = self.fixture.run_protocol(
            networks=[network, network, broker.GoogleTestBrokerError("google_test_broker_wifi_unavailable")],
            source_options={"record_failure": True})
        self.assertEqual(result["code"], "google_test_broker_wifi_unavailable")
        self.assertTrue(manager.released)
        self.assertEqual(source.recorded, [])
        self.assertIn("source_record", self.fixture.events)
        self.assertEqual(len(manager.stopped), 1)
        self.assertEqual(self.runner_result(result), result)

    def test_later_source_refusal_and_failed_recording_remain_terminal(self):
        for fail_at in (2, 3):
            with self.subTest(source_export=fail_at):
                manager = fixtures.FakeManager(self.fixture.value, self.fixture.events)
                source = fixtures.FakeSource(self.fixture.value, self.fixture.events, record_failure=True)
                original = source.export
                def export(request_id, deadline):
                    if source.exports + 1 == fail_at:
                        source.exports += 1
                        self.fixture.events.append("source_export" + str(source.exports))
                        raise broker.GoogleTestBrokerError("google_test_broker_source_refused")
                    return original(request_id, deadline)
                network = {"interface_index": 7, "profile": fixtures.UUID}
                with mock.patch.object(source, "export", side_effect=export), \
                        mock.patch.object(broker, "_current_request", return_value=fixtures.REQUEST), \
                        mock.patch.object(broker, "_network", return_value=network), \
                        mock.patch.object(broker, "_runtime", self.fixture.runtime_context), \
                        mock.patch("raspi_cloud_sync_pro.wifi_confinement.hold_wifi_confinement", self.fixture.proof):
                    result = broker._orchestrate(self.fixture.settings, self.fixture.source, self.fixture.backup, 99,
                                                time.monotonic() + 3, manager, source)
                self.assertEqual(result["code"], "google_test_broker_source_refused")
                self.assertEqual(manager.released, fail_at == 3)
                self.assertEqual(source.recorded, [])
                self.assertIn("source_record", self.fixture.events)
                self.assertEqual(len(manager.stopped), 1)
                self.assertEqual(self.runner_result(result), result)
                (self.fixture.runtime / ("4" * 32)).rename(self.fixture.runtime / str(fail_at))
                self.fixture.events.clear()
