"""Private Test CLI boundaries using real inherited pipes and Unix sockets.

The successful worker is deliberately stubbed: these tests release no network
namespace and make no rclone/cloud request or claim of actual cloud verification.
"""

import errno
import fcntl
import hashlib
import io
import json
import os
import socket
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import cli
from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_test as tester

REQUEST_ID = "a" * 32
PREPARATION_ID = "b" * 32
GENERATION_ID = "c" * 32
PRIVATE = "PRIVATE-CLI-client-token-folder-digest-exception"
ERROR = "Private Google Test unavailable; no uploads were enabled and no private details were printed.\n"
SUMMARY = {"schema_version": 1, "provider": "google_drive", "test_state": "verified", "error_code": None,
           "enabled": False, "current_worker_state_checked": False,
           "account_verified": False, "automatic_deletion": False}


def encode(value):
    raw = json.dumps(value, separators=(",", ":")).encode()
    return len(raw).to_bytes(4, "big") + raw


def request():
    fixture = control.google_test_fixture(REQUEST_ID)
    now = time.time()
    return {"schema_version": 1, "provider": "google_drive", "request_id": REQUEST_ID,
            "action": "test", "requested_at": now, "expires_at": now + 600,
            "preparation": {"request_id": PREPARATION_ID, "folder_name": "Private CLI Folder",
                            "authorization_sha256": "d" * 64, "rclone_config_sha256": "e" * 64},
            "test": None, "fixture": {"size": len(fixture), "sha256": hashlib.sha256(fixture).hexdigest()}}


def receipt():
    source = request()
    return {"schema_version": 1, "provider": "google_drive", "request_id": REQUEST_ID,
            "action": "test", "preparation": source["preparation"], "test": None,
            "completed_at": time.time(), "outcome": "verified", "enabled": False,
            "automatic_deletion": False, "result": {"generation_id": GENERATION_ID,
                "folder_id": PRIVATE, "operational_config_sha256": "f" * 64,
                "operational_settings_sha256": "1" * 64, "fixture_sha256": source["fixture"]["sha256"],
                "fixture_size": source["fixture"]["size"], "fixture_uploaded": True,
                "fixture_readback_verified": True, "token_refresh_verified": True}}


