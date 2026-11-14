"""Private local account updates; no signup, role changes or credential logging."""

import fcntl
import getpass
import json
import os
import re
import secrets
import stat
import time
import warnings
from contextlib import contextmanager
from pathlib import Path

from .config import _unique_object
from .errors import BackupError
from .staging import _fsync_directory, open_regular


def _private_state(state, *, create=False):
    state = Path(state).absolute()
    if create:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Dashboard state must be private and owned by the service user")
    return state


def read_auth(state):
    state = _private_state(state)
    with open_regular(state, "auth.json") as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
            raise ValueError("Dashboard authentication file must be private")
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Authentication configuration is too large")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid authentication configuration") from exc
    if not isinstance(data, dict) or set(data) != {"secret", "users"} or not isinstance(data["secret"], str) \
            or len(data["secret"]) < 64 or not isinstance(data["users"], dict) or not 1 <= len(data["users"]) <= 1024:
        raise ValueError("Invalid authentication configuration")
    for username, user in data["users"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", username) or not isinstance(user, dict) \
                or set(user) != {"role", "hash"} or user["role"] not in ("owner", "viewer") \
                or not isinstance(user["hash"], str) or not 1 <= len(user["hash"]) <= 1024:
            raise ValueError("Invalid authentication configuration")
    return data


@contextmanager
def _auth_lock(state):
    fd = os.open(state / "auth.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Authentication lock must be private")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("account_update_busy") from exc
        yield
    finally:
        os.close(fd)


def _publish_auth(state, data):
    """Caller holds auth.lock. Keep a durable private backup before replacement."""
    path = state / "auth.json"
    if path.is_symlink():
        raise ValueError("Authentication file cannot be a symlink")
    if path.exists():
        with open_regular(state, "auth.json") as incoming:
            previous = incoming.read(1024 * 1024 + 1)
        if len(previous) > 1024 * 1024:
            raise ValueError("Authentication configuration is too large")
        backup = state / ("auth-backup-" + str(time.time_ns()) + "-" + secrets.token_hex(4) + ".json")
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(previous)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(state)
    temporary = state / ("auth-" + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(state)
    finally:
        temporary.unlink(missing_ok=True)


def _password(password=None, confirmation=None):
    if password is None:
        try:
            with warnings.catch_warnings():
                # getpass warns before using its potentially echoed fallback.
                # Fail there instead of ever reading a secret insecurely.
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("New dashboard password: ")
                confirmation = getpass.getpass("Confirm dashboard password: ")
        except getpass.GetPassWarning:
            raise ValueError("A secure interactive terminal is required") from None
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Use a password between 12 and 256 characters")
    if confirmation is not None and (not isinstance(confirmation, str) or password != confirmation):
        raise ValueError("Passwords do not match")
    return password


def initialize_dashboard(state, *, username="owner", role="owner", password=None):
    from werkzeug.security import generate_password_hash
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", username) or role not in ("owner", "viewer"):
        raise ValueError("Invalid account name or role")
    password = _password(password)
    state = _private_state(state, create=True)
    with _auth_lock(state):
        path = state / "auth.json"
        if path.exists() or path.is_symlink():
            data = read_auth(state)
            if username in data["users"]:
                raise ValueError("Account exists; use the explicit password recovery command")
            if len(data["users"]) >= 1024:
                raise ValueError("Account limit reached")
        else:
            data = {"secret": secrets.token_hex(32), "users": {}}
        data["users"][username] = {"role": role, "hash": generate_password_hash(password)}
        _publish_auth(state, data)


def change_dashboard_password(state, username, current_password, password, confirmation):
    from werkzeug.security import check_password_hash, generate_password_hash
    if not isinstance(password, str) or not isinstance(confirmation, str):
        raise ValueError("Invalid password change")
    password = _password(password, confirmation)
    if not isinstance(current_password, str) or not 1 <= len(current_password) <= 256:
        raise ValueError("Invalid password change")
    if password == current_password:
        raise ValueError("Choose a different password")
    state = _private_state(state)
    with _auth_lock(state):
        data = read_auth(state)
        user = data["users"].get(username)
        if user is None or not check_password_hash(user["hash"], current_password):
            raise BackupError("current_password_rejected")
        user["hash"] = generate_password_hash(password)
        _publish_auth(state, data)


def reset_dashboard_password(state, *, username, password=None):
    """Local CLI recovery; OS access to the private state authorizes this operation."""
    from werkzeug.security import generate_password_hash
    if not isinstance(username, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", username):
        raise ValueError("Invalid account name")
    password = _password(password)
    state = _private_state(state)
    with _auth_lock(state):
        data = read_auth(state)
        if username not in data["users"]:
            raise ValueError("Account does not exist")
        data["users"][username]["hash"] = generate_password_hash(password)
        _publish_auth(state, data)
