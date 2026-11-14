"""Private, disabled operational generations seeded from Google preparation.

Invoke only as the ordinary backup identity. The caller supplies a request ID,
folder label and both expected digests obtained through independent source-side
validation. An untrusted prepared manifest, browser input, or this module's own
stored provenance is never a substitute for that authority.

Prepared files are opened read-only and left in place. Each new generation has
an immutable-by-policy provenance record and a separate writable config directory
for future rclone token refresh. No refresh, provider invocation, testing, account
verification, folder creation, enablement or network action happens here.
Inspection checks historical seed provenance and filesystem privacy. It does
not authorize changed operational config bytes, or establish current source,
account or folder trust. All generations remain disabled and untested.
"""

import ctypes
import errno
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import google_destination as destination
from .google_broker import GoogleBrokerError, _validate_config
from .config import _unique_object
from .errors import BackupError

_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CONFIG = 65536
_PROVENANCE = "generation.json"
_FAILURES = (OSError, ValueError, TypeError, KeyError, UnicodeError,
             RecursionError, OverflowError)


class GoogleOperationalError(BackupError):
    """Fixed codes only: never attach paths, payloads, digests or raw errors."""


def _fail(code):
    raise GoogleOperationalError(code) from None


def _ordinary_identity():
    destination._unprivileged()
    if (os.getresuid() != (os.geteuid(),) * 3
            or os.getresgid() != (os.getegid(),) * 3):
        _fail("google_operational_requires_unprivileged_identity")


def _path(value):
    if not isinstance(value, (str, Path)):
        _fail("google_operational_path_unsafe")
    value = Path(value)
    if not value.is_absolute() or ".." in value.parts or "\x00" in str(value):
        _fail("google_operational_path_unsafe")
    return value


def _paths(prepared_state, operational_state):
    prepared, operational = _path(prepared_state), _path(operational_state)
    # Never put operational writes in or above the prepared state tree.
    # Both independent state roots must already exist privately.
    if (prepared == operational or prepared in operational.parents
            or operational in prepared.parents):
        _fail("google_operational_path_unsafe")
    return prepared, operational


def _binding(request_id, folder_name, authorization_sha256, config_sha256):
    if (not isinstance(request_id, str) or _ID.fullmatch(request_id) is None
            or any(not isinstance(value, str) or _HASH.fullmatch(value) is None
                   for value in (authorization_sha256, config_sha256))):
        _fail("google_operational_binding_invalid")
    return {"source_request_id": request_id,
            "source_folder_name": destination._folder_name(folder_name),
            "source_authorization_sha256": authorization_sha256,
            "seed_rclone_config_sha256": config_sha256}


def _directory_info(fd):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        _fail("google_operational_path_unsafe")
    return info


def _inode(info):
    return info.st_dev, info.st_ino


def _file_identity(info):
    return (destination._file_identity(info), info.st_uid,
            stat.S_IMODE(info.st_mode), info.st_nlink)


def _file_info(info, limit):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not 0 < info.st_size <= limit):
        _fail("google_operational_file_unsafe")
    return _file_identity(info)