class GoogleTestCliTests(unittest.TestCase):
    def setUp(self):
        self.descriptors = []
        self.worker_gates = []
        self.addCleanup(self.close_descriptors)

    def close_descriptors(self):
        for fd in self.descriptors:
            try:
                os.close(fd)
            except OSError:
                pass

    def own(self, fd):
        self.descriptors.append(fd)
        return fd

    def pipe(self):
        return tuple(self.own(fd) for fd in os.pipe())

    def channels(self, raw=None, *, eof=True, socket_request=False):
        self.worker_gates = []
        if raw is None:
            raw = encode(request())
        if socket_request:
            sender, receiver = socket.socketpair()
            request_fd = self.own(receiver.detach())
            request_writer = self.own(sender.detach())
        else:
            request_fd, request_writer = self.pipe()
        if raw:
            os.write(request_writer, raw)
        if eof:
            if socket_request:
                with socket.socket(fileno=os.dup(request_writer)) as channel:
                    channel.shutdown(socket.SHUT_WR)
            else:
                os.close(request_writer)
                self.descriptors.remove(request_writer)
        gate_peer, gate = socket.socketpair()
        gate_fd, gate_peer_fd = self.own(gate.detach()), self.own(gate_peer.detach())
        os.write(gate_peer_fd, encode({"private_stub_release": PRIVATE}))
        with socket.socket(fileno=os.dup(gate_peer_fd)) as channel:
            channel.shutdown(socket.SHUT_WR)
        receipt_reader, receipt_fd = self.pipe()
        return {"request": request_fd, "request_writer": request_writer,
                "gate": gate_fd, "gate_peer": gate_peer_fd,
                "receipt": receipt_fd, "receipt_reader": receipt_reader}

    @staticmethod
    def arguments(channels, **changes):
        values = {"prepared-state": "/private/prepared", "operational-state": "/private/operational",
                  "request-fd": channels["request"], "root-gate-fd": channels["gate"],
                  "receipt-fd": channels["receipt"], "rclone": "/protected/rclone",
                  "rclone-sha256": "2" * 64, **changes}
        arguments = ["google-test"]
        for name, value in values.items():
            if value is not None:
                arguments.extend(("--" + name, str(value)))
        return arguments

    @staticmethod
    def invoke(arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def consumed(self, channels):
        for name in ("request", "gate", "receipt"):
            self.closed(channels[name])
        for fd in self.worker_gates:
            self.closed(fd)

    def no_public_private(self, text):
        for value in (PRIVATE, REQUEST_ID, PREPARATION_ID, GENERATION_ID, "/private/", "/protected/",
                      "Traceback", "folder_id", "authorization_sha256"):
            self.assertNotIn(value, text)

    def read_receipt(self, channels):
        with destination._channel(channels["receipt_reader"], writing=False) as fd:
            value = destination._read_frame(fd, destination._deadline(1))
        self.descriptors.remove(channels["receipt_reader"])
        return value

    def successful_worker(self, prepared, operational, value, **options):
        self.assertEqual(prepared, Path("/private/prepared"))
        self.assertEqual(operational, Path("/private/operational"))
        self.assertEqual(value["request_id"], REQUEST_ID)
        self.assertEqual(options["executable"], Path("/protected/rclone"))
        self.assertEqual(options["executable_sha256"], "2" * 64)
        self.assertEqual(options["timeout"], 120.0)
        self.worker_gates.append(options["root_gate_fd"])
        with destination._channel(options["root_gate_fd"], writing=False) as fd:
            self.assertEqual(destination._read_frame(fd, destination._deadline(1)), {"private_stub_release": PRIVATE})
        return tester.GoogleTestOutcome(receipt())

    def test_success_dispatches_exact_request_and_private_receipt_only(self):
        for sockets in (False, True):
            with self.subTest(socket_request=sockets):
                channels = self.channels(socket_request=sockets)
                original_gate = channels["gate"]
                with mock.patch.object(tester, "run_google_test", side_effect=self.successful_worker) as worker, \
                        mock.patch.object(control, "request_google_control", side_effect=AssertionError("No intent")), \
                        mock.patch("raspi_cloud_sync_pro.oauth.OwnerProof", side_effect=AssertionError("No proof")), \
                        mock.patch("subprocess.Popen", side_effect=AssertionError("No process")):
                    code, output, errors = self.invoke(self.arguments(channels))
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output), SUMMARY)
                self.assertEqual(errors, "")
                self.assertNotEqual(worker.call_args.kwargs["root_gate_fd"], original_gate)
                self.no_public_private(output)
                private = self.read_receipt(channels)
                self.assertEqual(private["request_id"], REQUEST_ID)
                self.assertEqual(private["result"]["folder_id"], PRIVATE)
                self.assertEqual(private["enabled"], False)
                self.consumed(channels)

    def test_absent_root_gate_refuses_without_reading_request_or_state(self):
        channels = self.channels(raw=b"", eof=False)
        with mock.patch.object(tester, "run_google_test") as worker, \
                mock.patch.object(destination, "_read_frame", side_effect=AssertionError("No request read")), \
                mock.patch.object(destination, "_private_directory", side_effect=AssertionError("No credentials")):
            code, output, errors = self.invoke(self.arguments(channels, **{"root-gate-fd": None}))
        self.assertEqual((code, output, errors), (2, "", ERROR))
        worker.assert_not_called()
        self.closed(channels["request"])
        self.closed(channels["receipt"])
        os.fstat(channels["gate"])  # Omitted/invalid descriptors remain caller-owned.

    def test_output_validation_precedes_worker_and_request_read(self):
        channels = self.channels()
        with tempfile.TemporaryFile() as invalid:
            with mock.patch.object(tester, "run_google_test") as worker, \
                    mock.patch.object(destination, "_read_frame", side_effect=AssertionError("No request read")):
                code, output, errors = self.invoke(self.arguments(channels, **{"receipt-fd": invalid.fileno()}))
            self.assertEqual((code, output, errors), (2, "", ERROR))
            worker.assert_not_called()
            os.fstat(invalid.fileno())
            self.assertEqual(os.fstat(invalid.fileno()).st_size, 0)
        self.closed(channels["request"])
        self.closed(channels["gate"])

    def test_regular_or_wrong_direction_input_and_gate_fail_before_worker(self):
        for name in ("request-fd", "root-gate-fd"):
            for regular in (True, False):
                with self.subTest(name=name, regular=regular), tempfile.TemporaryFile() as invalid:
                    channels = self.channels()
                    invalid_fd = invalid.fileno() if regular else channels["receipt"]
                    with mock.patch.object(tester, "run_google_test") as worker:
                        result = self.invoke(self.arguments(channels, **{name: invalid_fd}))
                    self.assertEqual(result, (2, "", ERROR))
                    worker.assert_not_called()
                    if regular:
                        os.fstat(invalid_fd)

    def test_standard_descriptor_and_standard_alias_remain_caller_owned(self):
        for standard in (0, 1, 2):
            for alias in (False, True):
                with self.subTest(standard=standard, alias=alias):
                    channels = self.channels()
                    fd = self.own(os.dup(standard)) if alias else standard
                    with mock.patch.object(tester, "run_google_test") as worker:
                        result = self.invoke(self.arguments(channels, **{"receipt-fd": fd}))
                    self.assertEqual(result, (2, "", ERROR))
                    worker.assert_not_called()
                    os.fstat(fd)
                    self.closed(channels["request"])
                    self.closed(channels["gate"])

    def test_same_number_and_kernel_alias_channels_are_rejected_and_consumed(self):
        for alias in (False, True):
            with self.subTest(alias=alias):
                channels = self.channels()
                extra = self.own(os.dup(channels["gate"])) if alias else channels["gate"]
                with mock.patch.object(tester, "run_google_test") as worker:
                    result = self.invoke(self.arguments(channels, **{"request-fd": extra}))
                self.assertEqual(result, (2, "", ERROR))
                worker.assert_not_called()
                self.closed(channels["receipt"])
                self.closed(channels["gate"])
                self.closed(extra)

    def test_opposite_ends_of_one_anonymous_pipe_are_not_distinct_channels(self):
        channels = self.channels()
        incoming, outgoing = self.pipe()
        with mock.patch.object(tester, "run_google_test") as worker:
            result = self.invoke(self.arguments(channels, **{"receipt-fd": outgoing, "request-fd": incoming}))
        self.assertEqual(result, (2, "", ERROR))
        worker.assert_not_called()
        self.closed(incoming)
        self.closed(outgoing)
        self.closed(channels["gate"])

    def test_invalid_descriptors_do_not_prevent_other_accepted_endpoint_cleanup(self):
        for name in ("request-fd", "root-gate-fd", "receipt-fd"):
            with self.subTest(name=name):
                channels = self.channels()
                with mock.patch.object(tester, "run_google_test") as worker:
                    result = self.invoke(self.arguments(channels, **{name: -1}))
                self.assertEqual(result, (2, "", ERROR))
                worker.assert_not_called()
                for key in ("request", "gate", "receipt"):
                    if key + "-fd" != name and not (key == "gate" and name == "root-gate-fd"):
                        self.closed(channels[key])

    def test_bad_frames_nonobjects_duplicate_json_and_oversize_never_call_worker(self):
        cases = [b"\0\0\0\0", (65537).to_bytes(4, "big"), b"\0\0\0\x05{}",
                 b"\0\0\0\x01\xff", b"\0\0\0\x0d{\"x\":1,\"x\":2}", encode([]),
                 encode(request()) + b"x", encode(request()) + encode(request())]
        for raw in cases:
            with self.subTest(length=len(raw)):
                channels = self.channels(raw)
                with mock.patch.object(tester, "run_google_test") as worker:
                    result = self.invoke(self.arguments(channels))
                self.assertEqual(result, (2, "", ERROR))
                worker.assert_not_called()
                self.consumed(channels)

    def test_missing_request_eof_has_bounded_deadline_and_restores_alias_flags(self):
        channels = self.channels(eof=False)
        alias = self.own(os.dup(channels["request"]))
        flags = fcntl.fcntl(alias, fcntl.F_GETFL)
        original = destination._deadline
        started = time.monotonic()
        with mock.patch.object(destination, "_deadline", side_effect=lambda _: original(.03)), \
                mock.patch.object(tester, "run_google_test") as worker:
            result = self.invoke(self.arguments(channels))
        self.assertEqual(result, (2, "", ERROR))
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), flags)
        worker.assert_not_called()
        self.consumed(channels)

    def test_timeout_paths_and_digest_arguments_are_validated_before_worker(self):
        cases = [{"timeout": value} for value in ("nan", "inf", "-1", "0", "301")]
        cases += [{name: value} for name, value in (("rclone-sha256", PRIVATE), ("rclone", "relative"),
                  ("prepared-state", "/private/../other"), ("operational-state", "relative"))]
        for changes in cases:
            with self.subTest(changes=changes):
                channels = self.channels()
                with mock.patch.object(tester, "run_google_test") as worker:
                    result = self.invoke(self.arguments(channels, **changes))
                self.assertEqual(result, (2, "", ERROR))
                worker.assert_not_called()
                self.consumed(channels)

    def test_custom_timeout_forwarded_without_any_other_worker_switches(self):
        channels = self.channels()
        def worker(*args, **options):
            self.assertEqual(options["timeout"], 12.5)
            self.assertEqual(set(options), {"root_gate_fd", "executable", "executable_sha256", "timeout"})
            self.worker_gates.append(options["root_gate_fd"])
            return tester.GoogleTestOutcome(receipt())
        with mock.patch.object(tester, "run_google_test", side_effect=worker):
            code, output, errors = self.invoke(self.arguments(channels, timeout="12.5"))
        self.assertEqual((code, json.loads(output), errors), (0, SUMMARY, ""))
        self.read_receipt(channels)
        self.consumed(channels)

    def test_parser_failures_are_fixed_private_and_do_not_accept_descriptors(self):
        cases = [{"timeout": PRIVATE}, {"request-fd": PRIVATE}, {"rclone": None}]
        for changes in cases:
            with self.subTest(changes=changes):
                channels = self.channels()
                with mock.patch.object(tester, "run_google_test") as worker:
                    result = self.invoke(self.arguments(channels, **changes))
                self.assertEqual(result, (2, "", ERROR))
                worker.assert_not_called()
                for name in ("request", "gate", "receipt"):
                    os.fstat(channels[name])
        channels = self.channels()
        with mock.patch.object(tester, "run_google_test") as worker:
            result = self.invoke(self.arguments(channels) + ["--enable", PRIVATE])
        self.assertEqual(result, (2, "", ERROR))
        worker.assert_not_called()
        self.no_public_private(result[2])

    def test_help_and_missing_arguments_never_call_worker(self):
        with mock.patch.object(tester, "run_google_test") as worker:
            code, output, errors = self.invoke(["google-test", "--help"])
            self.assertEqual(code, 0)
            self.assertIn("--root-gate-fd", output)
            self.assertEqual(errors, "")
            self.assertEqual(self.invoke(["google-test"]), (2, "", ERROR))
        worker.assert_not_called()

    def test_worker_exceptions_close_transferred_gate_even_before_acceptance(self):
        for exception in (tester.GoogleTestError, OSError, ValueError, RuntimeError, MemoryError):
            with self.subTest(exception=exception):
                channels = self.channels()
                def worker(*args, **options):
                    self.worker_gates.append(options["root_gate_fd"])
                    raise exception(PRIVATE)
                with mock.patch.object(tester, "run_google_test", side_effect=worker):
                    result = self.invoke(self.arguments(channels))
                self.assertEqual(result, (2, "", ERROR))
                self.no_public_private(result[2])
                self.consumed(channels)
                self.assertEqual(os.read(channels["receipt_reader"], 1), b"")

    def test_cleanup_does_not_close_an_unrelated_reused_worker_gate_number(self):
        channels = self.channels()
        unrelated = tempfile.TemporaryFile()
        self.addCleanup(unrelated.close)
        reused = []
        def worker(*args, **options):
            fd = options["root_gate_fd"]
            os.close(fd)
            os.dup2(unrelated.fileno(), fd)
            self.own(fd)
            reused.append(fd)
            raise RuntimeError(PRIVATE)
        with mock.patch.object(tester, "run_google_test", side_effect=worker):
            result = self.invoke(self.arguments(channels))
        self.assertEqual(result, (2, "", ERROR))
        os.fstat(reused[0])
        self.consumed(channels)

    def test_failed_worker_result_is_private_and_never_enables(self):
        channels = self.channels()
        failed = {**receipt(), "outcome": "failed", "result": None}
        with mock.patch.object(tester, "run_google_test", return_value=tester.GoogleTestOutcome(failed, PRIVATE)):
            code, output, errors = self.invoke(self.arguments(channels))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output), {**SUMMARY, "test_state": "failed", "error_code": "google_test_failed"})
        self.assertEqual(errors, "")
        self.no_public_private(output)
        self.assertEqual(self.read_receipt(channels), failed)
        self.consumed(channels)

    def test_malformed_or_oversize_worker_outcomes_never_leak_to_stdout(self):
        cases = [object(), tester.GoogleTestOutcome({**receipt(), "outcome": PRIVATE}),
                 tester.GoogleTestOutcome({**receipt(), "enabled": True}),
                 tester.GoogleTestOutcome({**receipt(), "automatic_deletion": True}),
                 tester.GoogleTestOutcome({**receipt(), "private": PRIVATE * 10000})]
        for outcome in cases:
            with self.subTest(kind=type(outcome).__name__):
                channels = self.channels()
                with mock.patch.object(tester, "run_google_test", return_value=outcome):
                    result = self.invoke(self.arguments(channels))
                self.assertEqual(result, (2, "", ERROR))
                self.assertEqual(os.read(channels["receipt_reader"], 1), b"")
                self.consumed(channels)

    def test_broken_receipt_reader_is_fixed_failure_and_closes_all_accepted_channels(self):
        channels = self.channels()
        os.close(channels["receipt_reader"])
        self.descriptors.remove(channels["receipt_reader"])
        with mock.patch.object(tester, "run_google_test", side_effect=self.successful_worker):
            result = self.invoke(self.arguments(channels))
        self.assertEqual(result, (2, "", ERROR))
        self.consumed(channels)

    @unittest.skipIf(os.geteuid() == 0, "Requires an actual non-root Unix peer")
    def test_real_worker_rejects_unprivileged_gate_before_credentials_or_process(self):
        channels = self.channels()
        with mock.patch.object(destination, "_private_directory", side_effect=AssertionError("No credential I/O")), \
                mock.patch.object(tester.operational, "create_google_operational_generation", side_effect=AssertionError("No generation")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("No process")):
            result = self.invoke(self.arguments(channels))
        self.assertEqual(result, (2, "", ERROR))
        self.consumed(channels)
