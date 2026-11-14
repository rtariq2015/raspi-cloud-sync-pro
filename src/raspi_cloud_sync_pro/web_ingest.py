"""Stage a completed HTTP upload separately from USB images and Samba writers."""

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath

from .errors import BackupError
from .staging import CHUNK_SIZE, ReadyBatch, StagedFile, _fsync_directory, relative_path


def stage_upload(stream, filename, destination, *, max_bytes=100 * 1024 * 1024,
                 reserve_bytes=16 * 1024 * 1024):
    """Retain incomplete uploads; publish only after stream completion and fsync."""
    relative_path(filename)
    if len(PurePosixPath(filename).parts) != 1 or filename.startswith(".") \
            or type(max_bytes) is not int or max_bytes < 1 \
            or type(reserve_bytes) is not int or reserve_bytes < 0:
        raise BackupError("invalid_web_upload")
    destination = Path(destination).absolute()
    if destination.is_symlink():
        raise BackupError("unsafe_staging_roots")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination / (uuid.uuid4().hex + ".incomplete")
    temporary.mkdir(mode=0o700)
    payload = temporary / "files"
    payload.mkdir(mode=0o700)
    try:
        digest, size = hashlib.sha256(), 0
        fd = os.open(payload / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            while True:
                block = stream.read(CHUNK_SIZE)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise BackupError("web_upload_too_large")
                if shutil.disk_usage(destination).free < len(block) + reserve_bytes:
                    raise BackupError("staging_space_low")
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        item = StagedFile(filename, size, digest.hexdigest())
        manifest_fd = os.open(temporary / "manifest.json",
                              os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(manifest_fd, "w", encoding="utf-8") as manifest:
            json.dump({"schema_version": 1, "files": [vars(item)]}, manifest)
            manifest.flush()
            os.fsync(manifest.fileno())
        _fsync_directory(payload)
        _fsync_directory(temporary)
        ready = temporary.with_suffix(".ready")
        temporary.rename(ready)
        _fsync_directory(destination)
        return ReadyBatch(ready, (item,))
    except OSError as exc:
        raise BackupError("web_upload_io_error") from exc
