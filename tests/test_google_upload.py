"""Private ordinary-UID files and synthetic transport; no real Enable or network."""

import ast
import copy
import hashlib
import inspect
import json
import os
import socket
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_operational as operational
from raspi_cloud_sync_pro import google_test as tester
from raspi_cloud_sync_pro import google_upload as upload
from raspi_cloud_sync_pro import staging
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from test_google_test import SETTINGS, TOKEN, config, frame

PREPARATION = "a" * 32
BATCH = "b" * 32
CONTROL = "c" * 32


def admission(preparation=None, generation=None):
    return {"schema_version": 1, "provider": "google_drive", "admission_authorized": True,
        "control_request_id": CONTROL, "single_admission": True, "checked_at": 1000,
        "preparation": preparation or {"request_id": PREPARATION, "folder_name": "Synthetic Folder",
            "authorization_sha256": "d" * 64, "rclone_config_sha256": "e" * 64},
        "test": {"request_id": "f" * 32, "generation_id": generation or "1" * 32,
            "folder_id": SETTINGS["root_folder_id"], "tested_at": 900,
            "receipt_sha256": "2" * 64,
            "operational_settings_sha256": control.google_operational_settings_sha256(SETTINGS)}}


def release(value, batch_id=BATCH, digest="3" * 64, **changes):
    return {"schema_version": 1, "provider": "google_drive", "purpose": "google_batch_upload_wifi_egress",
        "control_request_id": value["control_request_id"],
        "admission_sha256": hashlib.sha256(destination._canonical(value)).hexdigest(),
        "batch_id": batch_id, "manifest_sha256": digest, "worker_pid": os.getpid(), "expires_at": 1010, **changes}


class GateTests(unittest.TestCase):
    def test_authorization_has_no_runtime_clock_override(self):
        self.assertNotIn("now", inspect.signature(upload.run_google_batch_upload).parameters)
        self.assertNotIn("now", inspect.signature(upload._gate).parameters)

    def test_default_refusal_before_any_state_or_process_or_history(self):
        with mock.patch("os.open", side_effect=AssertionError("no state")), \
                mock.patch.object(tester, "_Runner", side_effect=AssertionError("no process")), \
                mock.patch.object(upload, "History", side_effect=AssertionError("no history")):
            with self.assertRaisesRegex(upload.GoogleUploadError, "^google_upload_network_disabled$"):
                upload.run_google_batch_upload("PRIVATE", "PRIVATE", "PRIVATE", BATCH, "3" * 64, {})

    def gate(self, value, gate=None, **kwargs):
        parent, fd = frame(release(value) if gate is None else gate, **kwargs)
        try:
            with mock.patch.object(tester, "_root_peer"), mock.patch.object(tester.time, "time", return_value=1000):
                upload._gate(fd, value, BATCH, "3" * 64)
        finally:
            parent.close()
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_fresh_exact_root_frame_and_eof_required(self):
        value = admission()
        self.gate(value)
        for field, changed in (("purpose", "google_test_wifi_egress"), ("worker_pid", True),
                ("worker_pid", os.getpid() + 1), ("batch_id", "4" * 32), ("manifest_sha256", "4" * 64),
                ("control_request_id", "4" * 32), ("admission_sha256", "4" * 64), ("expires_at", 1000),
                ("expires_at", 1031), ("schema_version", True)):
            with self.subTest(field=field), self.assertRaises(upload.GoogleUploadError):
                self.gate(value, release(value, **{field: changed}))
        for gate in ({**release(value), "extra": True}, {key: item for key, item in release(value).items() if key != "purpose"}):
            with self.assertRaises(upload.GoogleUploadError):
                self.gate(value, gate)
        with self.assertRaises(upload.GoogleUploadError):
            self.gate(value, trailing=b"PRIVATE")

    def test_real_unprivileged_peer_is_not_root_authority(self):
        if os.geteuid() == 0:
            self.skipTest("ordinary peer required")
        value = admission()
        parent, fd = frame(release(value))
        try:
            with self.assertRaises(upload.GoogleUploadError):
                upload._gate(fd, value, BATCH, "3" * 64)
            with self.assertRaises(OSError):
                os.fstat(fd)
        finally:
            parent.close()

    def test_denied_stale_future_or_tampered_admission_cannot_become_enable(self):
        value = admission()
        cases = [{**value, "admission_authorized": False}, {**value, "single_admission": False},
                 {**value, "checked_at": 970}, {**value, "checked_at": 1001},
                 {**value, "extra": True}, {**value, "test": None}, {**value, "preparation": None},
                 {**value, "control_request_id": "../bad"}, {**value, "schema_version": True},
                 {**value, "test": {**value["test"], "tested_at": 1001}}]
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(upload.GoogleUploadError):
                self.gate(changed)
        changed = copy.deepcopy(value)
        changed["test"]["generation_id"] = "4" * 32
        with self.assertRaises(upload.GoogleUploadError):
            self.gate(changed, release(value))

    def test_gate_clock_is_sampled_after_frame_ingest(self):
        value = admission()
        parent, fd = frame(release(value))
        original = destination._read_frame
        clock = [1000]
        def read(*args):
            result = original(*args)
            clock[0] = 1030
            return result
        try:
            with mock.patch.object(tester, "_root_peer"), mock.patch.object(destination, "_read_frame", side_effect=read), \
                    mock.patch.object(tester.time, "time", side_effect=lambda: clock[0]):
                with self.assertRaises(upload.GoogleUploadError):
                    upload._gate(fd, value, BATCH, "3" * 64)
        finally:
            parent.close()

    def test_private_outcome_projection_cannot_echo_constructor_private_values(self):
        outcome = upload.GoogleUploadOutcome({"batch_state": "PRIVATE", "files_pending": "PRIVATE", "token": "PRIVATE"})
        self.assertNotIn("PRIVATE", repr(outcome) + json.dumps(outcome.projection()))
        self.assertEqual(outcome.projection()["batch_state"], "failed")