def _read(directory_fd, name, limit):
    # These are our own files. O_NOATIME also leaves preparation access times
    # unchanged on Linux; no write descriptor is opened on preparation.
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                 | getattr(os, "O_NOATIME", 0), dir_fd=directory_fd)
    try:
        identity = _file_info(os.fstat(fd), limit)
        raw = bytearray()
        while len(raw) <= limit:
            block = os.read(fd, min(8192, limit + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        if (len(raw) > limit or _file_info(os.fstat(fd), limit) != identity
                or _file_info(os.stat(name, dir_fd=directory_fd,
                                      follow_symlinks=False), limit) != identity):
            _fail("google_operational_file_changed")
        return bytes(raw), identity
    finally:
        os.close(fd)


@contextmanager
def _child(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        original = _directory_info(fd)
        yield fd
        if (_inode(_directory_info(fd)) != _inode(original)
                or _inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _inode(original)):
            _fail("google_operational_path_changed")
    finally:
        os.close(fd)


def _still_named(path, fd):
    with destination._private_directory(path) as current:
        if _inode(_directory_info(current)) != _inode(_directory_info(fd)):
            _fail("google_operational_path_changed")


def _prepared(prepared_fd, binding):
    with _child(prepared_fd, destination.PREPARATION_DIRECTORY) as proof:
        raw, manifest_identity = _read(proof, "preparation.json", 8192)
        config, config_identity = _read(proof, "rclone.conf", _MAX_CONFIG)
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        projection = destination._projection()
        if (not isinstance(manifest, dict)
                or set(manifest) != set(projection) | {
                    "remote_name", "folder_name", "authorization_sha256", "rclone_config_sha256"}
                or any(type(manifest[key]) is not type(value) or manifest[key] != value
                       for key, value in projection.items())
                or manifest["remote_name"] != destination.REMOTE_NAME
                or manifest["folder_name"] != binding["source_folder_name"]
                or manifest["authorization_sha256"] != binding["source_authorization_sha256"]
                or manifest["rclone_config_sha256"] != binding["seed_rclone_config_sha256"]
                or not hmac.compare_digest(hashlib.sha256(config).hexdigest(),
                                           binding["seed_rclone_config_sha256"])):
            _fail("google_operational_preparation_mismatch")
        _validate_config(config)
        # Detect multi-file substitution too, retaining file identities as well
        # as bytes. The outer caller repeats the whole snapshot before publish.
        if (_read(proof, "preparation.json", 8192) != (raw, manifest_identity)
                or _read(proof, "rclone.conf", _MAX_CONFIG) != (config, config_identity)):
            _fail("google_operational_preparation_changed")
        return (raw, manifest_identity, config, config_identity, _inode(os.fstat(proof)))


def _destination_id(generation_id, binding):
    context = {"generation_id": generation_id, **binding}
    return "google_drive_rclone/generation/" + hashlib.sha256(
        destination._canonical(context)).hexdigest()


def _disabled():
    return {"enabled": False, "test_required": True, "account_verified": False,
            "folder_exists_verified": False, "automatic_deletion": False,
            "approved_uplinks": ["wifi"], "allow_metered": False}


@dataclass(frozen=True, repr=False)
class GoogleOperationalGeneration:
    """Private locations/identity, never a provider-readiness authorization.

    A later, separate Test/Enable implementation must pin the actual cloud
    folder to this generation, require a new generation for a folder change,
    and use destination_id as its history identity. Existing RcloneProvider
    derives its own unscoped identity and is not wired by this module. A
    matching seed hash describes bytes only, never cloud or source freshness.
    """

    generation_id: str
    destination_id: str
    config_path: Path
    mutable_config_matches_seed: bool

    def __repr__(self):
        return "<GoogleOperationalGeneration private, disabled, untested>"

    def projection(self):
        return {"schema_version": 1, "provider": "google_drive",
                "operational_state": "seeded_untested", **_disabled(),
                "mutable_configuration_requires_validation": True}


def _describe(operational, generation_id, binding, config):
    return GoogleOperationalGeneration(
        generation_id, _destination_id(generation_id, binding),
        operational / generation_id / "config" / "rclone.conf",
        hmac.compare_digest(hashlib.sha256(config).hexdigest(),
                            binding["seed_rclone_config_sha256"]))


def _publish(root, partial, generation_id):
    # Atomic Linux no-replace publication also refuses an existing empty dir.
    library = ctypes.CDLL(None, use_errno=True)
    try:
        rename = library.renameat2
    except AttributeError:
        _fail("google_operational_publication_unavailable")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(root, partial.encode("ascii"), root, generation_id.encode("ascii"), 1):
        if ctypes.get_errno() in (errno.EEXIST, errno.ENOTEMPTY):
            _fail("google_operational_exists")
        _fail("google_operational_publication_failed")


def create_google_operational_generation(
    prepared_state, operational_state, request_id, expected_folder_name,
    expected_authorization_sha256, expected_rclone_config_sha256, *, now=None
):
    """Seed a fresh generation; retain all incomplete/final data on any failure.

    Both private state roots must already exist and belong to this ordinary UID.
    The preparation is the google-drive.prepared child of request_id state.
    Expected fields must come from independent source authority. Nothing here
    rechecks the source owner, consent, current account or expiry on its behalf.
    """
    try:
        _ordinary_identity()
        prepared, operational = _paths(prepared_state, operational_state)
        binding = _binding(request_id, expected_folder_name,
                           expected_authorization_sha256, expected_rclone_config_sha256)
        if prepared.name != request_id:
            _fail("google_operational_binding_invalid")
        now = time.time() if now is None else now
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            _fail("google_operational_time_invalid")
        with destination._private_directory(prepared) as source, \
                destination._private_directory(operational) as root:
            if _inode(os.fstat(source)) == _inode(os.fstat(root)):
                _fail("google_operational_path_unsafe")
            snapshot = _prepared(source, binding)
            generation_id = secrets.token_hex(16)
            partial = ".google-operational-" + secrets.token_hex(16) + ".incomplete"
            provenance = {"schema_version": 1, "provider": "google_drive",
                          "operational_state": "seeded_untested",
                          "generation_id": generation_id,
                          "destination_id": _destination_id(generation_id, binding),
                          "remote_name": destination.REMOTE_NAME, "created_at": now,
                          "preparation_manifest_sha256": hashlib.sha256(snapshot[0]).hexdigest(),
                          **binding, **_disabled()}
            os.mkdir(partial, mode=0o700, dir_fd=root)
            os.fsync(root)
            with _child(root, partial) as incomplete:
                incomplete_inode = _inode(os.fstat(incomplete))
                os.mkdir("config", mode=0o700, dir_fd=incomplete)
                with _child(incomplete, "config") as config_dir:
                    destination._write_private(config_dir, "rclone.conf", snapshot[2])
                destination._write_private(incomplete, _PROVENANCE,
                                           destination._canonical(provenance))
                if _prepared(source, binding) != snapshot:
                    _fail("google_operational_preparation_changed")
                _still_named(prepared, source)
                _still_named(operational, root)
                os.fsync(incomplete)
            _publish(root, partial, generation_id)
            os.fsync(root)
            # A same-UID concurrent actor can rename a directory entry between
            # validation and renameat2. Never return a descriptor for a swapped
            # final directory; retain the published artifact for recovery.
            with _child(root, generation_id) as published:
                if (_inode(os.fstat(published)) != incomplete_inode
                        or _read(published, _PROVENANCE, 8192)[0]
                        != destination._canonical(provenance)):
                    _fail("google_operational_publication_changed")
                with _child(published, "config") as config_dir:
                    if _read(config_dir, "rclone.conf", _MAX_CONFIG)[0] != snapshot[2]:
                        _fail("google_operational_publication_changed")
            if _prepared(source, binding) != snapshot:
                _fail("google_operational_preparation_changed")
            _still_named(prepared, source)
            _still_named(operational, root)
            return _describe(operational, generation_id, binding, snapshot[2])
    except GoogleOperationalError:
        raise
    except (destination.GoogleDestinationError, GoogleBrokerError) + _FAILURES:
        _fail("google_operational_creation_failed")


def inspect_google_operational_generation(
    prepared_state, operational_state, generation_id, request_id,
    expected_folder_name, expected_authorization_sha256, expected_rclone_config_sha256
):
    """Validate historical seed proof/privacy; never accept mutable config trust.

    Token-refresh writes/renames in config/ are allowed. Nonempty bounded private
    config bytes are inspected only for seed equality; changed bytes remain
    unvalidated, even if they happen to be syntactically valid rclone settings.
    A missing, insecure or concurrently changing file fails closed.
    """
    try:
        _ordinary_identity()
        prepared, operational = _paths(prepared_state, operational_state)
        binding = _binding(request_id, expected_folder_name,
                           expected_authorization_sha256, expected_rclone_config_sha256)
        if (prepared.name != request_id or not isinstance(generation_id, str)
                or _ID.fullmatch(generation_id) is None):
            _fail("google_operational_binding_invalid")
        with destination._private_directory(prepared) as source, \
                destination._private_directory(operational) as root:
            snapshot = _prepared(source, binding)
            with _child(root, generation_id) as generation:
                raw, identity = _read(generation, _PROVENANCE, 8192)
                data = json.loads(raw, object_pairs_hook=_unique_object)
                expected = {"schema_version": 1, "provider": "google_drive",
                            "operational_state": "seeded_untested",
                            "generation_id": generation_id,
                            "destination_id": _destination_id(generation_id, binding),
                            "remote_name": destination.REMOTE_NAME,
                            "preparation_manifest_sha256": hashlib.sha256(snapshot[0]).hexdigest(),
                            **binding, **_disabled()}
                if (not isinstance(data, dict) or set(data) != set(expected) | {"created_at"}
                        or any(type(data[key]) is not type(value) or data[key] != value
                               for key, value in expected.items())
                        or type(data["created_at"]) not in (int, float)
                        or not math.isfinite(data["created_at"]) or data["created_at"] < 0):
                    _fail("google_operational_provenance_invalid")
                with _child(generation, "config") as config_dir:
                    config, _ = _read(config_dir, "rclone.conf", _MAX_CONFIG)
                if _read(generation, _PROVENANCE, 8192) != (raw, identity):
                    _fail("google_operational_provenance_changed")
                if _prepared(source, binding) != snapshot:
                    _fail("google_operational_preparation_changed")
                _still_named(prepared, source)
                _still_named(operational, root)
            return _describe(operational, generation_id, binding, config)
    except GoogleOperationalError:
        raise
    except (destination.GoogleDestinationError, GoogleBrokerError) + _FAILURES:
        _fail("google_operational_inspection_failed")
