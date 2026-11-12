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


def hash_stream(stream):
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
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

    def check_file(self, item: StagedFile):
        with open_regular(self.root / "files", item.relative) as stream:
            if hash_stream(stream) != (item.size, item.sha256):
                raise BackupError("staged_file_changed")

    @classmethod
    def load(cls, root: Path, *, verify_contents: bool = True):
        root = Path(root).absolute()
        if root.suffix != ".ready" or root.is_symlink():
            raise BackupError("batch_not_ready")
        try:
            with open_regular(root, "manifest.json") as stream:
                data = json.load(stream)
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
                    batch.check_file(item)
            return batch
        except BackupError:
            raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise BackupError("invalid_ready_batch") from exc


def stage_snapshot(source: Path, destination: Path, ownership: StorageOwnership,
                   *, reserve_bytes: int = 16 * 1024 * 1024) -> ReadyBatch:
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
                        (payload / relative).mkdir(parents=True, exist_ok=True)
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
