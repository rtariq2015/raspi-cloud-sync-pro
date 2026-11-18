"""Private Google OAuth handoff and disabled rclone preparation, without network I/O.

A trusted operator runs the exporter and importer under their separate ordinary
service identities, passing exclusively owned anonymous-pipe or connected Unix
stream endpoints. Accepted descriptors are consumed and closed, with original
status flags restored first. Standard descriptors/aliases and invalid channels
are never consumed. Close unused pipe copies in the orchestrator: a complete
frame and EOF are required, within one deadline. No authorization is printed.

Preparation does not create a Drive folder, refresh a token, test an account,
authorize file uploads, grant access to web-inbox/auth state, or start a service.
Failed private preparation directories are retained for explicit recovery.
"""

import ctypes
import errno
import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import selectors
import socket
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import _unique_object
from .errors import BackupError

GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.file"
REMOTE_NAME = "rcsp_google"
PREPARATION_DIRECTORY = "google-drive.prepared"
MAX_HANDOFF_BYTES = 65536
DEFAULT_TIMEOUT = 5.0
_AUTH_FIELDS = {
    "schema_version",
    "provider",
    "client_id_sha256",
    "received_at",
    "expires_at",
    "token_type",
    "access_token",
    "refresh_token",
    "scopes",
}


class GoogleDestinationError(BackupError):
    """Fixed codes only; never attach a credential, path, payload or raw error."""


def _fail(code):
    raise GoogleDestinationError(code) from None


def _unprivileged():
    if os.geteuid() == 0 or os.getuid() != os.geteuid():
        _fail("google_preparation_requires_unprivileged_identity")


def _deadline(timeout):
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        _fail("google_handoff_timeout_invalid")
    return time.monotonic() + timeout


def _descriptor_identity(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


@contextmanager
def _channel(fd, *, writing):
    """Validate before accepting ownership; restore flags even through an alias."""
    if type(fd) is not int or fd < 3:
        _fail("google_handoff_channel_invalid")
    accepted = False
    original_flags = None
    try:
        info = os.fstat(fd)
        if os.isatty(fd):
            _fail("google_handoff_channel_invalid")
        identity = _descriptor_identity(fd)
        for standard in (0, 1, 2):
            try:
                if _descriptor_identity(standard) == identity:
                    _fail("google_handoff_channel_invalid")
            except OSError:
                pass
        original_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        access = original_flags & os.O_ACCMODE
        if (writing and access == os.O_RDONLY) or (
            not writing and access == os.O_WRONLY
        ):
            _fail("google_handoff_channel_invalid")
        if stat.S_ISFIFO(info.st_mode):
            # Refuse named FIFOs, which can have unrelated writers/readers.
            if not re.fullmatch(r"pipe:\[\d+\]", os.readlink(f"/proc/self/fd/{fd}")):
                _fail("google_handoff_channel_invalid")
        elif stat.S_ISSOCK(info.st_mode):
            # Autodetect the actual descriptor's domain/type, never infer these
            # from arguments supplied to socket.fromfd. No connection is opened.
            probe = socket.socket(fileno=os.dup(fd))
            try:
                if probe.family != socket.AF_UNIX or probe.type != socket.SOCK_STREAM:
                    _fail("google_handoff_channel_invalid")
                probe.getpeername()
            finally:
                probe.close()
        else:
            _fail("google_handoff_channel_invalid")
        accepted = True
        fcntl.fcntl(fd, fcntl.F_SETFL, original_flags | os.O_NONBLOCK)
        yield fd
    except GoogleDestinationError:
        raise
    except (OSError, ValueError, TypeError):
        raise GoogleDestinationError("google_handoff_channel_invalid") from None
    finally:
        if accepted:
            try:
                fcntl.fcntl(fd, fcntl.F_SETFL, original_flags)
            finally:
                os.close(fd)


def _wait(selector, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not selector.select(remaining):
        _fail("google_handoff_timeout")


def _write_frame(fd, payload, deadline):
    if not 0 < len(payload) <= MAX_HANDOFF_BYTES:
        _fail("google_handoff_too_large")
    frame = len(payload).to_bytes(4, "big") + payload
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_WRITE)
        offset = 0
        while offset < len(frame):
            _wait(selector, deadline)
            try:
                sent = os.write(fd, frame[offset : offset + 8192])
            except BlockingIOError:
                continue
            except OSError:
                raise GoogleDestinationError("google_handoff_write_failed") from None
            if sent <= 0:
                _fail("google_handoff_write_failed")
            offset += sent


def _read_frame(fd, deadline):
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)

        def read_exact(count):
            chunks = bytearray()
            while len(chunks) < count:
                _wait(selector, deadline)
                try:
                    block = os.read(fd, min(8192, count - len(chunks)))
                except BlockingIOError:
                    continue
                except OSError:
                    raise GoogleDestinationError("google_handoff_incomplete") from None
                if not block:
                    _fail("google_handoff_incomplete")
                chunks.extend(block)
            return bytes(chunks)

        length = int.from_bytes(read_exact(4), "big")
        if not 0 < length <= MAX_HANDOFF_BYTES:
            _fail("google_handoff_too_large")
        raw = read_exact(length)
        # Reject concatenated frames/trailing bytes and wait only until the same
        # deadline for EOF. A sender retaining another endpoint is not complete.
        _wait(selector, deadline)
        try:
            extra = os.read(fd, 1)
        except OSError:
            raise GoogleDestinationError("google_handoff_incomplete") from None
        if extra:
            _fail("google_handoff_invalid")
        try:
            return json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise GoogleDestinationError("google_handoff_invalid") from None


