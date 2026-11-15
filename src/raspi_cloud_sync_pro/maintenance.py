"""Private one-shot maintenance intent; never evidence of Windows release.

The owning controller serializes USB operations. This separate short lock lets
an authenticated administrator submit a request without taking its lifetime lock.
Unresolved/partial records always block export, including when admission is off.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
import uuid

from .errors import BackupError


def require(value):
    if not value:
        raise BackupError('maintenance_review_required')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result);result[key] = value
    return result


class MaintenanceHold:
    def __init__(self, directory, backend, *, enabled=False):
        require(type(enabled) is bool)
        self.directory, self.backend, self.enabled = Path(directory), backend, enabled
        self.session_id = None
        self._mutex, self._fd = RLock(), None

    def _identity(self):
        value = getattr(self.backend, 'image_identity', None)
        require(type(value) in (list, tuple) and len(value) == 3
                and all(type(i) is int and i >= 0 for i in value) and value[2] > 0)
        return list(value)

    @contextmanager
    def locked(self):
        with self._mutex:
            if self._fd is not None:
                yield self
                return
            info = self.directory.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) == 0o700)
            fd = os.open(self.directory/'maintenance.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                        and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise BackupError('maintenance_admission_busy') from error
                self._fd = fd
                yield self
            finally:
                self._fd = None
                os.close(fd)

    def _read(self, name):
        try:
            fd = os.open(self.directory/name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                    and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                    and before.st_size <= 4096)
            raw = os.read(fd, 4097)
            require(len(raw) == before.st_size)
            value = json.loads(raw, object_pairs_hook=_unique)
            signature = lambda i: (i.st_dev, i.st_ino, i.st_size, i.st_mtime_ns, i.st_ctime_ns)
            require(signature(before) == signature(os.fstat(fd)) == signature((self.directory/name).lstat()))
            return value
        except (ValueError, TypeError) as error:
            raise BackupError('maintenance_review_required') from error
        finally:
            os.close(fd)

    def _write(self, name, value):
        require(self._fd is not None)
        temporary = self.directory/(name+'.pending')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, sort_keys=True, allow_nan=False)
            output.flush();os.fsync(output.fileno())
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
            os.replace(temporary, self.directory/name)
            os.fsync(directory)
        finally:
            os.close(directory)

    def _base(self):
        return {'schema_version': 1, 'image': str(self.backend.image), 'image_identity': self._identity()}

    def record(self):
        require(self._fd is not None)
        for name in ('maintenance.json.pending', 'maintenance-session.json.pending'):
            require(not os.path.lexists(self.directory/name))
        value = self._read('maintenance.json')
        if value is None:
            return None
        require(type(value) is dict and set(value) == set(self._base()) | {'session_id','request_id','phase','capture_sha256'})
        require(json.dumps({k:value[k] for k in self._base()}, sort_keys=True) == json.dumps(self._base(), sort_keys=True))
        require(all(type(value[k]) is str and re.fullmatch('[0-9a-f]{32}', value[k]) for k in ('session_id','request_id'))
                and value['phase'] in ('pending','capturing','captured','active','releasing','released'))
        require(value['capture_sha256'] is None if value['phase'] in ('pending','capturing') else
                type(value['capture_sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['capture_sha256']))
        return value

    def assert_normal(self):
        value = self.record()
        require(value is None or value['phase'] == 'released')
        self.validate_challenge()

    def validate_challenge(self):
        previous = self._read('maintenance-session.json')
        if previous is not None:
            require(type(previous) is dict and set(previous) == set(self._base()) | {'session_id'}
                    and json.dumps({k:previous[k] for k in self._base()},sort_keys=True) == json.dumps(self._base(),sort_keys=True)
                    and type(previous['session_id']) is str and re.fullmatch('[0-9a-f]{32}', previous['session_id']))
        return previous

    def open_session(self):
        require(self._fd is not None)
        self.assert_normal()
        if self.enabled:
            self.session_id = uuid.uuid4().hex
            self._write('maintenance-session.json', {**self._base(), 'session_id':self.session_id})

    def submit(self):
        """Called through the root-approved appliance action; no USB commands."""
        with self.locked():
            require(self.enabled and self.record() is None)
            # A persisted challenge from a stopped controller is not a request
            # endpoint. Probe its existing lifetime lock without taking it over.
            fd = os.open(self.directory/'controller.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                        and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise BackupError('maintenance_controller_not_running')
            finally:
                os.close(fd)
            value = self.validate_challenge()
            require(value is not None)
            request = {**value, 'request_id':uuid.uuid4().hex, 'phase':'pending', 'capture_sha256':None}
            self._write('maintenance.json', request)
            return request['request_id']

    def pending(self):
        value = self.record()
        if value is None or value['phase'] == 'released':
            return None
        require(self.enabled and self.session_id == value['session_id']
                and value['phase'] in ('pending','capturing'))
        return value

    def transition(self, value, phase, *, capture_sha256=None):
        require(self.record() == value)
        changed = {**value, 'phase':phase, 'capture_sha256':capture_sha256 if capture_sha256 is not None else value['capture_sha256']}
        self._write('maintenance.json', changed)
        return changed
