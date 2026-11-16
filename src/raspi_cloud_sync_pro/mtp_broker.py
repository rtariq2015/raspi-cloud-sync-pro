"""Optional local-only MTP completion FD broker; no listener starts on import.

One operation, one read-only SCM_RIGHTS descriptor, authenticated Unix peers.
No cloud credentials, USB configuration, shell commands or installation actions.
"""

import argparse
import array
from dataclasses import asdict
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import struct
import sys

from .config import _unique_object
from .mtp_ingest import CompletionRequest, CompletionSettings, MtpIngestError, publish_completed

DEFAULT_SOCKET = Path("/run/raspi-cloud-mtp/completion.sock")
MAX_REQUEST = 8192
TIMEOUT = 300.0
_FIELDS = {"relative", "size", "session", "transaction", "object_id"}


def _require(condition, code="mtp_broker_refused"):
    if not condition:
        raise MtpIngestError(code)


def _peer(connection):
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def _protected_ancestors(path):
    for parent in Path(path).parents:
        info = parent.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                 and info.st_uid == 0 and not info.st_mode & 0o022, "mtp_broker_parent_unsafe")


def _runtime_metadata(info, source_gid):
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == source_gid
             and stat.S_IMODE(info.st_mode) == 0o750, "mtp_broker_runtime_unsafe")


def _read_configuration(path):
    path = Path(path)
    info = path.lstat()
    _require(path.is_absolute() and path.resolve(strict=True) == path
             and stat.S_ISREG(info.st_mode) and info.st_uid == 0
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
             and 0 < info.st_size <= MAX_REQUEST, "mtp_broker_configuration_unsafe")
    _protected_ancestors(path)
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                               value.st_ctime_ns, value.st_mode, value.st_uid, value.st_gid, value.st_nlink)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        _require(signature(os.fstat(descriptor)) == signature(info), "mtp_broker_configuration_changed")
        raw = os.read(descriptor, MAX_REQUEST + 1)
        _require(len(raw) == info.st_size and signature(os.fstat(descriptor)) == signature(info)
                 and signature(path.lstat()) == signature(info), "mtp_broker_configuration_changed")
    finally:
        os.close(descriptor)
    return json.loads(raw, object_pairs_hook=_unique_object)


def handle_connection(connection, settings, *, timeout=TIMEOUT):
    """Serve one accepted connection. Close every received FD, including refusal."""
    descriptors = []
    result = {"status": "refused", "error": "mtp_broker_refused"}
    try:
        connection.settimeout(timeout)
        _require(_peer(connection)[1] == settings.source_uid, "mtp_broker_peer_refused")
        raw, ancillary, flags, _ = connection.recvmsg(
            MAX_REQUEST + 1, socket.CMSG_SPACE(16 * array.array("i").itemsize), socket.MSG_CMSG_CLOEXEC)
        unknown = False
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i")
                _require(len(data) % values.itemsize == 0)
                values.frombytes(data)
                descriptors.extend(values)
            else:
                unknown = True
        _require(not unknown and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                 and 0 < len(raw) <= MAX_REQUEST and len(descriptors) == 1, "mtp_broker_message_refused")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        _require(type(value) is dict and set(value) == _FIELDS, "mtp_broker_message_refused")
        request = CompletionRequest(**value)
        batch = publish_completed(descriptors[0], request, settings)
        _require(len(batch.files) == 1)
        result = {"status": "ready", "batch": batch.root.name,
                  "size": batch.files[0].size, "sha256": batch.files[0].sha256}
    except MtpIngestError as error:
        result = {"status": "refused", "error": error.code}
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        result = {"status": "refused", "error": "mtp_broker_message_refused"}
    except Exception:
        result = {"status": "refused", "error": "mtp_broker_completion_failed"}
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    try:
        connection.sendall(json.dumps(result, separators=(",", ":")).encode())
    except OSError:
        pass  # ACK may be lost after durable publication; retain ready evidence.
    return result