@unittest.skipIf(os.geteuid() == 0, "real private files require ordinary UID")
class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.old_umask)
        self.prepared = self.root / PREPARATION
        self.operational = self.root / "operational"
        self.staging = self.root / "staging"
        for path in (self.prepared, self.operational, self.staging):
            path.mkdir(mode=0o700)
        client = {key: SETTINGS[key] for key in ("client_id", "client_secret")}
        bundle = {"schema_version": 1, "provider": "google_drive", "source_origin": "https://synthetic.invalid",
            "client": client, "authorization": {"schema_version": 1, "provider": "google_drive",
                "client_id_sha256": hashlib.sha256(client["client_id"].encode()).hexdigest(),
                "received_at": 100, "expires_at": 200, "token_type": "Bearer",
                "access_token": TOKEN["access_token"], "refresh_token": TOKEN["refresh_token"],
                "scopes": [destination.GOOGLE_SCOPE]}}
        raw = destination._canonical(bundle)
        read, write = os.pipe()
        os.write(write, len(raw).to_bytes(4, "big") + raw)
        os.close(write)
        destination.prepare_google_destination(self.prepared, read, "Synthetic Folder")
        proof = self.prepared / destination.PREPARATION_DIRECTORY
        manifest = json.loads((proof / "preparation.json").read_bytes())
        self.preparation = {"request_id": PREPARATION, "folder_name": "Synthetic Folder",
            "authorization_sha256": manifest["authorization_sha256"], "rclone_config_sha256": manifest["rclone_config_sha256"]}
        self.generation = operational.create_google_operational_generation(self.prepared, self.operational, PREPARATION,
            self.preparation["folder_name"], self.preparation["authorization_sha256"], self.preparation["rclone_config_sha256"])
        self.generation.config_path.write_bytes(config())
        self.value = admission(self.preparation, self.generation.generation_id)
        self.original = {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                         for path in proof.iterdir()}
        self.calls = []
        self.remote = {}
        self.corrupt = False
        self.refresh = False
        self.tamper = None
        self.batch, self.digest = self.make_batch(BATCH, {"empty": b"", "nested/雪.txt": b"synthetic content"})
        owner = self
        class FakeRunner:
            def __init__(self, executable, digest, deadline, config):
                self.executable, self.deadline, self.config = str(executable), deadline, config
            def check(self):
                if time.monotonic() >= self.deadline:
                    raise tester.GoogleTestError("google_test_timeout")
            def run(self, args):
                self.check()
                owner.calls.append(args)
                if args[0] == "copyto":
                    owner.remote[args[2]] = Path(args[1]).read_bytes()
                elif args[0] == "checksum":
                    for line in Path(args[2]).read_text().splitlines():
                        digest, key = line.split("  ", 1)
                        actual = b"CORRUPT" if owner.corrupt else owner.remote[args[3] + key]
                        if hashlib.sha256(actual).hexdigest() != digest:
                            raise BackupError("synthetic_checksum_failed", retryable=True)
                else:
                    raise AssertionError("no folder creation, listing or deletion")
                if owner.refresh:
                    parsed = tester._parse(tester._read_config(self.config), root_required=True)
                    tester._write_config(self.config, parsed.settings, {**parsed.token, "access_token": "rotated-private-access"})
                if owner.tamper:
                    owner.tamper(self)
        self.runner = mock.patch.object(tester, "_Runner", FakeRunner)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def make_batch(self, identity, files):
        path = self.staging / (identity + ".ready")
        (path / "files").mkdir(mode=0o700, parents=True)
        entries = []
        for relative, raw in files.items():
            target = path / "files" / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(raw)
            entries.append({"relative": relative, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        manifest = {"schema_version": 1, "files": entries}
        (path / "manifest.json").write_bytes(json.dumps(manifest, indent=2).encode())
        digest = hashlib.sha256(destination._canonical(manifest)).hexdigest()
        return path, digest

    def execute(self, *, batch_id=BATCH, digest=None, value=None, **changes):
        value = self.value if value is None else value
        digest = self.digest if digest is None else digest
        parent, fd = frame(release(value, batch_id, digest))
        arguments = dict(root_gate_fd=fd, executable="/protected/rclone", executable_sha256="8" * 64)
        arguments.update(changes)
        try:
            with mock.patch.object(tester, "_root_peer"), \
                    mock.patch.object(tester.time, "time", return_value=1000), \
                    mock.patch.object(socket, "create_connection", side_effect=AssertionError("no real network")):
                return upload.run_google_batch_upload(self.prepared, self.operational, self.staging,
                    batch_id, digest, value, **arguments)
        finally:
            parent.close()

    def history(self):
        return History(self.generation.config_path.parent.parent / upload._HISTORY)

    def test_real_engine_upload_download_verification_and_historical_reuse(self):
        outcome = self.execute()
        self.assertEqual(outcome.receipt["batch_state"], "verified")
        self.assertEqual(outcome.receipt["files_verified_now"], 2)
        self.assertEqual(len(self.calls), 4)
        with self.history() as history:
            for job in history.jobs():
                self.assertEqual([event["state"] for event in history.events(job["id"])],
                                 ["queued", "uploading", "uploaded", "verifying", "verified"])
                self.assertEqual(job["destination"], self.generation.destination_id)
        again = self.execute()
        self.assertEqual(again.receipt["batch_state"], "historical")
        self.assertEqual((again.receipt["files_verified_now"], again.receipt["files_previously_verified"]), (0, 2))
        self.assertEqual(len(self.calls), 4)
        for path, original in self.original.items():
            self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), original)
        for private in (str(self.root), PREPARATION, self.generation.generation_id, SETTINGS["client_secret"], SETTINGS["root_folder_id"]):
            self.assertNotIn(private, repr(outcome) + json.dumps(outcome.projection()))

    def test_token_refresh_is_allowed_without_forced_expiry_or_test_provider(self):
        self.refresh = True
        self.assertEqual(self.execute().receipt["batch_state"], "verified")
        actual = tester.validate_google_operational_config(tester._read_config(self.generation.config_path), SETTINGS)
        self.assertEqual(actual.token["access_token"], "rotated-private-access")
        self.assertEqual(actual.token["expiry"], TOKEN["expiry"])
        self.assertEqual([args[0] for args in self.calls], ["copyto", "checksum", "copyto", "checksum"])

    def test_admission_is_snapshotted_before_caller_can_change_its_bindings(self):
        self.tamper = lambda runner: self.value["test"].update(folder_id="DifferentFolder123")
        outcome = self.execute()
        self.assertEqual(outcome.receipt["batch_state"], "verified")
        self.assertEqual(outcome.receipt["test"]["folder_id"], SETTINGS["root_folder_id"])

    def test_bad_batch_never_reaches_generation_credentials(self):
        with mock.patch.object(operational, "inspect_google_operational_generation", side_effect=AssertionError("no credentials")):
            with self.assertRaises(upload.GoogleUploadError):
                self.execute(digest="0" * 64)

    def test_expired_public_runner_deadline_cannot_claim_success(self):
        self.tamper = lambda runner: setattr(runner, "deadline", time.monotonic() - 1)
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        with self.history() as history:
            self.assertTrue(all(job["state"] != "verified" for job in history.jobs()))

    def test_every_later_rehash_phase_has_per_chunk_abort_before_false_verification(self):
        original = staging.hash_stream
        data = b"x" * (staging.CHUNK_SIZE * 3 + 17)
        # Direct load, enqueue revalidation, engine preflight, provider preflight,
        # then engine's post-download local recheck all use the same deadline.
        for phase in range(1, 6):
            identity = f"{phase:032x}"
            path, digest = self.make_batch(identity, {f"payload{phase}": data})
            self.calls.clear()
            calls = [0]
            stopped = []
            def hashed(stream, *, check=None):
                calls[0] += 1
                self.assertTrue(callable(check))
                if calls[0] != phase:
                    return original(stream, check=check)
                def abort():
                    check()
                    if stream.tell() >= staging.CHUNK_SIZE:
                        stopped.append(stream.tell())
                        raise tester.GoogleTestError("google_test_timeout")
                return original(stream, check=abort)
            with self.subTest(phase=phase), mock.patch.object(staging, "hash_stream", side_effect=hashed):
                try:
                    outcome = self.execute(batch_id=identity, digest=digest)
                except upload.GoogleUploadError:
                    outcome = None
                if outcome is not None:
                    self.assertEqual(outcome.receipt["batch_state"], "failed")
                self.assertEqual(stopped, [staging.CHUNK_SIZE])
                if phase < 5:
                    self.assertEqual(self.calls, [])
                else:
                    self.assertEqual([arguments[0] for arguments in self.calls], ["copyto", "checksum"])
                self.assertEqual((path / "files" / f"payload{phase}").read_bytes(), data)
                with self.history() as history:
                    self.assertTrue(all(job["state"] != "verified" for job in history.jobs()))

    def test_download_mismatch_is_pending_never_verified_and_keeps_files(self):
        self.corrupt = True
        outcome = self.execute()
        self.assertEqual((outcome.receipt["batch_state"], outcome.receipt["files_verified_now"]), ("pending", 0))
        self.assertTrue((self.batch / "files/nested/雪.txt").exists())
        with self.history() as history:
            self.assertTrue(all(job["state"] == "retry" for job in history.jobs()))
            self.assertTrue(all(event["state"] != "verified" for job in history.jobs() for event in history.events(job["id"])))

    def test_extra_config_or_wrong_folder_or_generation_refused_before_provider(self):
        original = self.generation.config_path.read_bytes()
        for raw in (original + b"token_url = https://private.invalid\n",
                    config({**SETTINGS, "root_folder_id": "WrongFolder123"})):
            self.generation.config_path.write_bytes(raw)
            with self.assertRaises(upload.GoogleUploadError):
                self.execute()
            self.assertEqual(self.calls, [])
        self.generation.config_path.write_bytes(original)
        changed = copy.deepcopy(self.value)
        changed["test"]["generation_id"] = "9" * 32
        with self.assertRaises(upload.GoogleUploadError):
            self.execute(value=changed)

    def test_settings_tamper_during_command_cannot_record_verified(self):
        def tamper(runner):
            tester._write_config(runner.config, {**SETTINGS, "root_folder_id": "WrongFolder123"}, TOKEN)
        self.tamper = tamper
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        with self.history() as history:
            self.assertTrue(all(job["state"] != "verified" for job in history.jobs()))

    def test_unadmitted_pending_root_is_not_read_rebound_or_uploaded(self):
        from raspi_cloud_sync_pro.staging import ReadyBatch
        with self.history() as history:
            history.enqueue(ReadyBatch.load(self.batch), self.generation.destination_id, 1000)
        identity = "7" * 32
        self.make_batch(identity, {"empty": b"", "nested/雪.txt": b"synthetic content"})
        (self.batch / "files/nested/雪.txt").write_bytes(b"UNADMITTED ROOT MUST NOT BE READ")
        outcome = self.execute(batch_id=identity)
        self.assertEqual((outcome.receipt["batch_state"], outcome.receipt["files_pending"]), ("pending", 2))
        self.assertEqual(self.calls, [])
        with self.history() as history:
            self.assertTrue(all(job["batch_root"] == str(self.batch) and job["attempts"] == 0 for job in history.jobs()))

    def test_verified_content_deduplicates_across_new_snapshot_without_new_network(self):
        self.execute()
        identity = "7" * 32
        self.make_batch(identity, {"empty": b"", "nested/雪.txt": b"synthetic content"})
        self.calls.clear()
        outcome = self.execute(batch_id=identity)
        self.assertEqual((outcome.receipt["batch_state"], outcome.receipt["files_previously_verified"]), ("historical", 2))
        self.assertEqual(self.calls, [])

    def test_manifest_digest_content_links_and_extra_files_refuse_before_provider(self):
        with self.assertRaises(upload.GoogleUploadError):
            self.execute(digest="0" * 64)
        extra = self.batch / "files/extra"
        extra.write_bytes(b"unexpected")
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        extra.unlink()
        target = self.batch / "files/empty"
        target.unlink()
        target.symlink_to(self.batch / "files/nested/雪.txt")
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_batch_change_during_upload_is_detected_without_success(self):
        self.tamper = lambda runner: (self.batch / "files/empty").write_bytes(b"changed")
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        self.assertEqual([args[0] for args in self.calls], ["copyto"])

    def test_generation_path_swap_is_refused_even_with_identical_provenance(self):
        path = self.generation.config_path.parent.parent
        retained = self.operational / "retained-original"
        def swap(runner):
            path.rename(retained)
            shutil.copytree(retained, path)
        self.tamper = swap
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        self.assertTrue(retained.is_dir() and path.is_dir())
        self.assertEqual([args[0] for args in self.calls], ["copyto"])

    def test_history_alias_and_public_mode_are_rejected_before_open_or_chmod(self):
        path = self.generation.config_path.parent.parent / upload._HISTORY
        outside = self.root / "do-not-change"
        outside.write_bytes(b"ORIGINAL")
        path.symlink_to(outside)
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        path.unlink()
        path.write_bytes(b"")
        path.chmod(0o644)
        with self.assertRaises(upload.GoogleUploadError):
            self.execute()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(outside.read_bytes(), b"ORIGINAL")

    def test_existing_operational_lock_refuses_without_provider_or_new_generation(self):
        with destination._private_directory(self.operational) as root, tester._lock(root):
            with self.assertRaises(upload.GoogleUploadError):
                self.execute()
        self.assertEqual(self.calls, [])
        self.assertEqual(len([path for path in self.operational.iterdir() if path.is_dir()]), 1)

    def test_actual_runner_and_generic_provider_execution_are_never_used_by_mock(self):
        with mock.patch.object(upload.RcloneProvider, "_run", side_effect=AssertionError("must use clean Runner")), \
                mock.patch.object(tester._TestProvider, "verify", side_effect=AssertionError("no forced refresh")):
            self.assertEqual(self.execute().receipt["files_verified_now"], 2)

    def test_invalid_timeout_refuses_before_generation_or_staging_access(self):
        with mock.patch.object(operational, "inspect_google_operational_generation", side_effect=AssertionError("no credentials")):
            for timeout in (0, True, 301, float("nan")):
                with self.subTest(timeout=timeout), self.assertRaises(upload.GoogleUploadError):
                    self.execute(timeout=timeout)

    def test_python39_grammar(self):
        ast.parse(Path(upload.__file__).read_text(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
