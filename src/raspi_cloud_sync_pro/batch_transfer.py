"""Private streaming transfer of selected completed web uploads between UIDs.

The trusted coordinator selects a batch ID and supplies its independently obtained
canonical manifest hash. All source opens stay below an approved web-inbox anchor;
no dashboard auth/OAuth/inbox access is granted to the backup identity. Accepted
anchors/channels are consumed. Incomplete and existing data are always retained.
No process, network, provider, chmod sharing, upload or deletion is performed.
"""

import ctypes
import errno
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import stat
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath

from . import google_destination as private
from .config import _unique_object
from .errors import BackupError
from .staging import relative_path

DEFAULT_TIMEOUT = 300.0
MAX_TIMEOUT = 3600.0
CHUNK_BYTES = 65536
MAX_METADATA_BYTES = 65536
MAX_FILES = 128
MAX_DEPTH = 8
MAX_PATH_BYTES = 1024
MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
RESERVE_BYTES = 16 * 1024 * 1024
_ID = re.compile(r'[0-9a-f]{32}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_FAILURES = (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError)


class BatchTransferError(BackupError):
    """Fixed codes only; paths, names, payloads and raw errors stay private."""


def _fail(code):
    raise BatchTransferError(code) from None


def _deadline(timeout):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= MAX_TIMEOUT):
        _fail('batch_transfer_timeout_invalid')
    return time.monotonic() + timeout


def _check_time(deadline):
    if time.monotonic() >= deadline:
        _fail('batch_transfer_timeout')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(fd):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        _fail('batch_transfer_path_unsafe')
    return info


def _file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        _fail('batch_transfer_path_unsafe')
    return info


def _batch_id(value):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        _fail('batch_transfer_identity_invalid')
    return value


def _digest(value):
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail('batch_transfer_identity_invalid')
    return value


def _canonical(value):
    return private._canonical(value)


def _path(value):
    try:
        value = relative_path(value)
        parts = PurePosixPath(value).parts
        if (len(parts) > MAX_DEPTH or len(value.encode('utf-8')) > MAX_PATH_BYTES
                or any(len(part.encode('utf-8')) > 255 for part in parts)):
            _fail('batch_transfer_manifest_invalid')
        return value
    except BackupError:
        _fail('batch_transfer_manifest_invalid')


def _manifest(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'files'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['files'], list) or len(value['files']) > MAX_FILES):
        _fail('batch_transfer_manifest_invalid')
    files, paths, total = [], set(), 0
    for item in value['files']:
        if not isinstance(item, dict) or set(item) != {'relative', 'size', 'sha256'}:
            _fail('batch_transfer_manifest_invalid')
        name = _path(item['relative'])
        if (name in paths or type(item['size']) is not int or not 0 <= item['size'] <= MAX_FILE_BYTES
                or not isinstance(item['sha256'], str) or _HASH.fullmatch(item['sha256']) is None):
            _fail('batch_transfer_manifest_invalid')
        paths.add(name)
        total += item['size']
        if total > MAX_TOTAL_BYTES:
            _fail('batch_transfer_manifest_invalid')
        files.append({'relative': name, 'size': item['size'], 'sha256': item['sha256']})
    for name in paths:
        if any('/'.join(PurePosixPath(name).parts[:index]) in paths
               for index in range(1, len(PurePosixPath(name).parts))):
            _fail('batch_transfer_manifest_invalid')
    result = {'schema_version': 1, 'files': files}
    raw = _canonical(result)
    if not 0 < len(raw) <= MAX_METADATA_BYTES:
        _fail('batch_transfer_manifest_invalid')
    return result, raw, hashlib.sha256(raw).hexdigest(), total


