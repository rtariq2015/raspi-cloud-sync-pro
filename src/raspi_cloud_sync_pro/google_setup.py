"""Durable owner intent for private, disabled Google destination preparation.

The dashboard writes intent, a separately controlled broker consumes private
channels, and a receipt records only a historical preparation report. Nothing in
this module opens a connection, starts a process, tests an account or enables a
worker. Lock order is auth.lock then the workflow lock. A request's supplied
commit guard owns auth.lock, so request creation never reacquires it.
"""

import fcntl
import hashlib
import hmac
import math
import os
import re
import secrets
import stat
import time
from contextlib import ExitStack, contextmanager

from . import google_destination as destination
from .errors import BackupError
from .oauth import OwnerProof

WORKFLOW_DIRECTORY = "google-setup"
REQUEST_LIFETIME = 600.0
DEFAULT_TIMEOUT = 5.0
_RECORD_LIMIT = 8192
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_USERNAME = re.compile(r"[a-zA-Z0-9_.-]{1,64}\Z")
_REQUEST_FIELDS = {
    "schema_version", "provider", "request_id", "folder_name", "requested_at",
    "expires_at", "owner_username", "owner_credential_tag", "authorization_sha256",
}
_RECEIPT_FIELDS = {
    "schema_version", "provider", "request_id", "authorization_sha256", "rclone_config_sha256",
    "folder_name",
    "configuration_matches", "prepared_at", "enabled", "test_required",
    "account_verified", "folder_exists_verified", "automatic_deletion",
}
_FAILURES = (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError,
             OverflowError)


class GoogleSetupError(destination.GoogleDestinationError):
    """Fixed public codes only, with private exception context suppressed."""


def _fail(code):
    raise GoogleSetupError(code) from None


def _timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _now(value):
    value = time.time() if value is None else value
    if not _timestamp(value):
        _fail("google_setup_time_invalid")
    return value


def _request_id(value):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        _fail("google_setup_request_invalid")
    return value


def _projection(state, age=None, *, historical=False):
    return {
        "schema_version": 1,
        "provider": "google_drive",
        "preparation_state": state,
        "age_seconds": age,
        "historical": historical,
        "current_worker_state_checked": False,
        "enabled": False,
        "test_required": True,
        "account_verified": False,
        "folder_exists_verified": False,
        "automatic_deletion": False,
    }


