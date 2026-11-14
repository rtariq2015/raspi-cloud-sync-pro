"""Copy an exclusively owned snapshot into atomically published ready batches.

Trusted service-owned roots are required. No image mounting or host detection is
implemented here. Incomplete batches are retained on failure, never auto-pruned.
"""

import hashlib
import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Tuple

from .errors import BackupError
from .storage import StorageOwnership

CHUNK_SIZE = 1024 * 1024
_CAPTURE_ENTRY_LIMIT = 10000
_CAPTURE_DEPTH_LIMIT = 32
_CAPTURE_METADATA_LIMIT = 1024 * 1024


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value) or "\\" in value:
        raise BackupError("unsafe_relative_path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(p in (".", "..") for p in path.parts):
        raise BackupError("unsafe_relative_path")
    return value


@contextmanager
def open_regular(root: Path, relative: str):
    """Walk directory descriptors without following symlinks (Linux/POSIX)."""
    parts = PurePosixPath(relative_path(relative)).parts
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BackupError("non_regular_file")
            stream = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            yield stream
    finally:
        os.close(directory_fd)


def hash_stream(stream, *, check=None):
    digest = hashlib.sha256()
    size = 0
    while True:
        if check is not None:
            check()
        block = stream.read(CHUNK_SIZE)
        if check is not None:
            check()
        if not block:
            break
        size += len(block)
        digest.update(block)
    return size, digest.hexdigest()


def _fsync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class StagedFile:
    relative: str
    size: int
    sha256: str

    def __post_init__(self):
        relative_path(self.relative)
        if type(self.size) is not int or self.size < 0 or not isinstance(self.sha256, str) \
                or len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise BackupError("invalid_staged_file")

    @property
    def remote_key(self):
        return f"versions/{self.sha256}/{self.relative}"


@dataclass(frozen=True)
class ReadyBatch:
    root: Path
    files: Tuple[StagedFile, ...]

    def check_file(self, item: StagedFile, *, check=None):
        with open_regular(self.root / "files", item.relative) as stream:
            if hash_stream(stream, check=check) != (item.size, item.sha256):
                raise BackupError("staged_file_changed")

    @classmethod
    def load(cls, root: Path, *, verify_contents: bool = True, check=None):
        if check is not None:
            check()
        root = Path(root).absolute()
        if root.suffix != ".ready" or root.is_symlink():
            raise BackupError("batch_not_ready")
        try:
            with open_regular(root, "manifest.json") as stream:
                data = json.load(stream)
            if check is not None:
                check()
            if set(data) != {"schema_version", "files"} or type(data["schema_version"]) is not int \
                    or data["schema_version"] != 1 or not isinstance(data["files"], list):
                raise ValueError()
            files = []
            paths = set()
            for entry in data["files"]:
                if set(entry) != {"relative", "size", "sha256"}:
                    raise ValueError()
                path = relative_path(entry["relative"])
                digest = entry["sha256"]
                if path in paths or type(entry["size"]) is not int or entry["size"] < 0 \
                        or not isinstance(digest, str) or len(digest) != 64 \
                        or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError()
                paths.add(path)
                files.append(StagedFile(path, entry["size"], digest))
            batch = cls(root, tuple(files))
            if verify_contents:
                for item in batch.files:
                    batch.check_file(item, check=check)
            return batch
        except BackupError:
            raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise BackupError("invalid_ready_batch") from exc


def stage_snapshot(source: Path, destination: Path, ownership: StorageOwnership,
                   *, reserve_bytes: int = 16 * 1024 * 1024, _selected=None) -> ReadyBatch:
    """Copy every regular file under a trusted exclusive read-only source root.

    Caller-supplied evidence in StorageOwnership is a model only, not permission
    to mount or read a live USB filesystem. Hardware integration remains blocked.
    """
    with ownership.read_access():
        try:
            if type(reserve_bytes) is not int or reserve_bytes < 0:
                raise BackupError("invalid_space_reserve")
            source, destination = Path(source).absolute(), Path(destination).absolute()
            if source.is_symlink() or destination.is_symlink():
                raise BackupError("unsafe_staging_roots")
            source, destination = source.resolve(strict=True), destination.resolve()
            if not source.is_dir():
                raise BackupError("source_not_directory")
            if source == destination or source in destination.parents or destination in source.parents:
                raise BackupError("overlapping_staging_roots")
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = destination / (uuid.uuid4().hex + ".incomplete")
            temporary.mkdir(mode=0o700)
            payload = temporary / "files"
            payload.mkdir()
            files = []

            def walk_error(error):
                raise error

            for directory, dirs, names in os.walk(source, followlinks=False, onerror=walk_error):
                for name in sorted(dirs + names):
                    entry = Path(directory) / name
                    relative = relative_path(entry.relative_to(source).as_posix())
                    mode = entry.lstat().st_mode
                    if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                        raise BackupError("unsupported_source_entry")
                    if stat.S_ISDIR(mode):
                        if _selected is None:
                            (payload / relative).mkdir(parents=True, exist_ok=True)
                        continue
                    if _selected is not None and relative not in _selected:
                        continue
                    output = payload / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with open_regular(source, relative) as incoming, output.open("xb") as outgoing:
                        before = os.fstat(incoming.fileno())
                        if shutil.disk_usage(destination).free < before.st_size + reserve_bytes:
                            raise BackupError("staging_space_low")
                        digest = hashlib.sha256()
                        size = 0
                        for block in iter(lambda: incoming.read(CHUNK_SIZE), b""):
                            outgoing.write(block)
                            digest.update(block)
                            size += len(block)
                        after = os.fstat(incoming.fileno())
                        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != \
                                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size:
                            raise BackupError("source_changed_during_staging")
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    files.append(StagedFile(relative, size, digest.hexdigest()))
            files.sort(key=lambda f: f.relative)
            manifest = {"schema_version": 1, "files": [vars(f) for f in files]}
            with (temporary / "manifest.json").open("x", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=True, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            for directory, _, _ in os.walk(temporary, topdown=False):
                _fsync_directory(Path(directory))
            ready = temporary.with_suffix(".ready")
            temporary.rename(ready)
            _fsync_directory(destination)
            return ReadyBatch(ready, tuple(files))
        except BackupError:
            raise
        except OSError as exc:
            # Includes ENOSPC; no cursor advances and partial batches remain for recovery.
            raise BackupError("staging_io_error") from exc


def _capture_inventory(source, *, contents):
    """Caller holds exclusive read-only ownership; metadata also detects test races."""
    files, fingerprints, budget = [], {}, [_CAPTURE_ENTRY_LIMIT - 2, _CAPTURE_METADATA_LIMIT]
    def fingerprint(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    def walk(directory, prefix, depth):
        if depth > _CAPTURE_DEPTH_LIMIT:
            raise BackupError('capture_inventory_limit')
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if budget[0] <= 0:
                    raise BackupError('capture_inventory_limit')
                budget[0] -= 1;names.append(entry.name)
        for name in sorted(names):
            relative = relative_path(prefix + '/' + name if prefix else name)
            budget[1] -= len(relative.encode('utf-8')) + 128
            if budget[1] < 0:
                raise BackupError('capture_inventory_limit')
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise BackupError('unsupported_source_entry')
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    walk(child, relative, depth + 1)
                finally:
                    os.close(child)
                continue
            if contents:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, 'rb') as incoming:
                    before = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(before.st_mode) or fingerprint(before) != fingerprint(info):
                        raise BackupError('source_changed_during_staging')
                    size, digest = hash_stream(incoming)
                    info = os.fstat(incoming.fileno())
                    if fingerprint(before) != fingerprint(info) or size != before.st_size:
                        raise BackupError('source_changed_during_staging')
                files.append(StagedFile(relative, size, digest))
            fingerprints[relative] = fingerprint(info)
    root = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(root, '', 1)  # Reserve the staged batch's files/ directory level.
    finally:
        os.close(root)
    return tuple(sorted(files, key=lambda item: item.relative)), fingerprints


def stage_changed_snapshot(source, destination, ownership, previous_files=None, *, reserve_bytes=16 * 1024 * 1024):
    """Hash all current files, copy only new/changed versions, or return no batch.

    The returned full inventory means local capture only, never handoff or cloud
    verification. The controller must retain it privately after clean release.
    It cannot be supplied by an untrusted writer as permission to skip capture.
    """
    with ownership.read_access():
        try:
            if type(reserve_bytes) is not int or reserve_bytes < 0:
                raise BackupError('invalid_space_reserve')
            source, destination = Path(source).absolute(), Path(destination).absolute()
            if source.is_symlink() or destination.is_symlink():
                raise BackupError('unsafe_staging_roots')
            source, destination = source.resolve(strict=True), destination.resolve()
            if not source.is_dir() or source == destination or source in destination.parents or destination in source.parents:
                raise BackupError('overlapping_staging_roots')
            previous = {}
            for item in previous_files or ():
                if not isinstance(item, StagedFile) or item.relative in previous:
                    raise BackupError('invalid_capture_manifest')
                previous[item.relative] = item
            current, fingerprints = _capture_inventory(source, contents=True)
            if len(json.dumps([vars(item) for item in current], ensure_ascii=True).encode('ascii')) > _CAPTURE_METADATA_LIMIT - 4096:
                raise BackupError('capture_inventory_limit')
            changed = tuple(item for item in current if previous.get(item.relative) != item)
            batch = stage_snapshot(source, destination, ownership, reserve_bytes=reserve_bytes,
                                   _selected={item.relative for item in changed}) if changed else None
            final = _capture_inventory(source, contents=False)[1]
            # FAT/exFAT inode numbers are lookup/cache identities, not persistent
            # file identities across reopen. Native exFAT reproduced inode-only
            # differences under an exclusive RO mount. Keep dev/size/timestamps
            # across passes; the full identity checks within an open FD remain.
            across_passes = lambda values: {path: value[:1] + value[2:] for path, value in values.items()}
            if (batch is not None and batch.files != changed) or across_passes(final) != across_passes(fingerprints):
                raise BackupError('source_changed_during_staging')
            return batch, current
        except BackupError:
            raise
        except OSError as error:
            raise BackupError('staging_io_error') from error