def _parse(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail('batch_transfer_protocol_invalid')


def _read_manifest(batch):
    fd = os.open('manifest.json', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=batch)
    try:
        info = _file(os.fstat(fd))
        if not 0 < info.st_size <= MAX_METADATA_BYTES:
            _fail('batch_transfer_manifest_invalid')
        data = bytearray()
        while len(data) <= MAX_METADATA_BYTES:
            block = os.read(fd, min(8192, MAX_METADATA_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        if (len(data) != info.st_size or _identity(os.fstat(fd)) != _identity(info)
                or _identity(os.stat('manifest.json', dir_fd=batch, follow_symlinks=False)) != _identity(info)):
            _fail('batch_transfer_source_changed')
        manifest, canonical, digest, total = _manifest(_parse(data))
        return manifest, canonical, digest, total, bytes(data), _identity(info)
    finally:
        os.close(fd)


@contextmanager
def _anchor(path, fd):
    accepted = False
    try:
        if type(fd) is not int or fd < 3:
            _fail('batch_transfer_anchor_invalid')
        info = _directory(fd)
        for standard in (0, 1, 2):
            try:
                if private._descriptor_identity(fd) == private._descriptor_identity(standard):
                    _fail('batch_transfer_anchor_invalid')
            except OSError:
                pass
        with private._private_directory(path) as configured:
            other = os.fstat(configured)
            if (info.st_dev, info.st_ino) != (other.st_dev, other.st_ino):
                _fail('batch_transfer_anchor_invalid')
        accepted = True
        yield fd
    finally:
        if accepted:
            os.close(fd)


def _recheck_anchor(path, fd):
    info = _directory(fd)
    with private._private_directory(path) as configured:
        other = os.fstat(configured)
        if (info.st_dev, info.st_ino) != (other.st_dev, other.st_ino):
            _fail('batch_transfer_source_changed')


@contextmanager
def _accepted(path, root_fd, channel_fd, *, writing):
    with ExitStack() as stack:
        root = channel = None
        failed = False
        try:
            root = stack.enter_context(_anchor(path, root_fd))
        except (BatchTransferError, private.GoogleDestinationError) + _FAILURES:
            failed = True
        try:
            channel = stack.enter_context(private._channel(channel_fd, writing=writing))
        except private.GoogleDestinationError:
            failed = True
        if failed:
            _fail('batch_transfer_descriptor_invalid')
        yield root, channel


@contextmanager
def _batch(root, name):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    files = None
    try:
        info = _directory(fd)
        files = os.open('files', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        _directory(files)
        yield fd, files
        if _identity(os.stat(name, dir_fd=root, follow_symlinks=False)) != _identity(info):
            _fail('batch_transfer_source_changed')
    finally:
        if files is not None:
            os.close(files)
        os.close(fd)


@contextmanager
def _regular(root, name):
    parts = PurePosixPath(_path(name)).parts
    directory = os.dup(root)
    fd = None
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                _directory(child)
            except BaseException:
                os.close(child)
                raise
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        before = _file(os.fstat(fd))
        yield fd, before
        if (_identity(os.fstat(fd)) != _identity(before)
                or _identity(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)) != _identity(before)):
            _fail('batch_transfer_source_changed')
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def _inventory(batch, files, manifest):
    names = set()
    with os.scandir(batch) as entries:
        for entry in entries:
            if entry.name not in ('manifest.json', 'files'):
                _fail('batch_transfer_manifest_invalid')
            names.add(entry.name)
    if names != {'manifest.json', 'files'}:
        _fail('batch_transfer_manifest_invalid')
    expected_files = {item['relative']: item for item in manifest['files']}
    expected_dirs = {''}
    for name in expected_files:
        parts = PurePosixPath(name).parts
        expected_dirs.update('/'.join(parts[:index]) for index in range(1, len(parts)))
    result = {('batch', ''): _identity(_directory(batch)), ('directory', ''): _identity(_directory(files))}
    seen_files, seen_dirs = set(), {''}
    def walk(fd, prefix):
        with os.scandir(fd) as entries:
            for entry in entries:
                if len(result) > MAX_FILES * (MAX_DEPTH + 1) + 4:
                    _fail('batch_transfer_manifest_invalid')
                name = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if name not in expected_dirs:
                        _fail('batch_transfer_manifest_invalid')
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if _identity(_directory(child)) != _identity(info):
                            _fail('batch_transfer_source_changed')
                        result[('directory', name)] = _identity(info)
                        seen_dirs.add(name)
                        walk(child, name + '/')
                        if _identity(os.fstat(child)) != _identity(info):
                            _fail('batch_transfer_source_changed')
                    finally:
                        os.close(child)
                else:
                    _file(info)
                    if name not in expected_files or info.st_size != expected_files[name]['size']:
                        _fail('batch_transfer_manifest_invalid')
                    result[('file', name)] = _identity(info)
                    seen_files.add(name)
    walk(files, '')
    if seen_files != set(expected_files) or seen_dirs != expected_dirs:
        _fail('batch_transfer_manifest_invalid')
    return result


class _Wire:
    def __init__(self, fd, deadline, *, writing):
        self.fd, self.deadline, self.writing = fd, deadline, writing
        self.selector = selectors.DefaultSelector()
        try:
            self.selector.register(fd, selectors.EVENT_WRITE if writing else selectors.EVENT_READ)
        except BaseException:
            self.selector.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *ignored):
        self.selector.close()

    def read_some(self, count):
        while True:
            private._wait(self.selector, self.deadline)
            try:
                block = os.read(self.fd, min(CHUNK_BYTES, count))
            except BlockingIOError:
                continue
            if not block:
                _fail('batch_transfer_incomplete')
            return block

    def read(self, count):
        result = bytearray()
        while len(result) < count:
            result.extend(self.read_some(count - len(result)))
        return bytes(result)

    def write(self, block):
        offset = 0
        while offset < len(block):
            private._wait(self.selector, self.deadline)
            try:
                count = os.write(self.fd, block[offset:offset + CHUNK_BYTES])
            except BlockingIOError:
                continue
            if count <= 0:
                _fail('batch_transfer_incomplete')
            offset += count


def _header(batch_id, manifest, digest):
    value = {'schema_version': 1, 'kind': 'web_ready_batch', 'batch_id': batch_id,
             'manifest_sha256': digest, 'manifest': manifest}
    if len(_canonical(value)) > MAX_METADATA_BYTES:
        _fail('batch_transfer_manifest_invalid')
    return value


def _read_header(wire, batch_id, expected):
    length = int.from_bytes(wire.read(4), 'big')
    if not 0 < length <= MAX_METADATA_BYTES:
        _fail('batch_transfer_protocol_invalid')
    value = _parse(wire.read(length))
    if (not isinstance(value, dict)
            or set(value) != {'schema_version', 'kind', 'batch_id', 'manifest_sha256', 'manifest'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'web_ready_batch' or value['batch_id'] != batch_id
            or value['manifest_sha256'] != expected):
        _fail('batch_transfer_identity_invalid')
    manifest, raw, digest, total = _manifest(value['manifest'])
    if digest != expected:
        _fail('batch_transfer_identity_invalid')
    return manifest, raw, total


def _completion(batch_id, digest):
    return {'schema_version': 1, 'kind': 'web_ready_batch_complete',
            'batch_id': batch_id, 'manifest_sha256': digest}


def _hash_file(files, item, deadline, outgoing=None):
    digest, size = hashlib.sha256(), 0
    with _regular(files, item['relative']) as (fd, before):
        if before.st_size != item['size']:
            _fail('batch_transfer_source_changed')
        while True:
            _check_time(deadline)
            block = os.read(fd, CHUNK_BYTES)
            if not block:
                break
            size += len(block)
            if size > item['size']:
                _fail('batch_transfer_source_changed')
            digest.update(block)
            if outgoing is not None:
                outgoing.write(block)
    if size != item['size'] or digest.hexdigest() != item['sha256']:
        _fail('batch_transfer_content_mismatch')


def _check_complete(root, name, expected, deadline):
    with _batch(root, name) as (batch, files):
        manifest, raw, digest, _, original, identity = _read_manifest(batch)
        if digest != expected:
            _fail('batch_transfer_target_mismatch')
        before = _inventory(batch, files, manifest)
        for item in manifest['files']:
            _hash_file(files, item, deadline)
        after = _read_manifest(batch)
        if (after[4:] != (original, identity) or _inventory(batch, files, manifest) != before):
            _fail('batch_transfer_source_changed')
        return manifest, raw


def _create_file(files, relative):
    parts = PurePosixPath(_path(relative)).parts
    directory = os.dup(files)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
                os.fsync(directory)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                _directory(child)
            except BaseException:
                os.close(child)
                raise
            os.close(directory)
            directory = child
        return os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                       0o600, dir_fd=directory)
    finally:
        os.close(directory)


def _write_bytes(fd, block):
    offset = 0
    while offset < len(block):
        count = os.write(fd, block[offset:])
        if count <= 0:
            _fail('batch_transfer_incomplete')
        offset += count


def _flush_tree(fd, deadline, *, depth=0, budget=None):
    # The backup-owned tree may be changed by another process. Never recursively
    # walk an arbitrary injected hierarchy or let an expanding directory bypass
    # the transfer's deadline before independent verification rejects it.
    _check_time(deadline)
    _directory(fd)
    if depth > MAX_DEPTH:
        _fail('batch_transfer_manifest_invalid')
    if budget is None:
        budget = [MAX_FILES * (MAX_DEPTH + 1) + 4]
    with os.scandir(fd) as entries:
        for entry in entries:
            _check_time(deadline)
            budget[0] -= 1
            if budget[0] < 0:
                _fail('batch_transfer_manifest_invalid')
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    _directory(child)
                    _flush_tree(child, deadline, depth=depth + 1, budget=budget)
                finally:
                    os.close(child)
            else:
                _file(info)
    _check_time(deadline)
    os.fsync(fd)
    _check_time(deadline)


def _publish(root, incomplete, ready):
    library = ctypes.CDLL(None, use_errno=True)
    try:
        rename = library.renameat2
    except AttributeError:
        _fail('batch_transfer_publication_unavailable')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(root, incomplete.encode('ascii'), root, ready.encode('ascii'), 1):
        if ctypes.get_errno() in (errno.EEXIST, errno.ENOTEMPTY):
            return False
        _fail('batch_transfer_publication_failed')
    return True


def _report(state, *, existing=False):
    return {'schema_version': 1, 'transfer_state': state, 'already_present': existing,
            'uploads_enabled': False, 'automatic_deletion': False}


def _convert(error):
    if isinstance(error, private.GoogleDestinationError):
        code = {
            'google_handoff_timeout': 'batch_transfer_timeout',
            'google_handoff_channel_invalid': 'batch_transfer_descriptor_invalid',
            'google_handoff_incomplete': 'batch_transfer_incomplete',
            'google_handoff_invalid': 'batch_transfer_protocol_invalid',
            'google_handoff_too_large': 'batch_transfer_protocol_invalid',
            'google_preparation_path_unsafe': 'batch_transfer_path_unsafe',
            'google_preparation_requires_unprivileged_identity': 'batch_transfer_requires_unprivileged_identity',
        }.get(error.code, 'batch_transfer_unavailable')
    else:
        code = 'batch_transfer_unavailable'
    _fail(code)


def describe_web_batch(web_inbox, batch_id, root_fd, *, timeout=DEFAULT_TIMEOUT):
    """Return PRIVATE authoritative canonical-manifest metadata, never a public status."""
    try:
        private._unprivileged()
        deadline = _deadline(timeout)
        with _anchor(web_inbox, root_fd) as root:
            batch_id = _batch_id(batch_id)
            if Path(web_inbox).name != 'web-inbox':
                _fail('batch_transfer_source_invalid')
            with _batch(root, batch_id + '.ready') as (batch, files):
                manifest, _, digest, total, _, _ = _read_manifest(batch)
                _inventory(batch, files, manifest)
                _header(batch_id, manifest, digest)
                _check_time(deadline)
                _recheck_anchor(web_inbox, root)
                return {'schema_version': 1, 'batch_id': batch_id, 'manifest_sha256': digest,
                        'file_count': len(manifest['files']), 'total_bytes': total}
    except BatchTransferError:
        raise
    except (private.GoogleDestinationError,) + _FAILURES as error:
        _convert(error)


def export_web_batch(web_inbox, batch_id, root_fd, output_fd, expected_manifest_sha256,
                     *, timeout=DEFAULT_TIMEOUT):
    """Consume anchor/channel; export only selected ready data and a completion proof."""
    try:
        private._unprivileged()
        deadline = _deadline(timeout)
        with _accepted(web_inbox, root_fd, output_fd, writing=True) as (root, channel):
            batch_id, expected = _batch_id(batch_id), _digest(expected_manifest_sha256)
            if Path(web_inbox).name != 'web-inbox':
                _fail('batch_transfer_source_invalid')
            with _batch(root, batch_id + '.ready') as (batch, files):
                manifest, _, digest, _, original, identity = _read_manifest(batch)
                if digest != expected:
                    _fail('batch_transfer_identity_invalid')
                before = _inventory(batch, files, manifest)
                private._write_frame(channel, _canonical(_header(batch_id, manifest, digest)), deadline)
                with _Wire(channel, deadline, writing=True) as wire:
                    for item in manifest['files']:
                        _hash_file(files, item, deadline, outgoing=wire)
                if (_read_manifest(batch)[4:] != (original, identity)
                        or _inventory(batch, files, manifest) != before):
                    _fail('batch_transfer_source_changed')
                current = os.stat(batch_id + '.ready', dir_fd=root, follow_symlinks=False)
                if _identity(current) != before[('batch', '')]:
                    _fail('batch_transfer_source_changed')
                _recheck_anchor(web_inbox, root)
                private._write_frame(channel, _canonical(_completion(batch_id, digest)), deadline)
        return _report('exported')
    except BatchTransferError:
        raise
    except (private.GoogleDestinationError,) + _FAILURES as error:
        _convert(error)


def import_web_batch(staging, batch_id, root_fd, input_fd, expected_manifest_sha256,
                     *, timeout=DEFAULT_TIMEOUT):
    """Consume a private stream, independently verify it, and publish without replacement."""
    try:
        private._unprivileged()
        deadline = _deadline(timeout)
        with _accepted(staging, root_fd, input_fd, writing=False) as (root, channel):
            batch_id, expected = _batch_id(batch_id), _digest(expected_manifest_sha256)
            ready = batch_id + '.ready'
            with _Wire(channel, deadline, writing=False) as wire:
                manifest, canonical, total = _read_header(wire, batch_id, expected)
                existing = False
                try:
                    os.stat(ready, dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    _check_complete(root, ready, expected, deadline)
                    existing = True
                if not existing:
                    space = os.fstatvfs(root)
                    if space.f_bavail * space.f_frsize < total + RESERVE_BYTES:
                        _fail('batch_transfer_space_low')
                    incomplete = '.' + batch_id + '-' + secrets.token_hex(16) + '.incomplete'
                    os.mkdir(incomplete, mode=0o700, dir_fd=root)
                    os.fsync(root)
                    batch = os.open(incomplete, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                    files = None
                    try:
                        _directory(batch)
                        os.mkdir('files', mode=0o700, dir_fd=batch)
                        files = os.open('files', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=batch)
                        _directory(files)
                        _receive_files(wire, files, manifest, deadline)
                        _receive_completion(channel, batch_id, expected, deadline)
                        private._write_private(batch, 'manifest.json', canonical)
                        _flush_tree(files, deadline)
                        os.fsync(batch)
                        os.fsync(root)
                        _check_complete(root, incomplete, expected, deadline)
                        _recheck_anchor(staging, root)
                        _check_time(deadline)
                        if not _publish(root, incomplete, ready):
                            _check_complete(root, ready, expected, deadline)
                            existing = True
                        _check_complete(root, ready, expected, deadline)
                        os.fsync(root)
                    finally:
                        if files is not None:
                            os.close(files)
                        os.close(batch)
                else:
                    _receive_files(wire, None, manifest, deadline)
                    _receive_completion(channel, batch_id, expected, deadline)
                    _check_complete(root, ready, expected, deadline)
                    _recheck_anchor(staging, root)
            # Publication uses the anchored FD. A concurrent rename can leave
            # valid retained data under an old root; it must not be reported as
            # a completed handoff into the configured staging path. A final
            # fsync returning after the deadline likewise retains data but fails.
            _check_time(deadline)
            _recheck_anchor(staging, root)
            _check_time(deadline)
        return _report('imported', existing=existing)
    except BatchTransferError:
        raise
    except (private.GoogleDestinationError,) + _FAILURES as error:
        _convert(error)


def _receive_files(wire, files, manifest, deadline):
    for item in manifest['files']:
        fd = _create_file(files, item['relative']) if files is not None else None
        try:
            if fd is not None:
                _file(os.fstat(fd))
            remaining, digest = item['size'], hashlib.sha256()
            while remaining:
                _check_time(deadline)
                block = wire.read_some(min(CHUNK_BYTES, remaining))
                digest.update(block)
                if fd is not None:
                    _write_bytes(fd, block)
                remaining -= len(block)
            if digest.hexdigest() != item['sha256']:
                _fail('batch_transfer_content_mismatch')
        finally:
            if fd is not None:
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)


def _receive_completion(channel, batch_id, expected, deadline):
    value = private._read_frame(channel, deadline)
    if (value != _completion(batch_id, expected) or not isinstance(value, dict)
            or type(value.get('schema_version')) is not int):
        _fail('batch_transfer_protocol_invalid')
