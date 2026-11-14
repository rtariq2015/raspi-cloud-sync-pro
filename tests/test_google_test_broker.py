"""Root Test coordination with injected PID1/source and real private protocols.

No privileged syscall, service start, external connection or cloud credential is
used. The fake worker explicitly does not constitute native/cloud acceptance.
"""

import copy
import hashlib
import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_test_broker as broker
from raspi_cloud_sync_pro import google_test_executor as executor
from raspi_cloud_sync_pro.errors import BackupError

REQUEST = "a" * 32
PREPARED = "b" * 32
GENERATION = "c" * 32
PRIVATE = "PRIVATE-broker-folder-client-token-exception"
UUID = "12345678-1234-1234-1234-123456789abc"


def request():
    now = time.time()
    fixture = control.google_test_fixture(REQUEST)
    return {"schema_version": 1, "provider": "google_drive", "request_id": REQUEST, "action": "test",
            "requested_at": now, "expires_at": now + 600, "test": None,
            "preparation": {"request_id": PREPARED, "folder_name": "Private Google Folder",
                            "authorization_sha256": "d" * 64, "rclone_config_sha256": "e" * 64},
            "fixture": {"sha256": hashlib.sha256(fixture).hexdigest(), "size": len(fixture)}}


def receipt(value):
    return {"schema_version": 1, "provider": "google_drive", "request_id": REQUEST, "action": "test",
            "preparation": value["preparation"], "test": None, "completed_at": time.time(),
            "outcome": "verified", "enabled": False, "automatic_deletion": False,
            "result": {"generation_id": GENERATION, "folder_id": PRIVATE,
                       "operational_config_sha256": "f" * 64, "operational_settings_sha256": "1" * 64,
                       "fixture_sha256": value["fixture"]["sha256"], "fixture_size": value["fixture"]["size"],
                       "fixture_uploaded": True, "fixture_readback_verified": True, "token_refresh_verified": True}}


class FakeSource:
    def __init__(self, value, events, *, change_at=None, record_failure=False, fail_export_at=None):
        self.value, self.events = value, events
        self.exports = 0
        self.recorded = []
        self.change_at, self.record_failure = change_at, record_failure
        self.fail_export_at = fail_export_at

    def export(self, request_id, deadline):
        self.exports += 1
        self.events.append("source_export" + str(self.exports))
        if self.exports == self.fail_export_at:
            raise ValueError(PRIVATE)
        value = copy.deepcopy(self.value)
        if self.change_at == self.exports:
            value["preparation"]["authorization_sha256"] = "2" * 64
        return value

    def record(self, request_id, value, deadline):
        self.events.append("source_record")
        if self.record_failure:
            raise ValueError(PRIVATE)
        self.recorded.append((request_id, value))


class FakeManager:
    def __init__(self, value, events, *, fail=None, report_changes=None, receipt_changes=None, trailing=False):
        self.value, self.events = value, events
        self.fail, self.report_changes, self.receipt_changes = fail, report_changes or {}, receipt_changes or {}
        self.trailing = trailing
        self.clients = {}
        self.errors = []
        self.thread = None
        self.released = False
        self.closed = False
        self.stopped = []
        self.invocation = "3" * 32

    def start(self, unit, description, properties, arguments):
        self.unit, self.description, self.properties, self.arguments = unit, description, properties, arguments
        self.events.append("unit_start")
        if self.fail == "start":
            raise OSError(PRIVATE)
        paths = properties["OpenFile"]
        for path in paths:
            filename, name = path.rsplit(":", 1)
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peer.connect(filename)
            peer.setblocking(False)
            self.clients[name] = peer
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def work(self):
        deadline = time.monotonic() + 3
        try:
            canary = self.clients["canary"]
            executor.canary_write_frame(canary.fileno(), {"schema_version": 1, "provider": "google_drive",
                "phase": "ready", "worker_pid": 12345 if self.fail != "ready" else 54321}, deadline)
            go = executor.canary_read_frame(canary.fileno(), deadline)
            self.events.append("canary_go")
            report = {**go, "phase": "report", **dict.fromkeys(broker._CANARY_FLAGS, True),
                      "official_https_bytes": 64, **self.report_changes}
            executor.canary_write_frame(canary.fileno(), report, deadline)
            if self.trailing:
                os.write(canary.fileno(), b"x")
            canary.shutdown(socket.SHUT_WR)
            acknowledgement = executor.canary_read_frame(canary.fileno(), deadline)
            if acknowledgement != {**go, "phase": "ack"}:
                raise AssertionError("Wrong ACK")
            executor._eof(canary.fileno(), deadline)
            self.events.append("canary_ack")
            private_request = destination._read_frame(self.clients["request"].fileno(), deadline)
            release = destination._read_frame(self.clients["gate"].fileno(), deadline)
            if (private_request != self.value or release != {"schema_version": 1, "provider": "google_drive",
                    "purpose": "google_test_wifi_egress", "request_id": REQUEST, "worker_pid": 12345,
                    "expires_at": release["expires_at"]} or not time.time() < release["expires_at"] <= time.time() + 30):
                raise AssertionError("Wrong release")
            self.released = True
            self.events.append("credential_release")
            completed = {**receipt(self.value), **self.receipt_changes}
            destination._write_frame(self.clients["receipt"].fileno(), destination._canonical(completed), deadline)
            self.clients["receipt"].shutdown(socket.SHUT_WR)
            completion = destination._read_frame(self.clients["receipt"].fileno(), deadline)
            expected = {key: go[key] for key in ("schema_version", "provider", "worker_pid", "request_id", "nonce")}
            if completion != {**expected, "phase": "complete"}:
                raise AssertionError("Wrong completion")
            self.events.append("complete_ack")
        except Exception as error:
            self.errors.append(type(error).__name__)

    def capture(self, unit, description):
        self.events.append("capture")
        return self.invocation

    def authenticate(self, unit, description, invocation, arguments):
        self.events.append("authenticate")
        if self.fail == "authenticate":
            raise ValueError(PRIVATE)
        manager = self
        class Worker:
            pid = 12345
            control_group = "/system.slice/" + unit
            def close(self):
                manager.closed = True
        return Worker()

    def recheck(self, *args):
        self.events.append("worker_recheck")
        if self.fail == "recheck":
            raise ValueError(PRIVATE)

    def wait(self, *args):
        self.thread.join(1)
        self.events.append("worker_exit")
        if self.fail == "exit" or self.errors or self.thread.is_alive():
            raise ValueError(PRIVATE)

    def complete_recheck(self, *args):
        self.events.append("completion_recheck")
        if self.fail == "completion":
            raise ValueError(PRIVATE)

    def stop(self, unit, description, invocation):
        self.events.append("unit_stop")
        self.stopped.append((unit, description, invocation))
        for peer in self.clients.values():
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.thread:
            self.thread.join(1)
        for peer in self.clients.values():
            peer.close()
        if self.thread and self.thread.is_alive():
            raise AssertionError("Fake worker did not stop")
        if self.fail == "stop":
            raise ValueError(PRIVATE)


class GoogleTestBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_path = self.root / "source"
        self.runtime = self.root / "runtime"
        for directory in (self.source_path, self.runtime):
            directory.mkdir(mode=0o700)
        self.settings = broker.GoogleTestBrokerSettings(enabled=True, interpreter=Path("/opt/app/venv/bin/python3"),
            rclone=Path("/opt/app/rclone"), rclone_sha256="2" * 64, source_state=self.source_path,
            backup_state=self.root / "prepared", operational_state=self.root / "operational",
            dashboard_user="source-user", backup_user="raspi-backup", public_origin="https://backup.example",
            approval_file=self.root / "APPROVED", runtime_directory=self.runtime, lock_file=self.runtime / "broker.lock",
            wifi_profile_uuid=UUID, dns_ipv4="1.1.1.1", config_path=self.root / "config.json")
        self.source = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        self.backup = SimpleNamespace(pw_uid=4321, pw_gid=4321)
        self.value = request()
        self.events = []
        self.proof_checks = 0
        self.proof_failure = None

    def safe(self, value):
        public = str(value)
        for private in (PRIVATE, REQUEST, PREPARED, GENERATION, UUID, str(self.root), "Traceback", "source-user"):
            self.assertNotIn(private, public)

    def failed_recorded(self, source):
        self.assertEqual(len(source.recorded), 1)
        identifier, failed = source.recorded[0]
        self.assertEqual(identifier, REQUEST)
        self.assertEqual(failed["outcome"], "failed")
        self.assertFalse(failed["enabled"])
        self.assertEqual(control._validate_receipt(failed, self.value, time.time(), None), failed)
        self.assertIsNone(failed["result"]["generation_id"])
        self.assertIsNone(failed["result"]["folder_id"])
        self.assertFalse(failed["result"]["fixture_readback_verified"])
        self.assertLess(self.events.index("unit_stop"), self.events.index("source_record"))

    @contextmanager
    def runtime_context(self, settings):
        identifier = "4" * 32
        path = self.runtime / identifier
        path.mkdir(mode=0o700)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield path, fd, identifier
        finally:
            os.close(fd)

    @contextmanager
    def proof(self, group, interface):
        self.assertEqual(group, "/system.slice/raspi-cloud-google-test-" + "4" * 32 + ".service")
        self.assertEqual(interface, 7)
        self.events.append("proof_held")
        outer = self
        class Held:
            def recheck(self):
                outer.proof_checks += 1
                outer.events.append("proof" + str(outer.proof_checks))
                if outer.proof_failure == outer.proof_checks:
                    raise ValueError(PRIVATE)
        try:
            yield Held()
        finally:
            self.events.append("proof_closed")

    def run_protocol(self, *, manager_options=None, source_options=None, networks=None, pointer=REQUEST):
        manager = FakeManager(self.value, self.events, **(manager_options or {}))
        source = FakeSource(self.value, self.events, **(source_options or {}))
        network = {"interface_index": 7, "profile": UUID}
        with mock.patch.object(broker, "_current_request", return_value=pointer), \
                mock.patch.object(broker, "_network", side_effect=networks or [network, network, network]), \
                mock.patch.object(broker, "_runtime", self.runtime_context), \
                mock.patch("raspi_cloud_sync_pro.wifi_confinement.hold_wifi_confinement", self.proof):
            result = broker._orchestrate(self.settings, self.source, self.backup, 99,
                                         time.monotonic() + 4, manager, source)
        self.safe(result)
        return result, manager, source

    def test_disabled_returns_before_hardware_configuration_state_or_commands(self):
        with mock.patch.object(broker, "_gates", side_effect=AssertionError("No gates")), \
                mock.patch.object(broker, "_command", side_effect=AssertionError("No command")), \
                mock.patch.object(broker.preparation, "_root_directory", side_effect=AssertionError("No state")):
            self.assertEqual(broker.run_google_test_broker(broker.GoogleTestBrokerSettings()),
                             broker._result("disabled", "google_test_broker_disabled"))
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_success_requires_ordered_fresh_authority_and_retains_private_records(self):
        result, manager, source = self.run_protocol()
        self.assertEqual(result, broker._result("tested", "google_test_broker_test_verified_not_enabled"))
        self.assertTrue(manager.released)
        self.assertEqual(source.exports, 3)
        self.assertEqual(len(source.recorded), 1)
        self.assertEqual(source.recorded[0][1]["result"]["folder_id"], PRIVATE)
        self.assertEqual(len(manager.stopped), 1)
        self.assertTrue(manager.closed)
        sequence = self.events
        self.assertLess(sequence.index("proof1"), sequence.index("canary_go"))
        self.assertLess(sequence.index("source_export2"), sequence.index("credential_release"))
        self.assertLess(sequence.index("completion_recheck"), sequence.index("proof4"))
        self.assertLess(sequence.index("proof4"), sequence.index("complete_ack"))
        self.assertLess(sequence.index("complete_ack"), sequence.index("worker_exit"))
        self.assertLess(sequence.index("worker_exit"), sequence.index("source_record"))
        self.assertLess(sequence.index("source_record"), sequence.index("proof_closed"))
        artifacts = self.runtime / ("4" * 32)
        self.assertEqual(json.loads((artifacts / "result.json").read_text()), result)
        for name in ("plan.json", "result.json"):
            self.assertEqual(stat.S_IMODE((artifacts / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((artifacts / "resolv.conf").stat().st_mode), 0o644)
        self.assertEqual((artifacts / "resolv.conf").read_bytes(), b"nameserver 1.1.1.1\noptions timeout:2 attempts:1\n")
        self.assertEqual(stat.S_IMODE(artifacts.stat().st_mode), 0o700)

    def test_canary_failures_never_release_credential_channels(self):
        for index, changes in enumerate(({"loopback_tcp_blocked": False}, {"dns_tcp_verified": 1},
                {"official_https_bytes": True}, {"request_id": "5" * 32}, {"extra": PRIVATE})):
            with self.subTest(changes=changes):
                if index:
                    self.temporary.cleanup()
                    self.setUp()
                result, manager, source = self.run_protocol(manager_options={"report_changes": changes})
                self.assertEqual(result["status"], "failed")
                self.assertFalse(manager.released)
                self.failed_recorded(source)
                self.assertEqual(len(manager.stopped), 1)

    def test_report_trailing_bytes_are_refused_before_ack_or_release(self):
        result, manager, source = self.run_protocol(manager_options={"trailing": True})
        self.assertEqual(result["code"], "google_test_broker_canary_invalid")
        self.assertFalse(manager.released)
        self.failed_recorded(source)

    def test_source_change_before_release_fails_without_worker_credentials(self):
        result, manager, source = self.run_protocol(source_options={"change_at": 2})
        self.assertEqual(result["code"], "google_test_broker_authority_changed")
        self.assertFalse(manager.released)
        self.assertEqual(source.recorded, [])

    def test_completion_source_change_never_acknowledges_or_records_success(self):
        result, manager, source = self.run_protocol(source_options={"change_at": 3})
        self.assertEqual(result["code"], "google_test_broker_authority_changed")
        self.assertTrue(manager.released)
        self.assertEqual(source.recorded, [])
        self.assertNotIn("complete_ack", self.events)

    def test_policy_change_before_release_fails_closed(self):
        result, manager, source = self.run_protocol(networks=[{"interface_index": 7}, {"interface_index": 8}])
        self.assertEqual(result["code"], "google_test_broker_authority_changed")
        self.assertFalse(manager.released)
        self.assertEqual(source.recorded, [])

    def test_dropped_held_policy_before_release_cannot_be_replaced_by_saved_report(self):
        self.proof_failure = 3
        result, manager, source = self.run_protocol()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(manager.released)
        self.failed_recorded(source)

    def test_completion_detached_policy_never_accepts_even_verified_worker_receipt(self):
        self.proof_failure = 4
        result, manager, source = self.run_protocol()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(manager.released)
        self.failed_recorded(source)
        self.assertNotIn("complete_ack", self.events)

    def test_exit_or_source_record_failure_retains_artifacts_and_stops_only_own_unit(self):
        result, manager, source = self.run_protocol(manager_options={"fail": "exit"})
        self.assertEqual(result["status"], "failed")
        self.failed_recorded(source)
        self.assertEqual(manager.stopped, [(manager.unit, manager.description, manager.invocation)])
        self.assertTrue((self.runtime / ("4" * 32) / "plan.json").exists())

    def test_record_refusal_keeps_success_private_and_returns_fixed_failure(self):
        result, manager, source = self.run_protocol(source_options={"record_failure": True})
        self.assertEqual(result["status"], "failed")
        self.assertTrue(manager.released)
        self.assertEqual(source.recorded, [])

    def test_failed_receipt_requires_fresh_source_after_confirmed_cleanup(self):
        result, manager, source = self.run_protocol(manager_options={"report_changes": {"dns_udp_verified": False}},
                                                   source_options={"fail_export_at": 2})
        self.assertEqual(result["status"], "failed")
        self.assertFalse(manager.released)
        self.assertEqual(source.recorded, [])
        self.assertLess(self.events.index("unit_stop"), self.events.index("source_export2"))

    def test_unconfirmed_cleanup_never_publishes_failed_receipt(self):
        result, manager, source = self.run_protocol(manager_options={"fail": "stop", "report_changes": {"dns_udp_verified": False}})
        self.assertEqual(result["code"], "google_test_broker_cleanup_unconfirmed")
        self.assertEqual(source.recorded, [])

    def test_intent_expiring_during_cleanup_never_gets_failed_receipt(self):
        initial = time.time()
        def clock():
            return self.value["expires_at"] + 1 if "unit_stop" in self.events else initial
        with mock.patch.object(broker.time, "time", side_effect=clock):
            result, manager, source = self.run_protocol(manager_options={"report_changes": {"dns_udp_verified": False}})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(source.recorded, [])

    def test_mismatched_private_receipt_never_reaches_source_recorder(self):
        result, manager, source = self.run_protocol(manager_options={"receipt_changes": {"enabled": True}})
        self.assertEqual(result["code"], "google_test_broker_receipt_invalid")
        self.failed_recorded(source)

    def test_cleanup_failure_overrides_success_and_never_claims_stopped(self):
        result, manager, source = self.run_protocol(manager_options={"fail": "stop"})
        self.assertEqual(result["code"], "google_test_broker_cleanup_unconfirmed")
        self.assertEqual(len(source.recorded), 1)

    def test_start_failure_is_scoped_to_predeclared_random_unit_and_retained(self):
        result, manager, source = self.run_protocol(manager_options={"fail": "start"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(manager.stopped, [(manager.unit, manager.description, None)])
        self.failed_recorded(source)
        self.assertTrue((self.runtime / ("4" * 32) / "result.json").exists())

    def test_absent_pointer_is_idle_without_runtime_or_start(self):
        result, manager, source = self.run_protocol(pointer=None)
        self.assertEqual(result["status"], "idle")
        self.assertEqual(source.exports, 0)
        self.assertEqual(manager.stopped, [])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_invalid_source_request_refused_before_runtime_or_pid1(self):
        self.value["preparation"]["folder_name"] = "../" + PRIVATE
        with self.assertRaises(BackupError):
            self.run_protocol()
        self.assertEqual(list(self.runtime.iterdir()), [])

    def decision(self, action="enable"):
        value = request()
        value.update(action=action, fixture=None)
        value["test"] = {"request_id": "9" * 32, "generation_id": GENERATION, "folder_id": PRIVATE,
                         "tested_at": value["requested_at"] - 1, "receipt_sha256": "4" * 64,
                         "operational_settings_sha256": "5" * 64}
        if action == "disable":
            value.update(preparation=None, test=None)
        return value

    def test_enable_disable_apply_only_after_fresh_source_export_without_cloud_or_worker(self):
        for action in ("enable", "disable"):
            with self.subTest(action=action):
                self.value = self.decision(action)
                with mock.patch.object(broker, "_network", side_effect=AssertionError("No network")), \
                        mock.patch.object(broker, "_runtime", side_effect=AssertionError("No runtime")):
                    result, manager, source = self.run_protocol()
                self.assertEqual(result, broker._result("applied", "google_test_broker_control_applied"))
                self.assertEqual(source.exports, 2)
                self.assertEqual(len(source.recorded), 1)
                identifier, applied = source.recorded[0]
                self.assertEqual(identifier, REQUEST)
                self.assertEqual(control._validate_receipt(applied, self.value, time.time(), None), applied)
                self.assertEqual(applied["outcome"], "applied")
                self.assertIs(applied["enabled"], action == "enable")
                self.assertIsNone(applied["result"])
                self.assertFalse(manager.released)
                self.assertEqual(manager.stopped, [])
                self.assertEqual(list(self.runtime.iterdir()), [])

    def test_apply_rejects_changed_source_or_recording_failure_without_worker(self):
        self.value = self.decision()
        for options in ({"change_at": 2}, {"record_failure": True}):
            with self.subTest(options=options), self.assertRaises((BackupError, ValueError)):
                self.run_protocol(source_options=options)
            self.assertEqual(list(self.runtime.iterdir()), [])

    def test_local_decision_strict_schema_expiry_test_freshness_and_disable_without_cloud_fields(self):
        value = self.decision()
        self.assertEqual(broker._control_decision(value, REQUEST), value)
        self.assertEqual(broker._control_decision(self.decision("disable"), REQUEST)["action"], "disable")
        cases = [{**value, "extra": True}, {**value, "fixture": {}}, {**value, "request_id": "0" * 32},
                 {**value, "test": None}, {**value, "preparation": None},
                 {**value, "expires_at": value["requested_at"] + 601},
                 {**value, "requested_at": 1, "expires_at": 601},
                 {**value, "test": {**value["test"], "tested_at": value["requested_at"] + 1}},
                 {**value, "test": {**value["test"], "tested_at": time.time() - 601}},
                 {**self.decision("disable"), "preparation": value["preparation"]},
                 {**self.decision("disable"), "test": value["test"]}]
        for bad in cases:
            with self.subTest(fields=set(bad)), self.assertRaises(BackupError):
                broker._control_decision(bad, REQUEST)

    def test_unit_plan_preserves_narrow_network_syscall_and_write_policy(self):
        properties = broker._unit_properties(self.settings, self.backup, self.runtime, self.runtime / "resolv.conf")
        for key, value in {"User": "raspi-backup", "NoNewPrivileges": "yes", "CapabilityBoundingSet": "",
                "AmbientCapabilities": "", "RestrictNetworkInterfaces": "wlan0", "RestrictAddressFamilies": "AF_INET",
                "ProtectSystem": "strict", "ProtectHome": "yes", "SystemCallArchitectures": "native",
                "PrivateNetwork": "no", "RemainAfterExit": "yes", "Restart": "no"}.items():
            self.assertEqual(properties[key], value)
        self.assertEqual(properties["ReadWritePaths"], str(self.settings.operational_state))
        self.assertEqual(properties["ReadOnlyPaths"], str(self.settings.backup_state))
        self.assertEqual({entry.rsplit(":", 1)[1] for entry in properties["OpenFile"]}, set(broker._NAMES))
        self.assertIn("io_uring_setup", properties["SystemCallFilter"])
        self.assertNotIn("env", broker._argv(self.settings, self.value))

    def test_root_metadata_no_replace_and_failure_retention_on_actual_filesystem(self):
        fd = os.open(self.runtime, os.O_RDONLY | os.O_DIRECTORY)
        try:
            broker._write_private(fd, "record.json", b"original")
            original = (self.runtime / "record.json").stat()
            with self.assertRaises(FileExistsError):
                broker._write_private(fd, "record.json", b"replacement")
            self.assertEqual((self.runtime / "record.json").read_bytes(), b"original")
            self.assertEqual((self.runtime / "record.json").stat().st_ino, original.st_ino)
            target = self.root / "outside"
            target.write_bytes(b"keep")
            (self.runtime / "linked.json").symlink_to(target)
            with self.assertRaises(OSError):
                broker._write_private(fd, "linked.json", b"changed")
            self.assertEqual(target.read_bytes(), b"keep")
        finally:
            os.close(fd)

    def test_actual_private_pointer_requires_owner_modes_strict_schema_and_no_symlink(self):
        workflow = self.source_path / control.WORKFLOW_DIRECTORY
        workflow.mkdir(mode=0o700)
        pointer = workflow / "current.json"
        pointer.write_text(json.dumps({"schema_version": 1, "request_id": REQUEST}))
        pointer.chmod(0o600)
        fd = os.open(self.source_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.assertEqual(broker._current_request(fd, os.geteuid()), REQUEST)
            pointer.chmod(0o644)
            with self.assertRaises(BackupError):
                broker._current_request(fd, os.geteuid())
            pointer.unlink()
            pointer.symlink_to(self.root / "missing")
            with self.assertRaises(OSError):
                broker._current_request(fd, os.geteuid())
        finally:
            os.close(fd)

    def test_disabled_settings_load_does_not_demand_paths_and_enabled_is_explicit(self):
        for enabled in (None, False):
            data = {"schema_version": 1}
            if enabled is not None:
                data["enabled"] = enabled
            with mock.patch.object(broker.preparation, "_root_json", return_value=(data, (1, 2))):
                settings = broker.GoogleTestBrokerSettings.load("/root/config.json")
            self.assertFalse(settings.enabled)
            self.safe(settings)
        for enabled in (1, "true", None):
            with mock.patch.object(broker.preparation, "_root_json", return_value=({"schema_version": 1, "enabled": enabled}, (1, 2))):
                with self.assertRaises(broker.GoogleTestBrokerError):
                    broker.GoogleTestBrokerSettings.load("/root/config.json")

    def test_enabled_configuration_rejects_unsafe_paths_dns_identity_and_policy_types(self):
        data = {name: getattr(self.settings, name) for name in broker._FIELDS if name not in ("schema_version",)}
        data["schema_version"] = 1
        data = {key: str(value) if isinstance(value, Path) else value for key, value in data.items()}
        with mock.patch.object(broker.preparation, "_root_json", return_value=(data, (1, 2))):
            self.assertTrue(broker.GoogleTestBrokerSettings.load("/root/config.json").enabled)
        cases = [{"interpreter": "/opt/current%X/python"}, {"rclone": "/opt/../unsafe"},
                 {"backup_user": "root"}, {"wifi_profile_uuid": "any"}, {"dns_ipv4": "127.0.0.53"},
                 {"dns_ipv4": "::1"}, {"allow_metered": "false"}, {"operational_state": data["backup_state"]},
                 {"rclone_sha256": PRIVATE}, {"lock_file": "/somewhere/lock"}]
        for changes in cases:
            with self.subTest(changes=changes), mock.patch.object(broker.preparation, "_root_json", return_value=({**data, **changes}, (1, 2))):
                with self.assertRaises(broker.GoogleTestBrokerError):
                    broker.GoogleTestBrokerSettings.load("/root/config.json")

    def test_platform_and_approval_gates_precede_writes_and_commands(self):
        with mock.patch.object(broker, "_gates", side_effect=broker.GoogleTestBrokerError("google_test_broker_approval_required")), \
                mock.patch.object(broker.preparation, "_service_identity", side_effect=AssertionError("No identity")), \
                mock.patch.object(broker, "_command", side_effect=AssertionError("No command")):
            result = broker.run_google_test_broker(self.settings)
        self.assertEqual(result["code"], "google_test_broker_approval_required")
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_cleanup_never_stops_another_description_or_invocation(self):
        manager = broker._Systemd(self.settings, self.backup, time.monotonic() + 2)
        base = {"Id": "known.service", "Description": "owned", "InvocationID": "5" * 32,
                "Transient": "yes", "DropInPaths": "", "User": "raspi-backup"}
        for changed in ({"Description": "other"}, {"InvocationID": "6" * 32}):
            with self.subTest(changed=changed), mock.patch.object(manager, "show", return_value={**base, **changed}), \
                    mock.patch.object(manager, "_run") as command:
                with self.assertRaises(broker.GoogleTestBrokerError):
                    manager.stop("known.service", "owned", "5" * 32)
                command.assert_not_called()

    def test_no_override_preflight_checks_generic_and_dash_prefix_dropins(self):
        for path in ("/etc/systemd/system/service.d", "/run/systemd/system/raspi-cloud-.service.d",
                     "/run/systemd/transient/raspi-cloud-google-test-" + "4" * 32 + ".service"):
            with self.subTest(path=path), mock.patch("os.path.lexists", side_effect=lambda value: str(value) == path):
                with self.assertRaises(broker.GoogleTestBrokerError):
                    broker._no_overrides("raspi-cloud-google-test-" + "4" * 32 + ".service")




    def test_gates_validate_fixed_regular_ip_target_and_network_invokes_that_same_target(self):
        executable = self.root / "public-executable"
        executable.write_bytes(b"PUBLIC_PINNED_EXECUTABLE")
        executable.chmod(0o755)
        settings = copy.copy(self.settings)
        object.__setattr__(settings, "rclone_sha256", hashlib.sha256(executable.read_bytes()).hexdigest())
        checked = []
        @contextmanager
        def fixed_file(path, **kwargs):
            checked.append(path)
            fd = os.open(executable, os.O_RDONLY)
            try:
                yield fd, os.fstat(fd)
            finally:
                os.close(fd)
        with mock.patch.object(broker.preparation, "_platform_gate"), \
                mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=settings), \
                mock.patch.object(broker.preparation, "_root_json", return_value=(broker._APPROVAL, ())), \
                mock.patch.object(broker, "_python_environment"), \
                mock.patch.object(broker.preparation, "_installed_package_gate"), \
                mock.patch.object(broker.preparation, "_root_file", fixed_file), \
                mock.patch.object(broker, "_command", return_value=(0, b"systemd 257 (257.13-1~deb13u1)\n")):
            broker._gates(settings)
        self.assertIn(Path("/usr/bin/ip"), checked)
        self.assertNotIn(Path("/usr/sbin/ip"), checked)
        calls = []
        fields = {"GENERAL.TYPE": "wifi", "GENERAL.STATE": "100 (connected)", "GENERAL.CON-UUID": UUID,
                  "GENERAL.METERED": "no", "IP4.DNS": settings.dns_ipv4}
        def query(arguments, deadline, **kwargs):
            calls.append(arguments)
            if arguments[0] == "/usr/bin/ip":
                return 0, b'[{"dev":"wlan0"}]'
            if "CONNECTIVITY" in arguments:
                return 0, b"full"
            return 0, fields[arguments[arguments.index("-g") + 1]].encode()
        with mock.patch.object(broker, "_command", side_effect=query), mock.patch.object(broker.socket, "if_nametoindex", return_value=7):
            broker._network(settings, time.monotonic() + 3)
        self.assertEqual(calls[-1], ["/usr/bin/ip", "-j", "-4", "route", "get", settings.dns_ipv4])

    def test_protected_executable_open_still_refuses_symlink_without_resolving_alias(self):
        target = self.root / "actual-ip"
        target.write_bytes(b"PUBLIC_EXECUTABLE")
        target.chmod(0o755)
        alias = self.root / "ip-alias"
        alias.symlink_to(target)
        @contextmanager
        def parent(path):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                yield fd
            finally:
                os.close(fd)
        with mock.patch.object(broker.preparation, "_root_directory", parent), \
                mock.patch.object(Path, "resolve", side_effect=AssertionError("Never resolve executable aliases")):
            with self.assertRaises(OSError) as error:
                with broker.preparation._root_file(alias, executable=True):
                    self.fail("A symlink executable must not be accepted")
        self.assertEqual(error.exception.errno, 40)  # Linux ELOOP, O_NOFOLLOW retained.


class GoogleTestNativeFacadeTests(unittest.TestCase):
    def test_show_requests_all_properties_so_empty_native_fields_are_present(self):
        unit = "raspi-cloud-google-test-" + "a" * 32 + ".service"
        manager = broker._Systemd(None, None, time.monotonic() + 2)
        raw = ("Id=" + unit + "\nLoadState=not-found\nActiveState=inactive\n"
               "DropInPaths=\nInvocationID=\n").encode()
        with mock.patch.object(manager, "_run", return_value=raw) as command:
            fields = manager.show(unit)
        command.assert_called_once_with(["/usr/bin/systemctl", "show", "--all", "--no-pager",
                                         "--property=" + ",".join(broker._SYSTEMD_PROPERTIES), unit])
        self.assertEqual(fields["DropInPaths"], "")
        self.assertEqual(fields["InvocationID"], "")

    def test_show_selects_single_properties_from_native_repeated_openfile_shape(self):
        unit = "raspi-cloud-google-test-" + "a" * 32 + ".service"
        settings = SimpleNamespace(backup_user="raspi-backup")
        manager = broker._Systemd(settings, None, time.monotonic() + 2)
        # systemd v257 prints one OpenFile line per inherited connection. Its
        # full output also has structured ExecStart/Environment properties.
        full = ("Id=" + unit + "\nDescription=owned\nInvocationID=" + "b" * 32
                + "\nLoadState=loaded\nActiveState=failed\nSubState=failed\n"
                "MainPID=0\nControlPID=0\nTransient=yes\nFragmentPath=/run/systemd/transient/" + unit
                + "\nDropInPaths=\nUser=raspi-backup\nNoNewPrivileges=yes\n"
                "CapabilityBoundingSet=\nAmbientCapabilities=\nRestrictAddressFamilies=AF_INET\n"
                "PrivateNetwork=no\nControlGroup=\nExecMainCode=1\nExecMainStatus=2\nResult=exit-code\n"
                "OpenFile=/run/private/gate.sock:gate\nOpenFile=/run/private/request.sock:request\n"
                "OpenFile=/run/private/receipt.sock:receipt\nOpenFile=/run/private/canary.sock:canary\n"
                "ExecStart={ path=/protected/python ; argv[]=/protected/python -I -B ; }\n"
                "Environment=LANG=C\n").encode()
        self.assertEqual(full.count(b"\nOpenFile="), 4)
        def selected_query(arguments):
            selected = next(value for value in arguments if value.startswith("--property=")).split("=", 1)[1].split(",")
            self.assertEqual(len(selected), len(set(selected)))
            self.assertNotIn("OpenFile", selected)
            self.assertNotIn("ExecStart", selected)
            self.assertNotIn("Environment", selected)
            return b"\n".join(line for line in full.splitlines() if line.split(b"=", 1)[0].decode() in selected) + b"\n"
        with mock.patch.object(manager, "_run", side_effect=selected_query):
            value = manager.show(unit)
            self.assertEqual(manager.capture(unit, "owned"), "b" * 32)
        self.assertEqual(set(value), set(broker._SYSTEMD_PROPERTIES))
        self.assertEqual(value["DropInPaths"], "")
        self.assertEqual(value["ExecMainStatus"], "2")

    def test_show_still_rejects_duplicate_malformed_or_unrequested_properties(self):
        manager = broker._Systemd(None, None, time.monotonic() + 2)
        cases = (b"DropInPaths=\nDropInPaths=/unexpected\n", b"MainPID=0\nMainPID=123\n",
                 b"Description=owned\nnot-a-property\n", b"=missing-key\n", b"Id=x\n\n",
                 b"OpenFile=/run/a:gate\nOpenFile=/run/b:request\n", b"Id=\xff\n")
        for raw in cases:
            with self.subTest(raw=raw), mock.patch.object(manager, "_run", return_value=raw):
                with self.assertRaises((broker.GoogleTestBrokerError, UnicodeError)):
                    manager.show("fresh.service")

    def test_selected_properties_cover_every_systemd_consumer(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(broker._Systemd))
        accessed = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in ("value", "current")
                    and node.args and isinstance(node.args[0], ast.Constant)):
                accessed.add(node.args[0].value)
            if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                    and node.value.id in ("value", "current") and isinstance(node.slice, ast.Constant)):
                accessed.add(node.slice.value)
        self.assertTrue(accessed)
        self.assertLessEqual(accessed, set(broker._SYSTEMD_PROPERTIES))

    def test_cleanup_confirms_absence_when_start_never_created_a_unit(self):
        unit = "raspi-cloud-google-test-" + "a" * 32 + ".service"
        expired = time.monotonic() - 10
        manager = broker._Systemd(None, None, expired)
        absent = {"Id": unit, "LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead",
                  "MainPID": "0", "ControlPID": "0", "Transient": "no", "FragmentPath": "",
                  "DropInPaths": "", "InvocationID": "", "ControlGroup": ""}
        with mock.patch.object(manager, "show", return_value=absent), \
                mock.patch.object(manager, "capture") as capture, mock.patch.object(manager, "_run") as command, \
                mock.patch.object(broker, "_cgroup_pids", return_value=()) as pids:
            manager.stop(unit, "never-created", None)
        capture.assert_not_called()
        command.assert_not_called()
        pids.assert_called_once_with("/system.slice/" + unit, missing=True)
        self.assertEqual(manager.deadline, expired)

    def test_cleanup_refuses_inconsistent_absence_or_remaining_cgroup_processes(self):
        unit = "raspi-cloud-google-test-" + "a" * 32 + ".service"
        manager = broker._Systemd(None, None, time.monotonic() - 10)
        absent = {"Id": unit, "LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead",
                  "MainPID": "0", "ControlPID": "0", "Transient": "no", "FragmentPath": "",
                  "DropInPaths": "", "InvocationID": "", "ControlGroup": ""}
        cases = [({key: value}, ()) for key, value in (
            ("Id", "foreign.service"), ("ActiveState", "active"), ("SubState", "running"),
            ("MainPID", "17"), ("ControlPID", "18"), ("Transient", "yes"),
            ("FragmentPath", "/unrelated/unit"), ("DropInPaths", "/unrelated/override"),
            ("InvocationID", "b" * 32), ("ControlGroup", "/foreign"), ("DropInPaths", None))]
        cases.append(({}, (17,)))
        for changes, processes in cases:
            with self.subTest(changes=changes, processes=processes), \
                    mock.patch.object(manager, "show", return_value={**absent, **changes}), \
                    mock.patch.object(manager, "capture") as capture, mock.patch.object(manager, "_run") as command, \
                    mock.patch.object(broker, "_cgroup_pids", return_value=processes):
                with self.assertRaises(broker.GoogleTestBrokerError):
                    manager.stop(unit, "never-created", None)
                capture.assert_not_called()
                command.assert_not_called()

    def test_expired_command_never_launches_privileged_process(self):
        with mock.patch.object(broker.subprocess, "Popen") as process:
            with self.assertRaises(broker.GoogleTestBrokerError):
                broker._command(["/usr/bin/systemd-run", "PRIVATE"], time.monotonic() - 1)
        process.assert_not_called()

    def test_expired_source_export_or_record_never_allocates_or_spawns(self):
        source = broker._Source(None, None)
        with mock.patch.object(broker.preparation, "_spawn") as process, \
                mock.patch.object(broker.preparation, "_private_pipe") as pipe:
            with self.assertRaises(broker.GoogleTestBrokerError):
                source.export(REQUEST, time.monotonic() - 1)
            with self.assertRaises(broker.GoogleTestBrokerError):
                source.record(REQUEST, {}, time.monotonic() - 1)
        process.assert_not_called()
        pipe.assert_not_called()

    def test_cleanup_has_fresh_bounded_budget_and_confirms_empty_stopped_unit(self):
        unit = "raspi-cloud-google-test-" + "a" * 32 + ".service"
        expired = time.monotonic() - 20
        manager = broker._Systemd(None, None, expired)
        observed = []
        def command(arguments):
            observed.append(manager.deadline - time.monotonic())
            self.assertEqual(arguments, ["/usr/bin/systemctl", "stop", unit])
        stopped = {"Id": unit, "MainPID": "0", "ControlPID": "0", "ActiveState": "inactive", "Description": "owned"}
        with mock.patch.object(manager, "capture", return_value="b" * 32), \
                mock.patch.object(manager, "_run", side_effect=command), mock.patch.object(manager, "show", return_value=stopped), \
                mock.patch.object(broker, "_cgroup_pids", return_value=()):
            manager.stop(unit, "owned", "b" * 32)
        self.assertEqual(manager.deadline, expired)
        self.assertTrue(4 < observed[0] <= 5)
        for changes, pids in (({"MainPID": "123"}, ()), ({"ActiveState": "active"}, ()), ({}, (123,))):
            with self.subTest(changes=changes, pids=pids), mock.patch.object(manager, "capture", return_value="b" * 32), \
                    mock.patch.object(manager, "_run"), mock.patch.object(manager, "show", return_value={**stopped, **changes}), \
                    mock.patch.object(broker, "_cgroup_pids", return_value=pids):
                with self.assertRaises(broker.GoogleTestBrokerError):
                    manager.stop(unit, "owned", "b" * 32)

    def test_actual_manager_load_path_dropins_are_refused_before_start(self):
        manager = broker._Systemd(None, None, time.monotonic() + 2)
        dangerous = "/custom/manager/service.d"
        with mock.patch.object(manager, "_run", return_value=b"/custom/manager /run/systemd/system") as command, \
                mock.patch.object(broker, "_trusted_load_path"), \
                mock.patch("os.path.lexists", side_effect=lambda path: str(path) == dangerous):
            with self.assertRaises(broker.GoogleTestBrokerError):
                manager.start("fresh.service", "owned", {}, ["/protected/python"])
        command.assert_called_once_with(["/usr/bin/systemctl", "show", "--property=UnitPath", "--value"])

    def test_worker_interpreter_must_be_current_isolated_protected_import_tree(self):
        @contextmanager
        def protected(path):
            yield 123
        settings = SimpleNamespace(interpreter=Path("/protected/venv/bin/python3"))
        site = "/protected/venv/lib/python3.13/site-packages"
        paths = (site, "/usr/lib/python3.13")
        flags = SimpleNamespace(isolated=1)
        infos = [SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644),
                 SimpleNamespace(st_uid=123, st_mode=stat.S_IFREG | 0o644),
                 SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o666),
                 SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777)]
        for info in infos:
            with self.subTest(info=info), mock.patch.object(broker.sys, "executable", str(settings.interpreter)), \
                    mock.patch.object(broker.sys, "prefix", "/protected/venv"), mock.patch.object(broker.sys, "flags", flags), \
                    mock.patch.object(broker.sys, "dont_write_bytecode", True), mock.patch.object(broker.sys, "path", paths), \
                    mock.patch.object(broker, "__file__", site + "/raspi_cloud_sync_pro/google_test_broker.py"), \
                    mock.patch.object(Path, "is_dir", autospec=True, side_effect=lambda path: str(path) in paths), \
                    mock.patch.object(broker.preparation, "_root_directory", protected), \
                    mock.patch("os.walk", return_value=[(site, [], ["dependency.py"])]), \
                    mock.patch.object(Path, "lstat", return_value=info):
                if info.st_uid == 0 and info.st_mode == stat.S_IFREG | 0o644:
                    broker._python_environment(settings)
                else:
                    with self.assertRaises(broker.GoogleTestBrokerError):
                        broker._python_environment(settings)
        with mock.patch.object(broker.sys, "executable", "/different/bin/python3"), \
                mock.patch.object(broker.preparation, "_root_directory", side_effect=AssertionError("No probe")):
            with self.assertRaises(broker.GoogleTestBrokerError):
                broker._python_environment(settings)

    def test_start_emits_four_individual_openfile_properties_for_native257_parser(self):
        manager = broker._Systemd(None, None, time.monotonic() + 2)
        specifications = tuple("/run/private/" + name + ".sock:" + name for name in broker._NAMES)
        absent = {"LoadState": "not-found", "ActiveState": "inactive", "DropInPaths": ""}
        with mock.patch.object(broker, "_no_overrides"), mock.patch.object(manager, "_run", return_value=b"/run/systemd/system") as command, \
                mock.patch.object(manager, "show", return_value=absent):
            manager.start("fresh.service", "owned", {"User": "raspi-backup", "OpenFile": specifications}, ["/protected/python"])
        arguments = command.call_args.args[0]
        self.assertEqual([value for value in arguments if value.startswith("--property=OpenFile=")],
                         ["--property=OpenFile=" + value for value in specifications])
        self.assertEqual(arguments[-2:], ["--", "/protected/python"])

    def test_fixed_command_clears_environment_and_discards_private_stderr(self):
        import sys
        script = "import os,sys; print('clean' if 'PRIVATE_PARENT' not in os.environ else 'bad'); sys.stderr.write('PRIVATE')"
        with mock.patch.dict(os.environ, {"PRIVATE_PARENT": PRIVATE}):
            code, raw = broker._command([sys.executable, "-I", "-c", script], time.monotonic() + 2)
        self.assertEqual((code, raw), (0, b"clean\n"))

    def test_command_output_and_wait_are_bounded_and_only_own_child_is_killed(self):
        import subprocess
        import sys
        actual = subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = actual(*args, **kwargs)
            children.append(child)
            return child
        cases = [("print('PRIVATE' * 10000)", 2, 64), ("import time; time.sleep(5)", .04, 65536)]
        for script, timeout, limit in cases:
            with self.subTest(timeout=timeout), mock.patch.object(broker.subprocess, "Popen", side_effect=launch):
                with self.assertRaises(broker.GoogleTestBrokerError) as error:
                    broker._command([sys.executable, "-I", "-c", script], time.monotonic() + timeout, limit=limit)
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertIsNotNone(children[-1].poll())
            self.assertTrue(children[-1].stdout.closed)
            os.kill(os.getpid(), 0)

    def test_source_transport_exports_and_records_with_real_private_framing(self):
        settings = SimpleNamespace(source_state=Path("/private/source"), public_origin="https://backup.example")
        source = broker._Source(settings, SimpleNamespace(pw_uid=123, pw_gid=123))
        value = request()
        report = receipt(value)
        calls = []
        recorded = []
        class Child:
            def __init__(self, incoming=None):
                self.incoming = incoming
            def wait(self, timeout):
                if self.incoming is not None:
                    with destination._channel(self.incoming, writing=False) as fd:
                        recorded.append(destination._read_frame(fd, time.monotonic() + 1))
                    self.incoming = None
                return 0
            def poll(self):
                return 0
        def launch(actual_settings, account, arguments, descriptors):
            self.assertIs(actual_settings, settings)
            self.assertEqual(len(descriptors), 1)
            calls.append(arguments)
            if arguments[0] == "google-control-export":
                fd = descriptors[0]
                destination._write_frame(fd, destination._canonical(value), time.monotonic() + 1)
                return Child()
            self.assertEqual(arguments[0], "google-control-record")
            return Child(os.dup(descriptors[0]))
        with mock.patch.object(broker.preparation, "_spawn", side_effect=launch):
            self.assertEqual(source.export(REQUEST, time.monotonic() + 2), value)
            source.record(REQUEST, report, time.monotonic() + 2)
        self.assertEqual(recorded, [report])
        self.assertEqual([args[0] for args in calls], ["google-control-export", "google-control-record"])
        for args in calls:
            self.assertEqual(args[args.index("--request-id") + 1], REQUEST)
            self.assertNotIn("--action", args)
            self.assertNotIn("--enable", args)

    def test_live_completion_requires_only_main_pid_and_one_unix_stream(self):
        settings = SimpleNamespace(backup_user="raspi-backup")
        manager = broker._Systemd(settings, None, time.monotonic() + 2)
        worker = SimpleNamespace(pid=12345, control_group="/system.slice/raspi-cloud-google-test-" + "a" * 32 + ".service")
        entries = [SimpleNamespace(path="fd0"), SimpleNamespace(path="fd3")]
        @contextmanager
        def scan(path):
            yield iter(entries)
        with mock.patch.object(manager, "recheck"), mock.patch.object(broker, "_cgroup_pids", return_value=(12345,)), \
                mock.patch("os.scandir", scan), mock.patch("os.readlink", side_effect=["/dev/null", "socket:[987]"]), \
                mock.patch.object(broker, "_proc_bytes", return_value=b"header\n0: 0 0 0 1 3 987\n"):
            manager.complete_recheck("unit", "description", "id", worker, [])
        for pids in ((12345, 12346), (), (12346,)):
            with self.subTest(pids=pids), mock.patch.object(manager, "recheck"), \
                    mock.patch.object(broker, "_cgroup_pids", return_value=pids):
                with self.assertRaises(broker.GoogleTestBrokerError):
                    manager.complete_recheck("unit", "description", "id", worker, [])
        with mock.patch.object(manager, "recheck"), mock.patch.object(broker, "_cgroup_pids", return_value=(12345,)), \
                mock.patch("os.scandir", scan), mock.patch("os.readlink", side_effect=["/dev/null", "socket:[987]"]), \
                mock.patch.object(broker, "_proc_bytes", return_value=b"header\n0: 0 0 0 1 3 988\n"):
            with self.assertRaises(broker.GoogleTestBrokerError):
                manager.complete_recheck("unit", "description", "id", worker, [])

    def test_approval_denial_precedes_installation_probes_or_commands(self):
        settings = broker.GoogleTestBrokerSettings(enabled=True, config_path=Path("/protected/config"),
                                                    approval_file=Path("/protected/APPROVED"))
        with mock.patch.object(broker.preparation, "_platform_gate"), \
                mock.patch.object(broker.GoogleTestBrokerSettings, "load", return_value=settings), \
                mock.patch.object(broker.preparation, "_root_json", return_value=({"schema_version": 1, "google_test_broker_approved": False}, ())), \
                mock.patch.object(broker.preparation, "_installed_package_gate", side_effect=AssertionError("No package")), \
                mock.patch.object(broker, "_command", side_effect=AssertionError("No command")):
            with self.assertRaises(broker.GoogleTestBrokerError) as error:
                broker._gates(settings)
        self.assertEqual(error.exception.code, "google_test_broker_approval_required")

    def test_runtime_and_configuration_owned_by_unprivileged_user_are_refused_without_write(self):
        if os.geteuid() == 0:
            self.skipTest("Exercises actual ordinary-UID ownership denial")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            config = path / "config.json"
            config.write_text('{"schema_version":1,"enabled":false}')
            config.chmod(0o600)
            with self.assertRaises(broker.GoogleTestBrokerError):
                broker.GoogleTestBrokerSettings.load(config)
            with self.assertRaises(BackupError):
                with broker._runtime(SimpleNamespace(runtime_directory=path)):
                    self.fail("No root runtime accepted")
            self.assertEqual([entry.name for entry in path.iterdir()], ["config.json"])


