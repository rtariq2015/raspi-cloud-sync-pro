"""Experimental MTP completion-to-ReadyBatch boundary; never opens USB images.

Only the patched responder's synchronous successful SendObject callback may call
this boundary. A stable size, file close or directory watch is not completion.
All roots must be precreated; no installation, account or service changes occur.
"""

import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from dataclasses import dataclass
import uuid

from .errors import BackupError
from .staging import ReadyBatch, StagedFile, relative_path


class MtpIngestError(BackupError):
    pass


@dataclass(frozen=True)
class CompletionSettings:
    source: Path
    work: Path
    staging: Path
    source_uid: int
    target_uid: int
    target_gid: int
    reserve_bytes: int = 2 * 1024**3
    max_bytes: int = 0xfffffff2


@dataclass(frozen=True)
class CompletionRequest:
    relative: str
    size: int
    session: int
    transaction: int
    object_id: int


def _require(value, code="mtp_completion_invalid"):
    if not value:
        raise MtpIngestError(code)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def _directory(path, owner):
    path = Path(path)
    _require(path.is_absolute() and ".." not in path.parts and path.resolve(strict=True) == path,
             "mtp_root_unsafe")
    for ancestor in path.parents:
        info = ancestor.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                 and (not info.st_mode & 0o022 or sticky), "mtp_root_unsafe")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner
                 and stat.S_IMODE(info.st_mode) == 0o700
                 and _identity(info) == _identity(path.lstat()), "mtp_root_unsafe")
        return fd, (info.st_dev, info.st_ino)
    except BaseException:
        os.close(fd)
        raise


def _anchor(path, fd, identity, owner):
    info = os.fstat(fd)
    named = Path(path).lstat()
    _require((info.st_dev, info.st_ino) == identity == (named.st_dev, named.st_ino)
             and stat.S_ISDIR(named.st_mode) and named.st_uid == owner
             and stat.S_IMODE(named.st_mode) == 0o700, "mtp_root_changed")


def _source_file(root, relative):
    current = os.dup(root)
    try:
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=current)
            os.close(current)
            current = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       dir_fd=current)
    finally:
        os.close(current)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        _require(type(count) is int and 0 < count <= len(view), "mtp_write_failed")
        view = view[count:]