@contextmanager
def _lock(root, filename, *, busy_code):
    fd = os.open(filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=root)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            _fail("google_setup_storage_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _fail(busy_code)
        yield
    finally:
        os.close(fd)


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
        fd = os.open(WORKFLOW_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=root)
    except FileNotFoundError:
        if create:
            raise
        yield None
        return
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            _fail("google_setup_storage_invalid")
        with _lock(fd, "workflow.lock", busy_code="google_setup_busy"):
            yield fd
    finally:
        os.close(fd)


def _read_record(root, name):
    try:
        return destination._read_private(root, name, _RECORD_LIMIT)[0]
    except FileNotFoundError:
        return None
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_setup_storage_invalid")


def _valid_header(value):
    return (isinstance(value, dict) and type(value.get("schema_version")) is int
            and value["schema_version"] == 1 and value.get("provider") == "google_drive")


def _current(root):
    pointer = _read_record(root, "current.json")
    if pointer is None:
        return None
    if (not isinstance(pointer, dict) or set(pointer) != {"schema_version", "request_id"}
            or type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1):
        _fail("google_setup_storage_invalid")
    request_id = _request_id(pointer["request_id"])
    record = _read_record(root, request_id + ".request.json")
    if (not _valid_header(record) or set(record) != _REQUEST_FIELDS
            or record["request_id"] != request_id
            or not isinstance(record["owner_username"], str)
            or _USERNAME.fullmatch(record["owner_username"]) is None
            or not isinstance(record["owner_credential_tag"], str)
            or _HASH.fullmatch(record["owner_credential_tag"]) is None
            or not isinstance(record["authorization_sha256"], str)
            or _HASH.fullmatch(record["authorization_sha256"]) is None
            or not _timestamp(record["requested_at"])
            or not _timestamp(record["expires_at"])
            or record["expires_at"] != record["requested_at"] + REQUEST_LIFETIME):
        _fail("google_setup_storage_invalid")
    try:
        destination._folder_name(record["folder_name"])
    except destination.GoogleDestinationError:
        _fail("google_setup_storage_invalid")
    return record


def _owner_tag(root, username):
    try:
        auth = destination._read_private(root, "auth.json", 1024 * 1024)[0]
        if (not isinstance(auth, dict) or set(auth) != {"secret", "users"}
                or not isinstance(auth["secret"], str) or len(auth["secret"]) < 64
                or not isinstance(auth["users"], dict) or not 1 <= len(auth["users"]) <= 1024):
            _fail("google_setup_owner_changed")
        for name, user in auth["users"].items():
            if (not isinstance(name, str) or _USERNAME.fullmatch(name) is None
                    or not isinstance(user, dict) or set(user) != {"role", "hash"}
                    or user["role"] not in ("owner", "viewer")
                    or not isinstance(user["hash"], str) or not 1 <= len(user["hash"]) <= 1024):
                _fail("google_setup_owner_changed")
        user = auth["users"].get(username)
        if not user or user["role"] != "owner":
            _fail("google_setup_owner_changed")
        return hashlib.sha256(user["hash"].encode()).hexdigest()
    except GoogleSetupError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_setup_owner_changed")


def _authorization(root, origin):
    try:
        authorization = destination._source_authorization(root, origin)
        payload = destination._canonical(authorization._bundle())
        if not 0 < len(payload) <= destination.MAX_HANDOFF_BYTES:
            _fail("google_setup_source_unavailable")
        # Both bindings derive from this single validated authorization object;
        # never reread a grant between metadata and credential-frame creation.
        return (payload, hashlib.sha256(payload).hexdigest(),
                hashlib.sha256(destination._render_config(authorization)).hexdigest())
    except GoogleSetupError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_setup_source_unavailable")


def _binding(root, origin, record):
    if not hmac.compare_digest(_owner_tag(root, record["owner_username"]),
                               record["owner_credential_tag"]):
        _fail("google_setup_owner_changed")
    payload, digest, config_digest = _authorization(root, origin)
    if not hmac.compare_digest(digest, record["authorization_sha256"]):
        _fail("google_setup_source_changed")
    return payload, config_digest


def _validate_receipt(receipt, request, now):
    if (not _valid_header(receipt) or set(receipt) != _RECEIPT_FIELDS
            or receipt["request_id"] != request["request_id"]
            or receipt["authorization_sha256"] != request["authorization_sha256"]
            or not isinstance(receipt["rclone_config_sha256"], str)
            or _HASH.fullmatch(receipt["rclone_config_sha256"]) is None
            or receipt["folder_name"] != request["folder_name"]
            or receipt["configuration_matches"] is not True
            or receipt["enabled"] is not False or receipt["test_required"] is not True
            or receipt["account_verified"] is not False
            or receipt["folder_exists_verified"] is not False
            or receipt["automatic_deletion"] is not False
            or not _timestamp(receipt["prepared_at"])
            or not request["requested_at"] <= receipt["prepared_at"] <= now):
        _fail("google_setup_receipt_invalid")
    return receipt


def _receipt(root, request, now):
    receipt = _read_record(root, request["request_id"] + ".receipt.json")
    if receipt is not None:
        _validate_receipt(receipt, request, now)
    return receipt


def _status(root, workflow, origin, now, record=None):
    if workflow is None:
        return _projection("not_requested")
    record = _current(workflow) if record is None else record
    if record is None:
        return _projection("not_requested")
    receipt = _receipt(workflow, record, now)
    age = max(0.0, now - (receipt["prepared_at"] if receipt else record["requested_at"]))
    try:
        _, config_digest = _binding(root, origin, record)
    except GoogleSetupError:
        return _projection("stale_receipt" if receipt else "stale_source", age,
                           historical=receipt is not None)
    if receipt:
        if not hmac.compare_digest(receipt["rclone_config_sha256"], config_digest):
            return _projection("stale_receipt", age, historical=True)
        return _projection("prepared_untested", age, historical=True)
    if now >= record["expires_at"]:
        return _projection("expired", age)
    return _projection("requested", age)


def _publish_record(root, filename, value):
    # An interrupted write retains its private incomplete file. Link publication
    # is atomic and refuses replacement, including competing final records.
    incomplete = "." + secrets.token_hex(16) + ".incomplete"
    destination._write_private(root, incomplete, destination._canonical(value))
    os.link(incomplete, filename, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
    os.fsync(root)
    os.unlink(incomplete, dir_fd=root)
    os.fsync(root)


def _publish_pointer(root, request_id):
    incomplete = ".current-" + secrets.token_hex(16) + ".incomplete"
    destination._write_private(root, incomplete,
                               destination._canonical({"schema_version": 1,
                                                       "request_id": request_id}))
    os.replace(incomplete, "current.json", src_dir_fd=root, dst_dir_fd=root)
    os.fsync(root)


def request_google_setup(state, origin, folder_name, proof, commit_guard, *, now=None):
    """Save owner intent while the caller's live-session guard owns auth.lock."""
    try:
        destination._unprivileged()
        now = _now(now)
        folder_name = destination._folder_name(folder_name)
        if (not isinstance(proof, OwnerProof) or not isinstance(proof.username, str)
                or _USERNAME.fullmatch(proof.username) is None
                or not isinstance(proof.credential_tag, str)
                or _HASH.fullmatch(proof.credential_tag) is None or not callable(commit_guard)):
            _fail("google_setup_owner_changed")
        with destination._private_directory(state) as root:
            try:
                with commit_guard(proof):
                    try:
                        tag = _owner_tag(root, proof.username)
                        if not hmac.compare_digest(tag, proof.credential_tag):
                            _fail("google_setup_owner_changed")
                        _, digest, _ = _authorization(root, origin)
                        with _workflow(root, create=True) as workflow:
                            current = _current(workflow)
                            if (current and current["owner_username"] == proof.username
                                    and current["owner_credential_tag"] == tag
                                    and current["authorization_sha256"] == digest
                                    and current["folder_name"] == folder_name
                                    and now < current["expires_at"]):
                                return _status(root, workflow, origin, now, current)
                            request_id = secrets.token_hex(16)
                            record = {
                                "schema_version": 1, "provider": "google_drive",
                                "request_id": request_id, "folder_name": folder_name,
                                "requested_at": now, "expires_at": now + REQUEST_LIFETIME,
                                "owner_username": proof.username, "owner_credential_tag": tag,
                                "authorization_sha256": digest,
                            }
                            _publish_record(workflow, request_id + ".request.json", record)
                            _publish_pointer(workflow, request_id)
                            return _projection("requested", 0.0)
                    except GoogleSetupError:
                        raise
                    except (destination.GoogleDestinationError,) + _FAILURES:
                        _fail("google_setup_unavailable")
            except GoogleSetupError:
                raise
            except (BackupError,) + _FAILURES:
                _fail("google_setup_owner_changed")
    except GoogleSetupError:
        raise
    except (destination.GoogleDestinationError,) + _FAILURES:
        _fail("google_setup_unavailable")


def google_setup_status(state, origin, *, now=None):
    """Return only historical age/state and conservative disabled flags."""
    try:
        destination._unprivileged()
        now = _now(now)
        with destination._private_directory(state) as root:
            with _lock(root, "auth.lock", busy_code="google_setup_busy"):
                with _workflow(root) as workflow:
                    return _status(root, workflow, origin, now)
    except GoogleSetupError as error:
        invalid = {"google_setup_storage_invalid", "google_setup_request_invalid",
                   "google_setup_receipt_invalid", "google_setup_time_invalid"}
        return _projection("invalid" if error.code in invalid else "unavailable")
    except (destination.GoogleDestinationError,) + _FAILURES:
        return _projection("unavailable")


@contextmanager
def _output_channels(metadata_fd, authorization_fd):
    # Validate each independently so every accepted endpoint is consumed even
    # when its partner is invalid. Invalid endpoints remain caller-owned.
    with ExitStack() as stack:
        channels = []
        owners = []
        seen = set()
        invalid = False
        for fd in (metadata_fd, authorization_fd):
            if type(fd) is int and fd in seen:
                invalid = True
                continue
            if type(fd) is int:
                seen.add(fd)
            try:
                owner = stack.enter_context(ExitStack())
                channels.append(owner.enter_context(destination._channel(fd, writing=True)))
                owners.append(owner)
            except destination.GoogleDestinationError:
                invalid = True
        if invalid or len(channels) != 2:
            _fail("google_setup_channel_invalid")
        if destination._descriptor_identity(channels[0]) == destination._descriptor_identity(channels[1]):
            _fail("google_setup_channel_invalid")
        yield channels, owners[0].close


def _live_request(workflow, request_id, now):
    record = _current(workflow)
    if record is None or record["request_id"] != request_id:
        _fail("google_setup_request_not_current")
    if now >= record["expires_at"]:
        _fail("google_setup_request_expired")
    return record


def export_requested_google_setup(state, origin, request_id, metadata_fd,
                                  authorization_fd, *, now=None):
    """Consume two private outputs, sending bound metadata before credentials."""
    try:
        destination._unprivileged()
        with _output_channels(metadata_fd, authorization_fd) as outputs:
            channels, finish_metadata = outputs
            deadline = destination._deadline(DEFAULT_TIMEOUT)
            now = _now(now)
            request_id = _request_id(request_id)
            with destination._private_directory(state) as root:
                with _lock(root, "auth.lock", busy_code="google_setup_busy"):
                    with _workflow(root) as workflow:
                        record = _live_request(workflow, request_id, now) if workflow is not None else None
                        if record is None:
                            _fail("google_setup_request_not_current")
                        if _receipt(workflow, record, now) is not None:
                            _fail("google_setup_already_prepared")
                        payload, config_digest = _binding(root, origin, record)
                        metadata = {
                            "schema_version": 1, "provider": "google_drive",
                            "request_id": request_id, "folder_name": record["folder_name"],
                            "authorization_sha256": record["authorization_sha256"],
                            "rclone_config_sha256": config_digest,
                        }
                        destination._write_frame(channels[0], destination._canonical(metadata), deadline)
                        finish_metadata()
                        destination._write_frame(channels[1], payload, deadline)
        return {"schema_version": 1, "provider": "google_drive",
                "authorization_exported": True, "uploads_enabled": False}
    except GoogleSetupError:
        raise
    except destination.GoogleDestinationError as error:
        _fail(error.code)
    except _FAILURES:
        _fail("google_setup_unavailable")


def record_google_setup_receipt(state, origin, request_id, receipt_fd, *, now=None):
    """Consume a strict private report and retain it without replacing history."""
    try:
        destination._unprivileged()
        with destination._channel(receipt_fd, writing=False) as channel:
            deadline = destination._deadline(DEFAULT_TIMEOUT)
            _now(now)
            request_id = _request_id(request_id)
            receipt = destination._read_frame(channel, deadline)
            # A frame may take almost the complete handoff deadline. Use the
            # completed-read time when validating live intent and prepared_at.
            now = _now(now)
            with destination._private_directory(state) as root:
                with _lock(root, "auth.lock", busy_code="google_setup_busy"):
                    with _workflow(root) as workflow:
                        record = _live_request(workflow, request_id, now) if workflow is not None else None
                        if record is None:
                            _fail("google_setup_request_not_current")
                        _, config_digest = _binding(root, origin, record)
                        _validate_receipt(receipt, record, now)
                        if not hmac.compare_digest(receipt["rclone_config_sha256"], config_digest):
                            _fail("google_setup_receipt_invalid")
                        existing = _receipt(workflow, record, now)
                        if existing is not None:
                            if existing != receipt:
                                _fail("google_setup_receipt_exists")
                        else:
                            _publish_record(workflow, request_id + ".receipt.json", receipt)
                        return _status(root, workflow, origin, now, record)
    except GoogleSetupError:
        raise
    except destination.GoogleDestinationError as error:
        _fail(error.code)
    except _FAILURES:
        _fail("google_setup_unavailable")