@unittest.skipIf(os.geteuid() == 0, "Real source workflow requires an ordinary UID")
class GoogleControlApplyIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing real auth/session/CSRF and preparation fixture. Only
        # its historical Test result is synthetic; no provider or root bypass runs.
        from test_google_control import GoogleControlTests
        self.fixture = GoogleControlTests(methodName="test_old_matching_completed_preparation_supports_new_private_test_intent")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.clock = 1004.0
        self.before_record = None
        self.exports = 0
        self.records = 0
        fixture = self.fixture
        outer = self
        class Transport:
            def export(self, request_id, deadline):
                outer.exports += 1
                return fixture.export(now=outer.clock, request_id=request_id)[1]
            def record(self, request_id, receipt, deadline):
                outer.records += 1
                if outer.before_record is not None:
                    outer.before_record()
                fixture.record(receipt, now=outer.clock, request_id=request_id)
        self.transport = Transport()

    def enable_request(self):
        self.fixture.verified_test()
        self.fixture.request("enable", now=1003)

    def apply(self):
        root = os.open(self.fixture.state, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, root)
        identity = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        forbidden = mock.Mock(side_effect=AssertionError("No cloud/worker path"))
        with mock.patch.object(broker.time, "time", return_value=self.clock), \
                mock.patch.object(broker, "_network", forbidden), mock.patch.object(broker, "_runtime", forbidden), \
                mock.patch.object(broker, "_listeners", forbidden), mock.patch.object(broker, "_release", forbidden):
            report = broker._orchestrate(None, identity, identity, root, time.monotonic() + 3,
                                         forbidden, self.transport)
        forbidden.assert_not_called()
        return report

    def test_real_source_enable_becomes_admitted_and_completed_retry_does_not_replace_receipt(self):
        self.enable_request()
        self.assertFalse(self.fixture.admission(now=self.clock)[1]["admission_authorized"])
        self.assertEqual(self.apply(), broker._result("applied", "google_test_broker_control_applied"))
        self.assertEqual((self.exports, self.records), (2, 1))
        self.assertTrue(self.fixture.admission(now=self.clock)[1]["admission_authorized"])
        path = self.fixture.workflow / (self.fixture.current_id() + ".receipt.json")
        before = path.read_bytes(), path.stat().st_ino
        with self.assertRaises(BackupError):
            self.apply()
        self.assertEqual((path.read_bytes(), path.stat().st_ino), before)
        self.assertTrue(self.fixture.admission(now=self.clock)[1]["admission_authorized"])

    def test_real_disable_records_without_google_preparation_or_grant_and_remains_denied(self):
        self.fixture.request("disable", now=1003)
        for name in ("oauth-clients.json", "oauth-google_drive.json"):
            (self.fixture.state / name).unlink()
        self.assertEqual(self.apply()["status"], "applied")
        receipt = json.loads((self.fixture.workflow / (self.fixture.current_id() + ".receipt.json")).read_text())
        self.assertEqual((receipt["action"], receipt["outcome"], receipt["enabled"], receipt["result"]),
                         ("disable", "applied", False, None))
        self.assertFalse(self.fixture.admission(now=self.clock)[1]["admission_authorized"])

    def test_recorder_rejects_owner_revocation_source_change_expiry_or_superseded_intent(self):
        for change in ("owner", "source", "expired", "superseded"):
            with self.subTest(change=change):
                if change != "owner":
                    self.fixture.doCleanups()
                    self.fixture.setUp()
                self.clock = 1004.0
                self.enable_request()
                identifier = self.fixture.current_id()
                def mutate():
                    if change == "owner":
                        self.fixture.write("auth.json", {**self.fixture.auth, "users": {"owner": {"role": "viewer", "hash": "changed"}}})
                    elif change == "source":
                        self.fixture.write("oauth-google_drive.json", {**self.fixture.grant, "refresh_token": "changed-source"})
                    elif change == "expired":
                        self.clock = 1603.0
                    else:
                        self.fixture.request("disable", now=1004)
                self.before_record = mutate
                with self.assertRaises(BackupError):
                    self.apply()
                self.assertFalse((self.fixture.workflow / (identifier + ".receipt.json")).exists())