def _write_json(root, name, value):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=root)
    try:
        _write_all(fd, json.dumps(value, ensure_ascii=True, sort_keys=True,
                                 separators=(",", ":")).encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(source_fd, name, destination_fd, ready):
    # Linux-only, no replacing an existing batch and no permissive fallback.
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    _require(rename is not None, "mtp_noreplace_unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(name), destination_fd, os.fsencode(ready), 1):
        error = ctypes.get_errno()
        raise OSError(error, "MTP publication refused")


def publish_completed(fd, request, settings):
    """Snapshot a protocol-complete held descriptor, retain evidence on failure.

    The caller retains ownership of fd. Success follows payload, manifest, tree
    and both publication-directory fsyncs. An ACK lost after publication can
    leave a valid ready batch: preserve it; the existing worker deduplicates.
    """
    roots, opened = [], []
    try:
        _require(isinstance(settings, CompletionSettings) and isinstance(request, CompletionRequest))
        _require(type(fd) is int and fd >= 0 and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
                 "mtp_descriptor_invalid")
        _require(all(type(value) is int and value >= 0 for value in
                     (settings.source_uid, settings.target_uid, settings.target_gid, settings.reserve_bytes))
                 and type(settings.max_bytes) is int and 0 <= settings.max_bytes <= 0xfffffff2)
        _require(type(request.size) is int and 0 <= request.size <= settings.max_bytes,
                 "mtp_object_too_large")
        _require(all(type(value) is int and 0 < value <= 0xffffffff for value in
                     (request.session, request.transaction, request.object_id)))
        name = relative_path(request.relative)
        parts = PurePosixPath(name).parts
        _require(len(parts) <= 32 and len(name.encode("utf-8")) <= 4096
                 and all(len(part.encode("utf-8")) <= 255 for part in parts))
        paths = [Path(value) for value in (settings.source, settings.work, settings.staging)]
        _require(all(a != b and a not in b.parents and b not in a.parents
                     for index, a in enumerate(paths) for b in paths[index + 1:]), "mtp_roots_overlap")
        owners = [settings.source_uid, os.geteuid(), settings.target_uid]
        for path, owner in zip(paths, owners):
            root, identity = _directory(path, owner)
            roots.append((path, root, identity, owner))
        source, work, staging = [entry[1] for entry in roots]
        _require(len({identity[0] for _, _, identity, _ in roots}) == 1, "mtp_cross_filesystem")

        def anchors():
            for path, root, identity, owner in roots:
                _anchor(path, root, identity, owner)

        original = os.fstat(fd)
        _require(stat.S_ISREG(original.st_mode) and original.st_uid == settings.source_uid
                 and original.st_nlink == 1 and original.st_size == request.size,
                 "mtp_source_invalid")

        def source_stable():
            anchors()
            other = _source_file(source, name)
            try:
                _require(_identity(original) == _identity(os.fstat(fd)) == _identity(os.fstat(other)),
                         "mtp_source_changed")
            finally:
                os.close(other)

        source_stable()
        space = os.fstatvfs(work)
        _require(space.f_bavail * space.f_frsize >= request.size + settings.reserve_bytes + 65536,
                 "mtp_space_low")
        batch_id = uuid.uuid4().hex
        incomplete, ready = batch_id + ".incomplete", batch_id + ".ready"
        os.mkdir(incomplete, 0o700, dir_fd=work)
        os.fsync(work)
        batch = os.open(incomplete, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=work)
        opened.append(batch)
        os.mkdir("files", 0o700, dir_fd=batch)
        payload = os.open("files", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=batch)
        opened.append(payload)
        directory = payload
        for part in parts[:-1]:
            os.mkdir(part, 0o700, dir_fd=directory)
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=directory)
            opened.append(directory)
        output = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
        opened.append(output)
        _require((os.fstat(output).st_dev, os.fstat(output).st_ino) !=
                 (original.st_dev, original.st_ino), "mtp_snapshot_not_distinct")
        digest, offset = hashlib.sha256(), 0
        while offset < request.size:
            source_stable()
            chunk = os.pread(fd, min(65536, request.size - offset), offset)
            _require(bool(chunk), "mtp_source_incomplete")
            space = os.fstatvfs(work)
            _require(space.f_bavail * space.f_frsize >= len(chunk) + settings.reserve_bytes,
                     "mtp_space_low")
            _write_all(output, chunk)
            digest.update(chunk)
            offset += len(chunk)
        _require(not os.pread(fd, 1, offset), "mtp_source_size_changed")
        os.fsync(output)
        source_stable()
        item = StagedFile(name, offset, digest.hexdigest())
        _write_json(batch, "manifest.json", {"schema_version": 1, "files": [vars(item)]})
        _write_json(batch, "mtp-completion.json", {"schema_version": 1, "protocol": "SendObject",
                    "session": request.session, "transaction": request.transaction,
                    "object_id": request.object_id, "relative": name, "size": offset, "sha256": item.sha256})
        # Reuse independent ReadyBatch payload verification while still private.
        ReadyBatch(Path(settings.work) / incomplete, (item,)).check_file(item)
        source_stable()
        # Work is broker-private: the target cannot reach partially re-owned data.
        for file_name in ("manifest.json", "mtp-completion.json"):
            child = os.open(file_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=batch)
            try:
                os.fchown(child, settings.target_uid, settings.target_gid)
                os.fchmod(child, 0o600)
                os.fsync(child)
            finally:
                os.close(child)
        for child in reversed(opened[1:]):
            info = os.fstat(child)
            os.fchown(child, settings.target_uid, settings.target_gid)
            os.fchmod(child, 0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
            os.fsync(child)
        os.fsync(batch)
        anchors()
        source_stable()
        os.fchown(batch, settings.target_uid, settings.target_gid)
        os.fchmod(batch, 0o700)
        os.fsync(batch)
        _publish(work, incomplete, staging, ready)
        os.fsync(work)
        os.fsync(staging)
        anchors()
        result = ReadyBatch.load(Path(settings.staging) / ready)
        _require(result.files == (item,), "mtp_published_batch_changed")
        return result
    except MtpIngestError:
        raise
    except BackupError as error:
        raise MtpIngestError("mtp_completion_invalid") from error
    except OSError as error:
        code = "mtp_space_low" if error.errno == errno.ENOSPC else "mtp_completion_io_error"
        raise MtpIngestError(code) from error
    except (ValueError, TypeError, UnicodeError, OverflowError) as error:
        raise MtpIngestError("mtp_completion_invalid") from error
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)
        for _, descriptor, _, _ in reversed(roots):
            os.close(descriptor)