@contextmanager
def _private_directory(path):
    """Open a private leaf through trusted, non-symlink ancestor descriptors."""
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            _fail("google_preparation_path_unsafe")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in path.parts[1:]:
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
                if info.st_uid not in (0, os.geteuid()) or (
                    info.st_mode & 0o022 and not sticky_root
                ):
                    _fail("google_preparation_path_unsafe")
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                _fail("google_preparation_path_unsafe")
            yield fd
        finally:
            os.close(fd)
    except GoogleDestinationError:
        raise
    except (OSError, ValueError, TypeError):
        raise GoogleDestinationError("google_preparation_path_unsafe") from None


def _file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_private(directory_fd, filename, limit):
    fd = os.open(
        filename, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory_fd
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= limit
        ):
            _fail("google_authorization_unavailable")
        raw = bytearray()
        while len(raw) <= limit:
            block = os.read(fd, min(8192, limit + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        if len(raw) > limit or _file_identity(os.fstat(fd)) != _file_identity(info):
            _fail("google_authorization_unavailable")
        identity = _file_identity(
            os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        )
        if identity != _file_identity(info):
            _fail("google_authorization_unavailable")
        return json.loads(raw, object_pairs_hook=_unique_object), identity
    finally:
        os.close(fd)


def _origin(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 8192
        or not value.isascii()
        or any(not 33 <= ord(character) < 127 for character in value)
    ):
        _fail("google_authorization_invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in value
        or "#" in value
        or parsed.path not in ("", "/")
    ):
        _fail("google_authorization_invalid")
    # Force port parsing and reject malformed host syntax instead of allowing
    # urlsplit's permissive handling to produce an ambiguous private binding.
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        _fail("google_authorization_invalid")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        if len(parsed.hostname) > 253 or not re.fullmatch(
            label + r"(?:\." + label + r")*", parsed.hostname
        ):
            _fail("google_authorization_invalid")
    return value.rstrip("/")


def _client_value(value):
    # These Google client fields occupy literal INI lines, unlike JSON tokens.
    # Never permit comments, quoting, whitespace or additional sections/options.
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9._~:/+=-]{1,4096}", value) is not None
    )


def _token(value, limit):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and value.isascii()
        and all(32 <= ord(char) < 127 for char in value)
    )


@dataclass(frozen=True, repr=False)
class _Authorization:
    source_origin: str
    client: dict
    authorization: dict

    def __repr__(self):
        return "<GoogleAuthorization private>"

    def _bundle(self):
        return {
            "schema_version": 1,
            "provider": "google_drive",
            "source_origin": self.source_origin,
            "client": self.client,
            "authorization": self.authorization,
        }