def send_completion(fd, request, socket_path=DEFAULT_SOCKET, *, expected_server_uid=0, timeout=TIMEOUT):
    """CLI uses fixed socket/root peer; override exists for unprivileged fixtures."""
    _require(isinstance(request, CompletionRequest) and type(fd) is int and fd >= 0)
    payload = json.dumps(asdict(request), ensure_ascii=True, separators=(",", ":")).encode()
    _require(len(payload) <= MAX_REQUEST)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as connection:
        connection.settimeout(timeout)
        connection.connect(str(socket_path))
        _require(_peer(connection)[1] == expected_server_uid, "mtp_broker_peer_refused")
        sent = connection.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
        _require(sent == len(payload), "mtp_broker_message_refused")
        raw, _, flags, _ = connection.recvmsg(MAX_REQUEST + 1)
        _require(not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) and 0 < len(raw) <= MAX_REQUEST)
        result = json.loads(raw, object_pairs_hook=_unique_object)
        _require(type(result) is dict and result.get("status") == "ready"
                 and set(result) == {"status", "batch", "size", "sha256"}, "mtp_broker_not_ready")
        _require(type(result["batch"]) is str and len(result["batch"]) == 38
                 and result["batch"].endswith(".ready")
                 and all(value in "0123456789abcdef" for value in result["batch"][:-6])
                 and type(result["size"]) is int and result["size"] == request.size
                 and type(result["sha256"]) is str and len(result["sha256"]) == 64
                 and all(value in "0123456789abcdef" for value in result["sha256"]), "mtp_broker_reply_invalid")
        return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def client_main(argv=None, *, socket_path=DEFAULT_SOCKET):
    parser = _Parser(add_help=False)
    for name in ("fd", "size", "session", "transaction", "object"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--relative", required=True)
    try:
        args = parser.parse_args(argv)
        request = CompletionRequest(args.relative, args.size, args.session, args.transaction, args.object)
        send_completion(args.fd, request, socket_path)
        return 0
    except (MtpIngestError, OSError, ValueError, TypeError, UnicodeError, OverflowError):
        # This helper is invoked with filenames: never print argv/tracebacks.
        print("MTP completion refused", file=sys.stderr)
        return 1


def server_main(argv=None):
    parser = _Parser(add_help=False)
    parser.add_argument("--config", required=True)
    try:
        args = parser.parse_args(argv)
        _require(os.geteuid() == 0, "mtp_broker_root_required")
        path = Path(args.config)
        value = _read_configuration(path)
        fields = {"source", "work", "staging", "source_uid", "target_uid", "target_gid", "reserve_bytes", "max_bytes"}
        _require(type(value) is dict and set(value) == fields | {"schema_version", "enabled", "socket_path"}
                 and type(value["schema_version"]) is int and value["schema_version"] == 1
                 and value["enabled"] is True)
        settings = CompletionSettings(**{key: Path(value[key]) if key in ("source", "work", "staging") else value[key]
                                         for key in fields})
        _require(type(settings.source_uid) is int and type(settings.target_uid) is int
                 and settings.source_uid > 0 and settings.target_uid > 0
                 and settings.source_uid != settings.target_uid)
        address = Path(value["socket_path"])
        _require(address.is_absolute() and address.parent.resolve(strict=True) == address.parent)
        _require(address.parent != settings.source and settings.source not in address.parent.parents,
                 "mtp_broker_socket_inside_export")
        source_gid = pwd.getpwuid(settings.source_uid).pw_gid
        _protected_ancestors(address)
        parent_fd = os.open(address.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            parent = os.fstat(parent_fd)
            _runtime_metadata(parent, source_gid)
            named = address.parent.lstat()
            _require((parent.st_dev, parent.st_ino) == (named.st_dev, named.st_ino))
            parent_identity = (parent.st_dev, parent.st_ino)
            _require(not os.path.lexists(address))
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as listener:
                # Anchor creation and ownership to the held directory. Never
                # follow an attacker-replaced path when root changes ownership.
                previous_umask = os.umask(0o117)
                try:
                    listener.bind("/proc/self/fd/" + str(parent_fd) + "/" + address.name)
                finally:
                    os.umask(previous_umask)
                original = os.stat(address.name, dir_fd=parent_fd, follow_symlinks=False)
                _require(stat.S_ISSOCK(original.st_mode) and original.st_uid == 0
                         and stat.S_IMODE(original.st_mode) == 0o660)
                os.chown(address.name, 0, source_gid,
                         dir_fd=parent_fd, follow_symlinks=False)
                current = os.stat(address.name, dir_fd=parent_fd, follow_symlinks=False)
                _require((current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
                         and stat.S_ISSOCK(current.st_mode) and current.st_uid == 0
                         and current.st_gid == source_gid and stat.S_IMODE(current.st_mode) == 0o660)
                listener.listen(1)
                while True:
                    current = address.parent.lstat()
                    _runtime_metadata(current, source_gid)
                    _require((current.st_dev, current.st_ino) == parent_identity)
                    connection, _ = listener.accept()
                    with connection:
                        handle_connection(connection, settings)
        finally:
            os.close(parent_fd)
    except (MtpIngestError, OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        print("MTP broker refused configuration or request", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        raise SystemExit(server_main(sys.argv[2:]))
    raise SystemExit(client_main())
