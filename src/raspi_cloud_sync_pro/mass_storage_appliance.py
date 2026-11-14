"""Approved-test-build MSC capture loop; no provider or network operations.

The public build is disabled. A separately reviewed protected test build may
enable this module alone; linux_gadget.HARDWARE_OPERATIONS_ENABLED stays False.
Only a completed root-private snapshot changes ownership. Originals and failed
handoffs remain on disk; the independent backup worker owns cloud processing.
"""

import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import time

from .errors import BackupError
from .gadget import GadgetController
from .google_broker import _root_directory, _root_file, _service_identity, _state_directory
from .linux_gadget import GadgetSettings, LinuxGadgetBackend, LiveLinuxIO

APPROVED_TEST_BUILD = False
CONFIG = Path('/etc/raspi-cloud-sync-pro/mass-storage-appliance.json')
APPROVAL = Path('/etc/raspi-cloud-sync-pro/MASS_STORAGE_APPLIANCE_APPROVED')
IMAGE = Path('/var/lib/raspi-cloud-sync-pro/mass-storage-trial/disk.img')
STATE = Path('/var/lib/raspi-cloud-sync-pro/mass-storage-controller')
PRIVATE_STAGING = Path('/var/lib/raspi-cloud-sync-pro/mass-storage-controller-staging')
STAGING = Path('/var/lib/raspi-cloud-sync-pro/mass-storage-staging')
MOUNT = Path('/run/raspi-cloud-mass-storage-controller')
NAME, SERIAL = 'raspi-cloud-disposable-msc', 'RCSP-DISPOSABLE-ONLY'
ROOT_UID = 0
_MAX_ENTRIES = 10000
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': '/nonexistent'}


def _require(value):
    if not value:
        raise BackupError('mass_storage_appliance_refused')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result);result[key] = value
    return result


def _json(path):
    with _root_file(path, private=True) as (fd, info):
        _require(info.st_size <= 16384)
        raw = os.read(fd, 16385)
        _require(len(raw) == info.st_size)
        return json.loads(raw, object_pairs_hook=_unique)


def _settings():
    value = _json(CONFIG)
    _require(type(value) is dict and set(value) == {'schema_version', 'enabled', 'udc', 'serial', 'approval_file'}
             and type(value['schema_version']) is int and value['schema_version'] == 1 and type(value['enabled']) is bool)
    if not value['enabled']:
        return None
    _require(value['serial'] == SERIAL and value['approval_file'] == str(APPROVAL))
    settings = GadgetSettings(IMAGE, MOUNT, STATE, PRIVATE_STAGING, value['udc'], name=NAME, serial=SERIAL)
    return settings


