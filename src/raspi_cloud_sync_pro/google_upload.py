"""Private, individually admitted uploads of existing ready batches.

Missing release refuses before staging or credential reads. A release is root
authority, never proof of confinement: a future coordinator must first verify
the actual live worker/policy/canaries and independently obtain a fresh source
Enable admission for THIS selected canonical batch. This library has no public
activation, network-policy bypass, scheduler or provider/account setup.

Already admitted work can finish; source Disable denies subsequent admissions.
Stopping an admitted worker remains the coordinator's responsibility. Outcomes
are private; projection() never asserts global enablement or account readiness.
"""

import hashlib
import hmac
import json
import math
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from . import batch_transfer as batches
from . import google_control as control
from . import google_destination as destination
from . import google_operational as operational
from . import google_test as tester
from .engine import BackupEngine
from .errors import BackupError
from .history import History
from .network import Connectivity
from .providers import RcloneProvider
from .staging import ReadyBatch

_ADMISSION_FIELDS = {"schema_version", "provider", "admission_authorized", "control_request_id",
                     "preparation", "test", "checked_at", "single_admission"}
_GATE_FIELDS = {"schema_version", "provider", "purpose", "control_request_id", "admission_sha256",
                "batch_id", "manifest_sha256", "worker_pid", "expires_at"}
_PURPOSE = "google_batch_upload_wifi_egress"
_HISTORY = "backup-history.sqlite3"
_FAILURES = (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError,
             sqlite3.Error)


class GoogleUploadError(BackupError):
    """Fixed codes only, without private values or exception context."""


def _fail(code):
    raise GoogleUploadError(code) from None


def _admission(value, now):
    raw = destination._canonical(value)
    if len(raw) > 16384:
        _fail("google_upload_admission_invalid")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != _ADMISSION_FIELDS
            or not control.setup._valid_header(value)
            or value["admission_authorized"] is not True or value["single_admission"] is not True
            or not control._id(value["control_request_id"])
            or not control._preparation_valid(value["preparation"])
            or not control._test_valid(value["test"])
            or not control.setup._timestamp(value["checked_at"])
            or not value["checked_at"] <= now < value["checked_at"] + 30
            or value["test"]["tested_at"] > value["checked_at"]):
        _fail("google_upload_admission_invalid")
    return value, hashlib.sha256(raw).hexdigest()


def _gate(fd, admission, batch_id, manifest_sha256):
    if fd is None:
        _fail("google_upload_network_disabled")
    try:
        with destination._channel(fd, writing=False) as channel:
            tester._root_peer(channel)
            value = destination._read_frame(channel, destination._deadline(5))
            current = tester._clock()  # Actual wall time after the complete frame and EOF.
            admission, digest = _admission(admission, current)
            if (not control._id(batch_id) or not control._hash(manifest_sha256)
                    or not isinstance(value, dict) or set(value) != _GATE_FIELDS
                    or not control.setup._valid_header(value) or value["purpose"] != _PURPOSE
                    or value["control_request_id"] != admission["control_request_id"]
                    or value["batch_id"] != batch_id or value["manifest_sha256"] != manifest_sha256
                    or value["admission_sha256"] != digest
                    or type(value["worker_pid"]) is not int or value["worker_pid"] != os.getpid()
                    or not control.setup._timestamp(value["expires_at"])
                    or not current < value["expires_at"] <= current + 30):
                _fail("google_upload_gate_invalid")
            return admission
    except GoogleUploadError:
        raise
    except (BackupError,) + _FAILURES:
        _fail("google_upload_gate_invalid")


