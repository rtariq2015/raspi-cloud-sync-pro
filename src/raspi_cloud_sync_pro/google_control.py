"""Durable owner controls and private, local-only Google coordination records.

HTTP callers must validate CSRF before constructing OwnerProof; commit_guard
must recheck the live owner/session under auth.lock, as in the dashboard's OAuth
commit guard. Requests never accept a browser path, digest, generation or folder
ID. This module makes no network, subprocess, provider or worker operations.

Lock order is auth.lock then google-control/workflow.lock. Preparation's lock is
acquired only underneath these; existing setup never acquires the control lock.
Receipts are historical reports from a separately reviewed private coordinator,
not independent cloud observations by this module. Test never enables anything.
A recorded Enable permits fresh individual admission checks while its owner and
source binding remain current. Disable supersedes it immediately. No record here
asserts that a worker is currently running or identifies the Google account.
"""

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager

from . import google_destination as destination
from . import google_setup as setup
from .errors import BackupError
from .oauth import OwnerProof

WORKFLOW_DIRECTORY = "google-control"
REQUEST_LIFETIME = 600.0
TEST_FRESHNESS = 600.0
DEFAULT_TIMEOUT = 5.0
PENDING_STATES = frozenset(("test_requested", "enable_requested", "disable_requested"))
CONTROL_STATES = PENDING_STATES | frozenset((
    "not_requested", "test_verified", "test_failed", "test_expired", "enable_recorded",
    "disable_recorded", "enable_failed", "disable_failed", "expired", "stale_binding",
    "invalid", "unavailable"))
_ACTIONS = {"test", "enable", "disable"}
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FOLDER = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_REQUEST_FIELDS = {"schema_version", "provider", "request_id", "action", "requested_at",
                   "expires_at", "owner_username", "owner_credential_tag", "preparation", "test"}
_PREPARATION_FIELDS = {"request_id", "folder_name", "authorization_sha256", "rclone_config_sha256"}
_TEST_FIELDS = {"request_id", "generation_id", "folder_id", "tested_at", "receipt_sha256",
                "operational_settings_sha256"}
_RESULT_FIELDS = {"generation_id", "folder_id", "operational_config_sha256",
                  "operational_settings_sha256", "fixture_sha256", "fixture_size",
                  "fixture_uploaded", "fixture_readback_verified", "token_refresh_verified"}
_RECEIPT_FIELDS = {"schema_version", "provider", "request_id", "action", "preparation", "test",
                   "completed_at", "outcome", "result", "enabled", "automatic_deletion"}
_FAILURES = setup._FAILURES


class GoogleControlError(destination.GoogleDestinationError):
    """Fixed public codes only; never include raw state, paths or credentials."""


def _fail(code):
    raise GoogleControlError(code) from None


def _now(value):
    if value is None:
        return setup._now(None)
    if not setup._timestamp(value):
        _fail("google_control_time_invalid")
    return value


def _id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _folder(value):
    return (isinstance(value, str) and _FOLDER.fullmatch(value) is not None
            and value not in ("root", "appDataFolder"))


def google_test_fixture(request_id):
    """Exact small fixture bytes for one private Test intent, never user data."""
    if not _id(request_id):
        _fail("google_control_request_invalid")
    return b"raspi-cloud-sync-pro Google Test v1\n" + request_id.encode("ascii") + b"\n"


def google_operational_settings_sha256(settings):
    """Hash exact validated direct-drive settings, excluding mutable token bytes.

    A tester must extract ALL non-token options from its actual config and reject
    extra sections/options. Do not construct this object from expected values
    while ignoring a different actual backend/client/scope/folder configuration.
    This helper alone does not parse a config, validate tokens or authorize use.
    """
    if (not isinstance(settings, dict) or set(settings) != {
            "remote_name", "type", "scope", "client_id", "client_secret", "root_folder_id"}
            or settings["remote_name"] != destination.REMOTE_NAME
            or settings["type"] != "drive" or settings["scope"] != "drive.file"
            or not destination._client_value(settings["client_id"])
            or not destination._client_value(settings["client_secret"])
            or not _folder(settings["root_folder_id"])):
        _fail("google_control_settings_invalid")
    return hashlib.sha256(destination._canonical(settings)).hexdigest()