def _approved(settings):
    _require(APPROVED_TEST_BUILD is True and os.getresuid() == (0, 0, 0) and os.getresgid() == (0, 0, 0))
    _require(platform.system() == 'Linux' and platform.machine() == 'aarch64'
             and Path('/proc/device-tree/model').read_bytes().rstrip(b'\0') == b'Raspberry Pi Zero 2 W Rev 1.0')
    _require(os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'))
    _require(_settings() == settings)
    approval = _json(APPROVAL)
    expected = {'schema_version': 1, 'mass_storage_appliance_approved': True,
                'image_identity': list(LiveLinuxIO().regular_image(IMAGE))}
    _require(json.dumps(approval, sort_keys=True) == json.dumps(expected, sort_keys=True)
             and expected['image_identity'][2] == 1024 ** 3)


class _ApplianceIO(LiveLinuxIO):
    def __init__(self, settings):
        self.settings = settings
        self.root = Path('/sys/kernel/config/usb_gadget') / NAME

    def require_mutations(self):
        _approved(self.settings)

    def _command(self, arguments, allowed):
        # No inherited loaders/proxies, shell, module, service or network tools.
        _require(arguments[0] in allowed and arguments[0] in {'losetup', 'lsblk', 'mount', 'umount', 'fsck.exfat', 'vcgencmd', 'findmnt'})
        import shutil
        executable = Path(shutil.which(arguments[0], path=ENV['PATH']) or '/missing').resolve(strict=True)
        with _root_file(executable, executable=True):
            result = subprocess.run([str(executable)] + arguments[1:], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=60, check=False, env=ENV)
        _require(len(result.stdout) <= 65536 and len(result.stderr) <= 65536)
        return result

    def write(self, path, value):
        allowed = {self.root / 'UDC': {'', self.settings.udc},
                   self.root / 'functions/mass_storage.0/lun.0/file': {str(IMAGE)}}
        _require(Path(path) in allowed and value in allowed[Path(path)])
        super().write(path, value)

    def mkdir(self, path):
        raise BackupError('mass_storage_appliance_existing_profile_required')

    def symlink(self, source, destination):
        raise BackupError('mass_storage_appliance_existing_profile_required')

    def provision_command(self, arguments):
        raise BackupError('mass_storage_appliance_provisioning_forbidden')


class _ApplianceBackend(LinuxGadgetBackend):
    def check_filesystem(self, loop):
        seen = self.observe()
        _require(not seen.bound and not seen.backing and seen.loops == (loop,) and not seen.mounts)
        super().check_filesystem(loop)
        fd = os.open(IMAGE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_uid == ROOT_UID and before.st_nlink == 1
                     and (before.st_dev, before.st_ino, before.st_size) == self.image_identity)
            mbr, boot = os.pread(fd, 512, 0), os.pread(fd, 512, 1024 * 1024)
            _require(len(mbr) == len(boot) == 512 and mbr[510:] == boot[510:] == b'\x55\xaa'
                and mbr[450] == 7 and int.from_bytes(mbr[454:458], 'little') == 2048
                and int.from_bytes(mbr[458:462], 'little') == before.st_size // 512 - 2048
                and not any(mbr[462:510]) and boot[3:11] == b'EXFAT   '
                and int.from_bytes(boot[106:108], 'little') & 6 == 0)
            after = os.fstat(fd)
            _require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) ==
                     (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns))
        finally:
            os.close(fd)


def _directory(fd, owner):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner and stat.S_IMODE(info.st_mode) == 0o700)
    return info.st_dev, info.st_ino


def _inventory(root):
    result = {}
    def walk(parent, relative, depth):
        _require(depth <= 32 and len(result) <= _MAX_ENTRIES)
        for name in sorted(os.listdir(parent)):
            _require(len(result) < _MAX_ENTRIES)
            path = relative + '/' + name if relative else name
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _require(before.st_uid == ROOT_UID and not stat.S_ISLNK(before.st_mode)
                and (stat.S_ISDIR(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o700
                     or stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600))
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                current = os.fstat(fd)
                _require((current.st_dev, current.st_ino, current.st_mode, current.st_uid, current.st_nlink, current.st_size) ==
                         (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_nlink, before.st_size))
                result[path] = (current.st_dev, current.st_ino, current.st_mode, current.st_uid, current.st_nlink, current.st_size)
                if stat.S_ISDIR(current.st_mode):
                    walk(fd, path, depth + 1)
            finally:
                os.close(fd)
    walk(root, '', 0)
    return result


def _open_relative(root, relative):
    fd = os.dup(root)
    try:
        for name in relative.split('/'):
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            os.close(fd);fd = child
        return fd
    except BaseException:
        os.close(fd);raise


def _publish(source, target, name):
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(source, name.encode('ascii'), target, name.encode('ascii'), 1):
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise BackupError('mass_storage_appliance_batch_exists')
        raise OSError(error, 'Ready batch publication refused')


def handoff_ready_batch(batch, account):
    """Move only this inaccessible root-private completed batch; top owner last."""
    name = batch.root.name
    _require(batch.root.parent == PRIVATE_STAGING and re.fullmatch(r'[0-9a-f]{32}\.ready', name))
    with _root_directory(PRIVATE_STAGING) as source, _state_directory(STAGING, account.pw_uid) as target:
        _directory(source, ROOT_UID);_directory(target, account.pw_uid)
        _require(os.fstat(source).st_dev == os.fstat(target).st_dev)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source)
        try:
            batch_identity = _directory(fd, ROOT_UID)
            inventory = _inventory(fd)
            metadata = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                raw = os.read(metadata, 1024 * 1024 + 1)
                _require(len(raw) <= 1024 * 1024)
            finally:
                os.close(metadata)
            _require(json.loads(raw, object_pairs_hook=_unique) == {'schema_version': 1, 'files': [vars(item) for item in batch.files]})
            files = {'files/' + item.relative: item.size for item in batch.files}
            _require({path for path, entry in inventory.items() if stat.S_ISREG(entry[2])} == set(files) | {'manifest.json'})
            _require('files' in inventory and stat.S_ISDIR(inventory['files'][2]))
            for path, size in files.items():
                _require(inventory[path][5] == size)
            # All descendants remain inaccessible to backup while the top is root700.
            for path in sorted(inventory, key=lambda value: value.count('/'), reverse=True):
                child = _open_relative(fd, path)
                try:
                    info = os.fstat(child)
                    _require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size) == inventory[path])
                    os.fchown(child, account.pw_uid, account.pw_gid);os.fchmod(child, 0o700 if stat.S_ISDIR(info.st_mode) else 0o600);os.fsync(child)
                finally:
                    os.close(child)
            _require(_directory(fd, ROOT_UID) == batch_identity)
            os.fchown(fd, account.pw_uid, account.pw_gid);os.fchmod(fd, 0o700);os.fsync(fd)
            with _state_directory(STAGING, account.pw_uid) as current:
                _require(_directory(current, account.pw_uid) == _directory(target, account.pw_uid))
            named = os.stat(name, dir_fd=source, follow_symlinks=False)
            _require((named.st_dev, named.st_ino) == batch_identity)
            _publish(source, target, name)
            os.fsync(source);os.fsync(target)
            current = os.stat(name, dir_fd=target, follow_symlinks=False)
            _require((current.st_dev, current.st_ino) == batch_identity and current.st_uid == account.pw_uid)
        finally:
            os.close(fd)


