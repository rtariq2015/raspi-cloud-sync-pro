"""Opt-in 8 GiB capture admission. Observations are not block reservations."""

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath

from .errors import BackupError
from .mass_storage_image import EXPANDED_SIZE, LEGACY_SIZE, validate_image_size

LEGACY_POLICY = 'legacy-16mib'
CAPACITY_POLICY = 'capacity-reserve-v1'
OS_RESERVE = 4 * 1024 ** 3
CONTROL_RESERVE = 16 * 1024 ** 2
INODE_RESERVE = 128
MAX_INTEGER = 2 ** 63 - 1


def require(value, code='capture_space_unknown'):
    if not value:
        raise BackupError(code)


def approved_capture_policy(image_size, name):
    validate_image_size(image_size)
    require(type(name) is str, 'invalid_capture_space_policy')
    if image_size == LEGACY_SIZE and name == LEGACY_POLICY:
        return None
    require(image_size == EXPANDED_SIZE and name == CAPACITY_POLICY, 'invalid_capture_space_policy')
    return CaptureSpacePolicy()


@dataclass(frozen=True)
class CaptureSpacePolicy:
    image_size_bytes: int = EXPANDED_SIZE

    def __post_init__(self):
        require(type(self.image_size_bytes) is int and self.image_size_bytes == EXPANDED_SIZE,
                'invalid_capture_space_policy')

    def admit(self, destination, files):
        return CaptureSpaceAdmission(self, destination, files)


class CaptureSpaceAdmission:
    def __init__(self, policy, destination, files):
        from .staging import StagedFile
        require(type(policy) is CaptureSpacePolicy, 'invalid_capture_space_policy')
        self.policy, self.destination = policy, Path(destination)
        self.files = tuple(files)
        require(len(self.files) <= 10000 and all(type(item) is StagedFile for item in self.files),
                'invalid_capture_space_plan')
        self.expected = {item.relative:item for item in self.files}
        require(len(self.expected) == len(self.files)
                and sum(item.size for item in self.files) <= policy.image_size_bytes,
                'invalid_capture_space_plan')
        self.parents = {str(parent) for item in self.files for parent in PurePosixPath(item.relative).parents
                        if str(parent) != '.'}
        require(len(self.files) + len(self.parents) <= 10000, 'invalid_capture_space_plan')
        self.manifest_bytes = len(json.dumps({'schema_version':1, 'files':[vars(i) for i in self.files]},
                                            ensure_ascii=True, sort_keys=True).encode('ascii'))
        require(self.manifest_bytes <= 1024 ** 2, 'invalid_capture_space_plan')
        self.fd = None
        self.progress, self.credited, self.finished, self.created_parents = {}, {}, set(), set()
        self.unit = self.total = self.identity = None

    def _observe(self):
        try:
            value = os.fstatvfs(self.fd)
            named, opened = self.destination.lstat(), os.fstat(self.fd)
            identity = (opened.st_dev, opened.st_ino)
            require(identity == (named.st_dev, named.st_ino)
                    and (self.identity is None or identity == self.identity))
            fields = ('f_frsize','f_bsize','f_blocks','f_bfree','f_bavail','f_files','f_favail')
            require(all(type(getattr(value,k,None)) is int and 0 <= getattr(value,k) <= MAX_INTEGER for k in fields))
            require(all(512 <= getattr(value,k) <= 1024 ** 2 and getattr(value,k) & (getattr(value,k)-1) == 0
                        for k in ('f_frsize','f_bsize')))
            total = value.f_blocks * value.f_frsize
            require(0 < total <= MAX_INTEGER and 0 <= value.f_bavail <= value.f_bfree <= value.f_blocks
                    and 0 < value.f_files and value.f_favail <= value.f_files)
            unit = max(value.f_frsize, value.f_bsize)
            require(self.unit is None or (unit,self.total) == (self.unit,total))
            self.identity = identity
            return value.f_bavail * value.f_frsize, unit, total, value.f_favail
        except (OSError, AttributeError, TypeError, ValueError) as error:
            raise BackupError('capture_space_unknown') from error

    def __enter__(self):
        try:
            self.fd = os.open(self.destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            _, self.unit, self.total, _ = self._observe()
            rounded = lambda size: ((size+self.unit-1)//self.unit)*self.unit
            self.allocations = {item.relative:rounded(item.size) for item in self.files}
            self.remaining = sum(self.allocations.values())
            # Keep transactional/metadata margin to the end, including manifests,
            # directory/inode estimates and 1% extent/allocation slack.
            self.overhead = (CONTROL_RESERVE + rounded(self.manifest_bytes)
                + 2*self.unit*(len(self.files)+len(self.parents)+3)
                + rounded((self.remaining+99)//100))
            self.check()
            return self
        except BaseException:
            if self.fd is not None:
                os.close(self.fd);self.fd = None
            raise

    def __exit__(self, *unused):
        os.close(self.fd);self.fd = None

    def check(self):
        require(self.fd is not None)
        available, _, _, inodes = self._observe()
        required = self.remaining + self.overhead + self.policy.image_size_bytes + OS_RESERVE
        inode_need = (INODE_RESERVE + len(self.files)-len(self.progress)
                      + len(self.parents-self.created_parents) + 3)
        require(available >= required and inodes >= inode_need, 'capture_space_low')
        return required

    def begin_file(self, relative, size):
        require(relative in self.expected and relative not in self.progress
                and type(size) is int and self.expected[relative].size == size, 'source_changed_during_staging')
        self.progress[relative] = self.credited[relative] = 0
        self.created_parents.update(str(p) for p in PurePosixPath(relative).parents if str(p) != '.')
        self.check()

    def written(self, relative, count):
        require(relative in self.progress and type(count) is int and count > 0,
                'invalid_capture_space_plan')
        self.progress[relative] += count
        require(self.progress[relative] <= self.expected[relative].size, 'source_changed_during_staging')
        credit = self.progress[relative] // self.unit * self.unit
        self.remaining -= credit-self.credited[relative]
        self.credited[relative] = credit
        self.check()

    def finish_file(self, relative, size, sha256):
        require(relative in self.progress and relative not in self.finished
                and type(size) is int and self.progress[relative] == size == self.expected[relative].size
                and sha256 == self.expected[relative].sha256, 'source_changed_during_staging')
        self.remaining -= self.allocations[relative]-self.credited[relative]
        self.finished.add(relative)
        self.check()

    def publication_check(self):
        require(self.finished == set(self.expected) and self.remaining == 0, 'invalid_capture_space_plan')
        self.check()