def _validate_bundle(bundle):
    try:
        if (
            not isinstance(bundle, dict)
            or set(bundle)
            != {
                "schema_version",
                "provider",
                "source_origin",
                "client",
                "authorization",
            }
            or type(bundle["schema_version"]) is not int
            or bundle["schema_version"] != 1
            or bundle["provider"] != "google_drive"
        ):
            _fail("google_handoff_invalid")
        source_origin = _origin(bundle["source_origin"])
        if bundle["source_origin"] != source_origin:
            _fail("google_authorization_invalid")
        client, authorization = bundle["client"], bundle["authorization"]
        if (
            not isinstance(client, dict)
            or set(client) != {"client_id", "client_secret"}
            or not all(_client_value(value) for value in client.values())
        ):
            _fail("google_authorization_invalid")
        if (
            not isinstance(authorization, dict)
            or set(authorization) != _AUTH_FIELDS
            or type(authorization["schema_version"]) is not int
            or authorization["schema_version"] != 1
            or authorization["provider"] != "google_drive"
            or authorization["token_type"] != "Bearer"
            or not _token(authorization["access_token"], 32768)
            or not _token(authorization["refresh_token"], 16384)
        ):
            _fail("google_authorization_invalid")
        if (
            authorization["client_id_sha256"]
            != hashlib.sha256(client["client_id"].encode("ascii")).hexdigest()
        ):
            _fail("google_client_mismatch")
        if authorization["scopes"] != [GOOGLE_SCOPE]:
            _fail("google_scope_invalid")
        received, expiry = authorization["received_at"], authorization["expires_at"]
        if (
            type(received) not in (int, float)
            or type(expiry) not in (int, float)
            or not math.isfinite(received)
            or not math.isfinite(expiry)
            or not 0 <= received < expiry
        ):
            _fail("google_authorization_invalid")
        # An expired access token with a refresh grant is valid preparation input;
        # no refresh is attempted here. Check the representable UTC range only.
        datetime.fromtimestamp(expiry, timezone.utc)
        return _Authorization(source_origin, dict(client), dict(authorization))
    except GoogleDestinationError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        raise GoogleDestinationError("google_authorization_invalid") from None


def _source_authorization(directory_fd, origin):
    clients, client_identity = _read_private(directory_fd, "oauth-clients.json", 32768)
    authorization, authorization_identity = _read_private(
        directory_fd, "oauth-google_drive.json", 65536
    )
    if (
        not isinstance(clients, dict)
        or set(clients) != {"schema_version", "public_origin", "providers"}
        or type(clients["schema_version"]) is not int
        or clients["schema_version"] != 1
        or clients["public_origin"] != _origin(origin)
        or not isinstance(clients["providers"], dict)
        or set(clients["providers"]) - {"google_drive", "dropbox"}
    ):
        _fail("google_authorization_invalid")
    result = _validate_bundle(
        {
            "schema_version": 1,
            "provider": "google_drive",
            "source_origin": _origin(origin),
            "client": clients["providers"]["google_drive"],
            "authorization": authorization,
        }
    )
    for filename, identity in (
        ("oauth-clients.json", client_identity),
        ("oauth-google_drive.json", authorization_identity),
    ):
        if (
            _file_identity(
                os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
            )
            != identity
        ):
            _fail("google_authorization_unavailable")
    return result