def _private_directory(path):
    with _root_directory(path.parent):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    with _root_directory(path) as fd:
        _directory(fd, ROOT_UID)


def _run(settings, account, *, once=False):
    io = _ApplianceIO(settings);io.require_mutations()
    previous_umask = os.umask(0o077)
    try:
        result = io._command(['findmnt', '-J', '-T', str(PRIVATE_STAGING.parent), '-o', 'SOURCE,FSTYPE,OPTIONS'], {'findmnt'})
        rows = json.loads(result.stdout)['filesystems'] if result.returncode == 0 else []
        _require(len(rows) == 1 and rows[0]['source'] == '/dev/mmcblk0p2' and rows[0]['fstype'] == 'ext4'
                 and 'rw' in rows[0]['options'].split(','))
        for path in (STATE, PRIVATE_STAGING, MOUNT):
            _private_directory(path)
        with _state_directory(STAGING, account.pw_uid):
            pass
        backend = _ApplianceBackend(settings, io)
        captured = 0
        with GadgetController(backend, STATE).session() as controller:
            controller.start(adopt_host=True)
            while True:
                result = controller.poll(PRIVATE_STAGING)
                if hasattr(result, 'root'):
                    handoff_ready_batch(result, account);captured += 1
                if once:
                    return {'appliance_state': 'host_owned', 'batches_handed_off': captured,
                            'root_cloud_operations': False, 'automatic_deletion': False}
                time.sleep(1)
    finally:
        os.umask(previous_umask)


def run_mass_storage_appliance(*, once=False):
    if APPROVED_TEST_BUILD is not True:
        return {'appliance_state': 'disabled', 'root_cloud_operations': False, 'automatic_deletion': False}
    try:
        settings = _settings()
        if settings is None:
            return {'appliance_state': 'disabled', 'root_cloud_operations': False, 'automatic_deletion': False}
        _approved(settings)
        return _run(settings, _service_identity('raspi-backup'), once=once)
    except Exception:
        return {'appliance_state': 'failed_review_required', 'root_cloud_operations': False, 'automatic_deletion': False}


if __name__ == '__main__':
    report = run_mass_storage_appliance()
    print(json.dumps(report))
    raise SystemExit(2 if report['appliance_state'] == 'failed_review_required' else 0)