def _settings(payload, folder_id):
    authorization = destination._validate_bundle(json.loads(payload))
    return google_operational_settings_sha256({
        "remote_name": destination.REMOTE_NAME, "type": "drive", "scope": "drive.file",
        **authorization.client, "root_folder_id": folder_id})


@contextmanager
def _workflow(root, *, create=False):
    if create:
        try:
            os.mkdir(WORKFLOW_DIRECTORY, mode=0o700, dir_fd=root)
        except FileExistsError:
            pass
        else:
            os.fsync(root)
    try:
        fd = os.open(WORKFLOW_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    except FileNotFoundError:
        if create:
            raise
        yield None
        return
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or (info.st_mode & 0o7777) != 0o700:
            _fail("google_control_storage_invalid")
        with setup._lock(fd, "workflow.lock", busy_code="google_control_busy"):
            yield fd
    finally:
        os.close(fd)


def _preparation_valid(value):
    if (not isinstance(value, dict) or set(value) != _PREPARATION_FIELDS
            or not _id(value["request_id"]) or not _hash(value["authorization_sha256"])
            or not _hash(value["rclone_config_sha256"])):
        return False
    destination._folder_name(value["folder_name"])
    return True


def _test_valid(value):
    return (isinstance(value, dict) and set(value) == _TEST_FIELDS
            and _id(value["request_id"]) and _id(value["generation_id"])
            and _folder(value["folder_id"]) and setup._timestamp(value["tested_at"])
            and _hash(value["receipt_sha256"]) and _hash(value["operational_settings_sha256"]))


def _validate_request(record, request_id):
    if (not setup._valid_header(record) or set(record) != _REQUEST_FIELDS
            or record["request_id"] != request_id or not _id(request_id)
            or record["action"] not in _ACTIONS
            or not isinstance(record["owner_username"], str)
            or setup._USERNAME.fullmatch(record["owner_username"]) is None
            or not _hash(record["owner_credential_tag"])
            or not setup._timestamp(record["requested_at"])
            or not setup._timestamp(record["expires_at"])
            or record["expires_at"] != record["requested_at"] + REQUEST_LIFETIME
            or (record["preparation"] is not None if record["action"] == "disable"
                else not _preparation_valid(record["preparation"]))
            or (not _test_valid(record["test"]) if record["action"] == "enable"
                else record["test"] is not None)):
        _fail("google_control_storage_invalid")
    return record


def _current(workflow):
    if workflow is None:
        return None
    pointer = setup._read_record(workflow, "current.json")
    if pointer is None:
        return None
    if (not isinstance(pointer, dict) or set(pointer) != {"schema_version", "request_id"}
            or type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1
            or not _id(pointer["request_id"])):
        _fail("google_control_storage_invalid")
    return _validate_request(setup._read_record(workflow, pointer["request_id"] + ".request.json"),
                             pointer["request_id"])


def _owner(root, username, tag):
    try:
        current = setup._owner_tag(root, username)
        if not hmac.compare_digest(current, tag):
            _fail("google_control_owner_changed")
        return current
    except setup.GoogleSetupError:
        _fail("google_control_owner_changed")


def _preparation(root, origin, username, tag, now, *, read_only=False):
    with (_existing_workflow(root, setup.WORKFLOW_DIRECTORY) if read_only else setup._workflow(root)) as workflow:
        record = setup._current(workflow) if workflow is not None else None
        if record is None:
            _fail("google_control_preparation_required")
        receipt = setup._receipt(workflow, record, now)
        if receipt is None:
            _fail("google_control_preparation_required")
        if record["owner_username"] != username or record["owner_credential_tag"] != tag:
            _fail("google_control_owner_changed")
        try:
            payload, config_digest = setup._binding(root, origin, record)
        except setup.GoogleSetupError:
            _fail("google_control_source_changed")
        if not hmac.compare_digest(receipt["rclone_config_sha256"], config_digest):
            _fail("google_control_source_changed")
        # A completed matching receipt survives the old preparation-intent TTL.
        return ({"request_id": record["request_id"], "folder_name": record["folder_name"],
                 "authorization_sha256": record["authorization_sha256"],
                 "rclone_config_sha256": config_digest}, payload)


def _validate_receipt(receipt, record, now, payload):
    if (not setup._valid_header(receipt) or set(receipt) != _RECEIPT_FIELDS
            or any(receipt[key] != record[key] for key in ("request_id", "action", "preparation", "test"))
            or not setup._timestamp(receipt["completed_at"])
            or not record["requested_at"] <= receipt["completed_at"] < record["expires_at"]
            or receipt["completed_at"] > now
            or receipt["automatic_deletion"] is not False):
        _fail("google_control_receipt_invalid")
    action, outcome, result = record["action"], receipt["outcome"], receipt["result"]
    if action != "test":
        if (outcome not in ("applied", "failed") or result is not None
                or receipt["enabled"] is not (action == "enable" and outcome == "applied")):
            _fail("google_control_receipt_invalid")
        return receipt
    fixture = google_test_fixture(record["request_id"])
    if (outcome not in ("verified", "failed") or receipt["enabled"] is not False
            or not isinstance(result, dict) or set(result) != _RESULT_FIELDS
            or result["fixture_sha256"] != hashlib.sha256(fixture).hexdigest()
            or type(result["fixture_size"]) is not int or result["fixture_size"] != len(fixture)
            or any(type(result[key]) is not bool for key in
                   ("fixture_uploaded", "fixture_readback_verified", "token_refresh_verified"))
            or (result["generation_id"] is not None and not _id(result["generation_id"]))
            or (result["folder_id"] is not None and not _folder(result["folder_id"]))
            or any(result[key] is not None and not _hash(result[key]) for key in
                   ("operational_config_sha256", "operational_settings_sha256"))):
        _fail("google_control_receipt_invalid")
    if result["operational_settings_sha256"] is not None:
        if (result["folder_id"] is None or payload is None
                or not hmac.compare_digest(result["operational_settings_sha256"],
                                           _settings(payload, result["folder_id"]))):
            _fail("google_control_receipt_invalid")
    if outcome == "verified":
        if (any(result[key] is None for key in ("generation_id", "folder_id",
                "operational_config_sha256", "operational_settings_sha256"))
                or not all(result[key] for key in
                           ("fixture_uploaded", "fixture_readback_verified", "token_refresh_verified"))):
            _fail("google_control_receipt_invalid")
    elif result["fixture_readback_verified"]:
        _fail("google_control_receipt_invalid")
    return receipt


def _receipt(workflow, record, now, payload):
    receipt = setup._read_record(workflow, record["request_id"] + ".receipt.json")
    return None if receipt is None else _validate_receipt(receipt, record, now, payload)


def _test_binding(record, receipt):
    result = receipt["result"]
    return {"request_id": record["request_id"], "generation_id": result["generation_id"],
            "folder_id": result["folder_id"], "tested_at": receipt["completed_at"],
            "receipt_sha256": hashlib.sha256(destination._canonical(receipt)).hexdigest(),
            "operational_settings_sha256": result["operational_settings_sha256"]}


def _successful_test(workflow, record, now, payload, *, fresh):
    binding = record["test"]
    tested = _validate_request(setup._read_record(workflow, binding["request_id"] + ".request.json"),
                               binding["request_id"])
    if (tested["action"] != "test" or any(tested[key] != record[key] for key in
            ("owner_username", "owner_credential_tag", "preparation"))):
        _fail("google_control_test_required")
    receipt = _receipt(workflow, tested, now, payload)
    if (receipt is None or receipt["outcome"] != "verified"
            or _test_binding(tested, receipt) != binding
            or (fresh and now >= receipt["completed_at"] + TEST_FRESHNESS)):
        _fail("google_control_test_required")
    return binding


def _authority(root, workflow, origin, record, now, *, fresh_test=False, read_only=False):
    _owner(root, record["owner_username"], record["owner_credential_tag"])
    if record["action"] == "disable":
        return None
    binding, payload = _preparation(root, origin, record["owner_username"],
                                    record["owner_credential_tag"], now, read_only=read_only)
    if binding != record["preparation"]:
        _fail("google_control_source_changed")
    if record["action"] == "enable":
        _successful_test(workflow, record, now, payload, fresh=fresh_test)
    return payload


def _projection(state, record=None, receipt=None, now=None, *, admitted=False):
    return {"schema_version": 1, "provider": "google_drive", "control_state": state,
            "requested_action": None if record is None else record["action"],
            "age_seconds": None if record is None else max(0.0, now - (
                receipt["completed_at"] if receipt else record["requested_at"])),
            "historical": receipt is not None, "admission_authorized": admitted,
            "current_worker_state_checked": False, "account_verified": False,
            "automatic_deletion": False}


def _status(root, workflow, origin, now, record=None, *, read_only=False):
    record = _current(workflow) if record is None else record
    if record is None:
        return _projection("not_requested")
    try:
        payload = _authority(root, workflow, origin, record, now, read_only=read_only)
    except GoogleControlError as error:
        if read_only and error.code == "google_control_busy":
            raise
        return _projection("stale_binding", record, now=now)
    receipt = _receipt(workflow, record, now, payload)
    action = record["action"]
    if receipt is not None:
        if receipt["outcome"] == "failed":
            return _projection(action + "_failed", record, receipt, now)
        if action == "test":
            state = "test_expired" if now >= receipt["completed_at"] + TEST_FRESHNESS else "test_verified"
            return _projection(state, record, receipt, now)
        return _projection(action + "_recorded", record, receipt, now,
                           admitted=action == "enable")
    return _projection("expired" if now >= record["expires_at"] else action + "_requested",
                       record, now=now)


def _proof(proof, commit_guard):
    if (not isinstance(proof, OwnerProof) or not isinstance(proof.username, str)
            or setup._USERNAME.fullmatch(proof.username) is None
            or not _hash(proof.credential_tag) or not callable(commit_guard)):
        _fail("google_control_owner_changed")


def _request_locked(root, origin, action, proof, now):
    _owner(root, proof.username, proof.credential_tag)
    with _workflow(root, create=True) as workflow:
        preparation, payload = (None, None) if action == "disable" else _preparation(
            root, origin, proof.username, proof.credential_tag, now)
        current = _current(workflow)
        same = (current is not None and current["action"] == action
                and current["owner_username"] == proof.username
                and current["owner_credential_tag"] == proof.credential_tag
                and current["preparation"] == preparation)
        if same:
            prior = _receipt(workflow, current, now, payload)
            if (prior is not None and prior["outcome"] != "failed"
                    and (action != "test"
                         or now < prior["completed_at"] + TEST_FRESHNESS)):
                return _status(root, workflow, origin, now, current)
            if prior is None and now < current["expires_at"]:
                _authority(root, workflow, origin, current, now, fresh_test=action == "enable")
                return _status(root, workflow, origin, now, current)
        test = None
        if action == "enable":
            if current is None:
                _fail("google_control_test_required")
            if current["action"] == "test":
                _authority(root, workflow, origin, current, now)
                receipt = _receipt(workflow, current, now, payload)
                if (receipt is None or receipt["outcome"] != "verified"
                        or now >= receipt["completed_at"] + TEST_FRESHNESS):
                    _fail("google_control_test_required")
                test = _test_binding(current, receipt)
            elif same:
                test = _successful_test(workflow, current, now, payload, fresh=True)
            else:
                _fail("google_control_test_required")
        request_id = secrets.token_hex(16)
        record = {"schema_version": 1, "provider": "google_drive",
                  "request_id": request_id, "action": action,
                  "requested_at": now, "expires_at": now + REQUEST_LIFETIME,
                  "owner_username": proof.username,
                  "owner_credential_tag": proof.credential_tag,
                  "preparation": preparation, "test": test}
        setup._publish_record(workflow, request_id + ".request.json", record)
        setup._publish_pointer(workflow, request_id)
        return _projection(action + "_requested", record, now=now)


def request_google_control(state, origin, action, proof, commit_guard, *, now=None):
    """Commit one CSRF-checked live owner's intent, never a cloud operation."""
    try:
        destination._unprivileged()
        now = _now(now)
        if not isinstance(action, str) or action not in _ACTIONS:
            _fail("google_control_action_invalid")
        _proof(proof, commit_guard)
        with destination._private_directory(state) as root:
            deferred = None
            try:
                with commit_guard(proof):
                    try:
                        result = _request_locked(root, origin, action, proof, now)
                    except GoogleControlError as error:
                        deferred = error
                    except (destination.GoogleDestinationError,) + _FAILURES:
                        deferred = GoogleControlError("google_control_unavailable")
                # The existing OAuth commit guard wraps BackupError raised
                # through its yield as session revocation. Keep workflow errors
                # until after that guard releases auth.lock, retaining their
                # own fixed code without weakening the actual owner check.
            except (BackupError,) + _FAILURES:
                _fail("google_control_owner_changed")
            if deferred is not None:
                raise deferred from None
            return result
    except GoogleControlError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_control_unavailable")


def google_control_status(state, origin, *, now=None):
    """Describe source intent and historical reports; never observed worker state."""
    try:
        destination._unprivileged()
        now = _now(now)
        with destination._private_directory(state) as root:
            with setup._lock(root, "auth.lock", busy_code="google_control_busy"):
                with _workflow(root) as workflow:
                    return _status(root, workflow, origin, now)
    except GoogleControlError as error:
        invalid = {"google_control_storage_invalid", "google_control_receipt_invalid",
                   "google_control_request_invalid", "google_control_time_invalid"}
        return _projection("invalid" if error.code in invalid else "unavailable")
    except (destination.GoogleDestinationError,) + _FAILURES:
        return _projection("unavailable")


@contextmanager
def _existing_shared_lock(parent, name):
    """Read existing locks without creating missing state during a boot check."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            _fail("google_control_storage_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            _fail("google_control_busy")
        yield
    finally:
        os.close(fd)


@contextmanager
def _existing_workflow(root, name):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    except FileNotFoundError:
        yield None
        return
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            _fail("google_control_storage_invalid")
        with _existing_shared_lock(fd, "workflow.lock"):
            yield fd
    finally:
        os.close(fd)


def export_google_control_pending(state, origin, output_fd, *, now=None):
    """Export eligibility metadata only; never request, record or authorize work.

    A root runner may use this to skip completed intents, then must call the
    existing broker which independently exports and validates current authority.
    Missing/busy locks are unavailable; this operation creates no lock files.
    """
    destination._unprivileged()
    with destination._channel(output_fd, writing=True) as channel:
        deadline, checked = destination._deadline(DEFAULT_TIMEOUT), _now(now)
        destination._origin(origin)
        try:
            with destination._private_directory(state) as root, _existing_shared_lock(root, "auth.lock"):
                try:
                    workflow = os.open(WORKFLOW_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                except FileNotFoundError:
                    report = _projection("not_requested")
                else:
                    try:
                        info = os.fstat(workflow)
                        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                            _fail("google_control_storage_invalid")
                        with _existing_shared_lock(workflow, "workflow.lock"):
                            report = _status(root, workflow, origin, checked, read_only=True)
                    finally:
                        os.close(workflow)
        except GoogleControlError as error:
            invalid = {"google_control_storage_invalid", "google_control_receipt_invalid",
                       "google_control_request_invalid", "google_control_time_invalid"}
            report = _projection("invalid" if error.code in invalid else "unavailable")
        except (destination.GoogleDestinationError,) + _FAILURES:
            report = _projection("unavailable")
        current = report["control_state"]
        if current not in CONTROL_STATES:
            _fail("google_control_pending_invalid")
        pending = current in PENDING_STATES
        if pending and (report["historical"] is not False
                        or report["requested_action"] != current.removesuffix("_requested")):
            _fail("google_control_pending_invalid")
        destination._write_frame(channel, destination._canonical({
            "schema_version": 1, "provider": "google_drive", "control_state": current,
            "pending": pending, "checked_at": checked}), deadline)
    return {"schema_version": 1, "provider": "google_drive", "pending_exported": True,
            "current_worker_state_checked": False, "account_verified": False, "automatic_deletion": False}


def _live(workflow, request_id, now):
    record = _current(workflow)
    if record is None or record["request_id"] != request_id:
        _fail("google_control_request_not_current")
    if now < record["requested_at"]:
        _fail("google_control_time_invalid")
    if now >= record["expires_at"]:
        _fail("google_control_request_expired")
    return record


def export_requested_google_control(state, origin, request_id, output_fd, *, now=None):
    """Consume one private output FD and export current uncompleted authority."""
    try:
        destination._unprivileged()
        with destination._channel(output_fd, writing=True) as channel:
            deadline = destination._deadline(DEFAULT_TIMEOUT)
            now = _now(now)
            if not _id(request_id):
                _fail("google_control_request_invalid")
            with destination._private_directory(state) as root:
                with setup._lock(root, "auth.lock", busy_code="google_control_busy"):
                    with _workflow(root) as workflow:
                        record = _live(workflow, request_id, now)
                        payload = _authority(root, workflow, origin, record, now, fresh_test=True)
                        if _receipt(workflow, record, now, payload) is not None:
                            _fail("google_control_already_completed")
                        fixture = google_test_fixture(request_id) if record["action"] == "test" else None
                        wire = {key: record[key] for key in ("schema_version", "provider", "request_id",
                                "action", "requested_at", "expires_at", "preparation", "test")}
                        wire["fixture"] = None if fixture is None else {
                            "sha256": hashlib.sha256(fixture).hexdigest(), "size": len(fixture)}
                        destination._write_frame(channel, destination._canonical(wire), deadline)
        return {"schema_version": 1, "provider": "google_drive", "control_exported": True,
                "current_worker_state_checked": False}
    except GoogleControlError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_control_export_failed")


def record_google_control_receipt(state, origin, request_id, receipt_fd, *, now=None):
    """Consume a private historical report, recheck authority and never replace it."""
    try:
        destination._unprivileged()
        with destination._channel(receipt_fd, writing=False) as channel:
            deadline = destination._deadline(DEFAULT_TIMEOUT)
            _now(now)
            if not _id(request_id):
                _fail("google_control_request_invalid")
            receipt = destination._read_frame(channel, deadline)
            now = _now(now)  # Real wall clock again after bounded frame ingestion.
            with destination._private_directory(state) as root:
                with setup._lock(root, "auth.lock", busy_code="google_control_busy"):
                    with _workflow(root) as workflow:
                        record = _live(workflow, request_id, now)
                        payload = _authority(root, workflow, origin, record, now, fresh_test=True)
                        _validate_receipt(receipt, record, now, payload)
                        prior = _receipt(workflow, record, now, payload)
                        if prior is not None:
                            if prior != receipt:
                                _fail("google_control_receipt_exists")
                        else:
                            setup._publish_record(workflow, request_id + ".receipt.json", receipt)
                        return _status(root, workflow, origin, now, record)
    except GoogleControlError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_control_record_failed")


def export_google_control_admission(state, origin, output_fd, *, now=None):
    """Export one fresh decision, never a reusable blanket permission or worker status.

    The coordinator must make a new call for EACH admitted batch and compare its
    actual operational stable settings with test.operational_settings_sha256.
    Mutable current token bytes need not match the historical Test config hash.
    A failure/invalid frame means deny. Already admitted work is not cancelled
    here; Disable prevents subsequent admission decisions immediately.
    """
    try:
        destination._unprivileged()
        with destination._channel(output_fd, writing=True) as channel:
            deadline = destination._deadline(DEFAULT_TIMEOUT)
            now = _now(now)
            with destination._private_directory(state) as root:
                with setup._lock(root, "auth.lock", busy_code="google_control_busy"):
                    with _workflow(root) as workflow:
                        record = _current(workflow)
                        admitted = False
                        if record is not None and record["action"] == "enable":
                            payload = _authority(root, workflow, origin, record, now)
                            receipt = _receipt(workflow, record, now, payload)
                            admitted = receipt is not None and receipt["outcome"] == "applied"
                        wire = {"schema_version": 1, "provider": "google_drive",
                                "admission_authorized": admitted,
                                "control_request_id": record["request_id"] if admitted else None,
                                "preparation": record["preparation"] if admitted else None,
                                "test": record["test"] if admitted else None,
                                "checked_at": now, "single_admission": True}
                        destination._write_frame(channel, destination._canonical(wire), deadline)
        return {"schema_version": 1, "provider": "google_drive", "admission_exported": True,
                "current_worker_state_checked": False}
    except GoogleControlError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_control_admission_failed")