def _canonical(data):
    return json.dumps(
        data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _render_config(authorization):
    record = authorization.authorization
    expiry = (
        datetime.fromtimestamp(record["expires_at"], timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    token = {
        "access_token": record["access_token"],
        "refresh_token": record["refresh_token"],
        "token_type": "Bearer",
        "expiry": expiry,
    }
    encoded_token = _canonical(token).decode("ascii")
    return (
        f"[{REMOTE_NAME}]\ntype = drive\nscope = drive.file\n"
        f"client_id = {authorization.client['client_id']}\n"
        f"client_secret = {authorization.client['client_secret']}\n"
        f"token = {encoded_token}\n"
    ).encode("ascii")


def _folder_name(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,62}[A-Za-z0-9])?", value
    ):
        _fail("google_folder_name_invalid")
    return value


def _projection():
    return {
        "schema_version": 1,
        "provider": "google_drive",
        "preparation_state": "prepared_untested",
        "enabled": False,
        "test_required": True,
        "account_verified": False,
        "folder_exists_verified": False,
        "automatic_deletion": False,
        "approved_uplinks": ["wifi"],
        "allow_metered": False,
    }


def _publish_directory(root_fd, incomplete):
    # os.rename may replace an existing empty directory. Linux RENAME_NOREPLACE
    # provides the explicit no-replacement transaction needed here, including races.
    library = ctypes.CDLL(None, use_errno=True)
    try:
        rename = library.renameat2
    except AttributeError:
        _fail("google_preparation_publication_unavailable")
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    if rename(
        root_fd,
        incomplete.encode("ascii"),
        root_fd,
        PREPARATION_DIRECTORY.encode("ascii"),
        1,
    ):
        if ctypes.get_errno() in (errno.EEXIST, errno.ENOTEMPTY):
            _fail("google_preparation_exists")
        _fail("google_preparation_publication_failed")


def _write_private(directory_fd, filename, payload):
    fd = os.open(
        filename,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail("google_preparation_failed")
        offset = 0
        while offset < len(payload):
            count = os.write(fd, payload[offset:])
            if count <= 0:
                _fail("google_preparation_failed")
            offset += count
        os.fsync(fd)
        os.fsync(directory_fd)
    finally:
        os.close(fd)


def export_google_authorization(
    dashboard_state, origin, output_fd, *, timeout=DEFAULT_TIMEOUT
):
    """Consume an approved output endpoint; return no credential/fingerprint data."""
    _unprivileged()
    deadline = _deadline(timeout)
    try:
        with _channel(output_fd, writing=True) as fd, \
             _private_directory(dashboard_state) as root:
            authorization = _source_authorization(root, origin)
            payload = _canonical(authorization._bundle())
            _write_frame(fd, payload, deadline)
        return {
            "provider": "google_drive",
            "authorization_exported": True,
            "uploads_enabled": False,
        }
    except GoogleDestinationError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        RecursionError,
        OverflowError,
    ):
        raise GoogleDestinationError("google_authorization_unavailable") from None


def prepare_google_destination(
    worker_state, input_fd, folder_name, *, timeout=DEFAULT_TIMEOUT
):
    """Consume a private handoff and publish disabled, worker-owned preparation.

    The existing worker state must be private and owned by the invoking ordinary
    UID. No source account/auth files are opened, no ownership is changed, and
    even a failed or interrupted partial preparation is retained privately.
    """
    _unprivileged()
    deadline = _deadline(timeout)
    try:
        with _channel(input_fd, writing=False) as fd, \
             _private_directory(worker_state) as root:
            folder_name = _folder_name(folder_name)
            try:
                os.stat(PREPARATION_DIRECTORY, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _fail("google_preparation_exists")
            authorization = _validate_bundle(_read_frame(fd, deadline))
            config = _render_config(authorization)
            projection = _projection()
            preparation = {
                **projection,
                "remote_name": REMOTE_NAME,
                "folder_name": folder_name,
                "authorization_sha256": hashlib.sha256(
                    _canonical(authorization._bundle())
                ).hexdigest(),
                "rclone_config_sha256": hashlib.sha256(config).hexdigest(),
            }
            incomplete = ".google-drive-" + secrets.token_hex(16) + ".incomplete"
            os.mkdir(incomplete, mode=0o700, dir_fd=root)
            os.fsync(root)
            partial_fd = os.open(
                incomplete, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
            )
            try:
                info = os.fstat(partial_fd)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    _fail("google_preparation_failed")
                _write_private(partial_fd, "rclone.conf", config)
                _write_private(partial_fd, "preparation.json", _canonical(preparation))
                os.fsync(partial_fd)
                os.fsync(root)
                _publish_directory(root, incomplete)
                os.fsync(root)
            finally:
                os.close(partial_fd)
        return projection
    except GoogleDestinationError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        RecursionError,
        OverflowError,
    ):
        raise GoogleDestinationError("google_preparation_failed") from None