def _private_history(root):
    # History's common implementation creates its own DB/worker lock. Reject
    # aliases/insecure pre-existing files BEFORE it can open or chmod them.
    for name in (_HISTORY, _HISTORY + ".worker.lock", _HISTORY + "-journal",
                 _HISTORY + "-wal", _HISTORY + "-shm"):
        try:
            info = os.stat(name, dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            _fail("google_upload_history_invalid")


def _selected_jobs(history, batch, destination_id):
    result = {}
    for item in batch.files:
        row = history.connection.execute(
            "SELECT size,state,batch_root FROM jobs WHERE destination=? AND relative=? AND sha256=?",
            (destination_id, item.relative, item.sha256)).fetchone()
        if row is not None:
            result[(item.relative, item.sha256)] = dict(row)
    return result


class _UploadProvider(RcloneProvider):
    def __init__(self, generation, runner, expected, recheck):
        super().__init__("google_drive_rclone", destination.REMOTE_NAME + ":", generation.config_path,
                         enabled=True, executable=runner.executable)
        self.destination_id = generation.destination_id
        self.runner, self.expected, self.recheck = runner, expected, recheck

    def _run(self, arguments):
        # Never call the generic adapter's environment-inheriting _run, or the
        # Test adapter's forced token expiry/refresh operation.
        self.recheck()
        tester.validate_google_operational_config(tester._read_config(self.config), self.expected)
        self.runner.run(arguments)
        tester.validate_google_operational_config(tester._read_config(self.config), self.expected)
        self.recheck()

    def upload(self, batch, item):
        return super().upload(batch, item, check=self.runner.check)


@dataclass(frozen=True, repr=False)
class GoogleUploadOutcome:
    receipt: dict

    def __repr__(self):
        return "<GoogleUploadOutcome private>"

    def projection(self):
        value = self.receipt
        state = value.get("batch_state")
        state = state if state in ("verified", "historical", "pending", "failed") else "failed"
        def count(name):
            value = self.receipt.get(name)
            return value if type(value) is int and 0 <= value <= batches.MAX_FILES else 0
        return {"schema_version": 1, "provider": "google_drive", "batch_state": state,
                "files_verified_now": count("files_verified_now"),
                "files_previously_verified": count("files_previously_verified"),
                "files_pending": count("files_pending"), "files_failed": count("files_failed"),
                "current_worker_state_checked": False, "account_verified": False,
                "automatic_deletion": False}


def run_google_batch_upload(prepared_state, operational_state, staging, batch_id,
                            expected_manifest_sha256, admission, *, root_gate_fd=None,
                            executable=None, executable_sha256=None, timeout=300):
    """One selected batch under a distinct consumed root release and Enable admission.

    admission is the exact independently exported source frame, not a browser
    decision. Neither that dict nor an old successful Test authorizes this call
    without its PID/batch/admission-bound root stream. Only a future confined
    executor/coordinator may release it after its fresh policy/canary checks.
    Paths are private service configuration, never browser parameters. A web
    bridge must complete final-frame/EOF verification AND both child exits first.
    Python hashing loops check the deadline per chunk; subprocesses share that
    deadline. Blocking kernel filesystem operations have no hard wall-clock bound.
    """
    try:
        admission = _gate(root_gate_fd, admission, batch_id, expected_manifest_sha256)
        operational._ordinary_identity()
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300
                or executable is None or not control._hash(executable_sha256)):
            _fail("google_upload_request_invalid")
        deadline = time.monotonic() + timeout
        runner = tester._Runner(executable, executable_sha256, deadline, Path("/nonexistent"))
        prepared, operational_state = operational._paths(prepared_state, operational_state)
        staging = operational._path(staging)
        if any(staging == path or staging in path.parents or path in staging.parents
               for path in (prepared, operational_state)):
            _fail("google_upload_path_invalid")
        preparation, tested = admission["preparation"], admission["test"]
        with destination._private_directory(staging) as staging_fd, \
                batches._batch(staging_fd, batch_id + ".ready") as (batch_fd, files_fd):
            manifest, _, digest, _, original, manifest_identity = batches._read_manifest(batch_fd)
            if digest != expected_manifest_sha256 or not manifest["files"]:
                _fail("google_upload_batch_invalid")
            inventory = batches._inventory(batch_fd, files_fd, manifest)
            for item in manifest["files"]:
                batches._hash_file(files_fd, item, deadline)

            def inspect():
                return operational.inspect_google_operational_generation(prepared, operational_state,
                    tested["generation_id"], preparation["request_id"], preparation["folder_name"],
                    preparation["authorization_sha256"], preparation["rclone_config_sha256"])

            def batch_recheck():
                runner.check()
                batches._recheck_anchor(staging, staging_fd)
                if (batches._read_manifest(batch_fd)[4:] != (original, manifest_identity)
                        or batches._inventory(batch_fd, files_fd, manifest) != inventory
                        or batches._identity(os.stat(batch_id + ".ready", dir_fd=staging_fd,
                                                    follow_symlinks=False)) != inventory[("batch", "")]):
                    _fail("google_upload_batch_changed")

            with destination._private_directory(prepared) as prepared_fd, \
                    destination._private_directory(operational_state) as root, tester._lock(root):
                if len({operational._inode(os.fstat(fd)) for fd in (prepared_fd, root, staging_fd)}) != 3:
                    _fail("google_upload_path_invalid")
                generation = inspect()
                with destination._private_directory(generation.config_path.parent.parent) as generation_fd:
                    seed = tester._parse(tester._read_config(prepared / destination.PREPARATION_DIRECTORY / "rclone.conf"),
                                         root_required=False)
                    expected = {**seed.settings, "root_folder_id": tested["folder_id"]}
                    if not hmac.compare_digest(control.google_operational_settings_sha256(expected),
                                               tested["operational_settings_sha256"]):
                        _fail("google_upload_settings_changed")

                    def recheck():
                        batch_recheck()
                        operational._still_named(prepared, prepared_fd)
                        operational._still_named(operational_state, root)
                        if (operational._inode(os.stat(tested["generation_id"], dir_fd=root, follow_symlinks=False))
                                != operational._inode(os.fstat(generation_fd))):
                            _fail("google_upload_generation_changed")
                        current = inspect()
                        if current != generation:
                            _fail("google_upload_generation_changed")
                        parsed = tester.validate_google_operational_config(tester._read_config(generation.config_path), expected)
                        if parsed.settings_sha256 != tested["operational_settings_sha256"]:
                            _fail("google_upload_settings_changed")

                    recheck()
                    runner.config = generation.config_path
                    provider = _UploadProvider(generation, runner, expected, recheck)
                    _private_history(generation_fd)
                    path = generation.config_path.parent.parent / _HISTORY
                    with History(path) as history:
                        _private_history(generation_fd)
                        batch = ReadyBatch.load(staging / (batch_id + ".ready"), check=runner.check)
                        before = _selected_jobs(history, batch, generation.destination_id)
                        history.enqueue(batch, generation.destination_id, tester._clock(), check=runner.check)
                        verified_now = 0
                        for _ in batch.files:
                            recheck()
                            state = BackupEngine(history).run_once(provider, connectivity=Connectivity.ONLINE,
                                                                  batch_root=batch.root, check=runner.check)
                            if state is None:
                                break
                            if state == "verified":
                                verified_now += 1
                        recheck()
                        rows = _selected_jobs(history, batch, generation.destination_id)
                        previous = pending = failed = 0
                        for item in batch.files:
                            key = item.relative, item.sha256
                            row = rows[key]
                            if row["size"] != item.size:
                                _fail("google_upload_history_invalid")
                            if row["state"] == "verified":
                                if before.get(key, {}).get("state") == "verified":
                                    previous += 1
                            elif row["state"] == "failed":
                                failed += 1
                            else:
                                pending += 1
                    state = "failed" if failed else "pending" if pending else "verified" if verified_now else "historical"
                    receipt = {"schema_version": 1, "provider": "google_drive", "batch_state": state,
                        "batch_id": batch_id, "manifest_sha256": expected_manifest_sha256,
                        "control_request_id": admission["control_request_id"], "preparation": preparation,
                        "test": tested, "destination_id": generation.destination_id, "completed_at": tester._clock(),
                        "files_verified_now": verified_now, "files_previously_verified": previous,
                        "files_pending": pending, "files_failed": failed, "automatic_deletion": False}
                    return GoogleUploadOutcome(receipt)
    except GoogleUploadError:
        raise
    except (BackupError,) + _FAILURES:
        _fail("google_upload_failed")
