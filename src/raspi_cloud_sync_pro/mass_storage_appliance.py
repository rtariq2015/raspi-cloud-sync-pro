"""Approved-test-build MSC capture loop; no provider or network operations.

The public build is disabled. A separately reviewed protected test build may
enable this module alone; linux_gadget.HARDWARE_OPERATIONS_ENABLED stays False.
Only a completed root-private snapshot changes ownership. Originals and failed
handoffs remain on disk; the independent backup worker owns cloud processing.
"""

import ctypes
import argparse
import errno
from dataclasses import replace
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
from .hardware import power_diagnostics
from .google_broker import _root_directory, _root_file, _service_identity, _state_directory
from .linux_gadget import LEGACY_GADGET_MODULES, GadgetSettings, LinuxGadgetBackend, LiveLinuxIO
from .mass_storage_image import PARTITION_OFFSET, validate_image_approval, validate_image_headers
from .maintenance import MaintenanceHold
from .capture_space import CAPACITY_POLICY, LEGACY_POLICY, CaptureSpacePolicy, approved_capture_policy

APPROVED_TEST_BUILD = False
MAINTENANCE_HOLD_ENABLED = False  # Separate reviewed build/admin approval; no config toggle.
IMAGE_ENROLLMENT_ENABLED = False  # Independent protected-build approval; never a config toggle.
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
    required = {'schema_version', 'enabled', 'udc', 'serial', 'approval_file'}
    _require(type(value) is dict and type(value.get('schema_version')) is int
             and (value['schema_version'] == 1 and set(value) == required
                  or value['schema_version'] == 2 and set(value) == required | {'capture_space_policy'}
                     and value['capture_space_policy'] == CAPACITY_POLICY)
             and type(value['enabled']) is bool)
    if not value['enabled']:
        return None
    _require(value['serial'] == SERIAL and value['approval_file'] == str(APPROVAL))
    settings = GadgetSettings(IMAGE, MOUNT, STATE, PRIVATE_STAGING, value['udc'], name=NAME, serial=SERIAL,
                             capture_space_policy=value.get('capture_space_policy', LEGACY_POLICY))
    return settings


def _approved(settings):
    _require(APPROVED_TEST_BUILD is True and os.getresuid() == (0, 0, 0) and os.getresgid() == (0, 0, 0))
    _require(platform.system() == 'Linux' and platform.machine() == 'aarch64'
             and Path('/proc/device-tree/model').read_bytes().rstrip(b'\0') == b'Raspberry Pi Zero 2 W Rev 1.0')
    _require(os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'))
    _require(_settings() == settings)
    approval = _json(APPROVAL)
    size = validate_image_approval(approval, LiveLinuxIO().regular_image(IMAGE))
    approved_capture_policy(size, settings.capture_space_policy)


class _ApplianceIO(LiveLinuxIO):
    def __init__(self, settings):
        self.settings = settings
        self.root = Path('/sys/kernel/config/usb_gadget') / NAME
        self.preparing = False

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
        if self.preparing:
            allowed = {path: {value} for path, value in self.profile_attributes().items()}
        _require(Path(path) in allowed and value in allowed[Path(path)])
        if not self.preparing and value and Path(path) in {
                self.root/'UDC', self.root/'functions/mass_storage.0/lun.0/file'}:
            from .image_enrollment import guard_enrollment
            guard_enrollment(STATE, IMAGE, self.regular_image(IMAGE), export=True)
        super().write(path, value)

    def profile_attributes(self):
        return {self.root/'idVendor': self.settings.vendor_id, self.root/'idProduct': self.settings.product_id,
                self.root/'bcdUSB': '0x0200', self.root/'bcdDevice': '0x0100',
                self.root/'strings/0x409/serialnumber': self.settings.serial,
                self.root/'strings/0x409/manufacturer': 'Raspi Cloud Sync Pro',
                self.root/'strings/0x409/product': 'Raspi Cloud Sync Pro - disposable test',
                self.root/'configs/c.1/MaxPower': '500',
                self.root/'functions/mass_storage.0/lun.0/removable': '1',
                self.root/'functions/mass_storage.0/lun.0/ro': '0', self.root/'functions/mass_storage.0/lun.0/nofua': '0'}

    def mkdir(self, path):
        _require(self.preparing and Path(path) in {self.root, self.root/'strings/0x409',
                 self.root/'configs/c.1', self.root/'functions/mass_storage.0'})
        super().mkdir(path)

    def symlink(self, source, destination):
        _require(self.preparing and Path(source) == self.root/'functions/mass_storage.0'
                 and Path(destination) == self.root/'configs/c.1/mass_storage.0')
        super().symlink(source, destination)

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
            mbr, boot = os.pread(fd, 512, 0), os.pread(fd, 512, PARTITION_OFFSET)
            validate_image_headers(mbr, boot, before.st_size)
            after = os.fstat(fd)
            _require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) ==
                     (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns))
            named = IMAGE.lstat()
            _require((named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns, named.st_ctime_ns) ==
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


def _start_controller(controller, backend, io):
    """Called inside the exclusive session; no OS provisioning or driver loads."""
    backend._pin_image()
    controller.validate_maintenance_start()  # Before adoption, configfs preparation or cold export.
    present = io.exists(backend.root)
    if present and (io.read(backend.root/'UDC') or io.read(backend.lun/'file')):
        controller.start(adopt_host=True)
        return
    controller.validate_cold_start(PRIVATE_STAGING)
    _require(not any(io.exists('/sys/module/' + name) for name in LEGACY_GADGET_MODULES))
    _require([path.name for path in io.children('/sys/class/udc')] == [backend.settings.udc])
    _require(io.children('/sys/kernel/config/usb_gadget') == ([backend.root] if present else []))
    _require(not backend._loops() and not backend._mounts(()))
    _require(power_diagnostics(io)['current_ok'] is True)
    if not present:
        io.preparing = True
        try:
            backend.prepare()
        finally:
            io.preparing = False
    # An exact complete empty profile is reusable; partial profiles are never wiped.
    _require(all(io.read(path) == value for path, value in io.profile_attributes().items()))
    _require(io.children(backend.root/'strings') == [backend.root/'strings/0x409'])
    seen = backend.observe()
    _require(seen.profile_ok and not seen.bound and not seen.backing and not seen.loops and not seen.mounts)
    controller.start(cold_start=True, staging_directory=PRIVATE_STAGING)


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
        policy = CaptureSpacePolicy() if settings.capture_space_policy == CAPACITY_POLICY else None
        with GadgetController(backend, STATE, maintenance_enabled=MAINTENANCE_HOLD_ENABLED, space_policy=policy).session() as controller:
            _start_controller(controller, backend, io)
            while True:
                result = controller.poll(PRIVATE_STAGING)
                if hasattr(result, 'root'):
                    handoff_ready_batch(result, account);captured += 1
                if MAINTENANCE_HOLD_ENABLED and controller.complete_maintenance(PRIVATE_STAGING):
                    return {'appliance_state':'maintenance_active', 'batches_handed_off':captured,
                            'root_cloud_operations':False, 'automatic_deletion':False}
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
    except Exception as error:
        result = {'appliance_state': 'failed_review_required', 'root_cloud_operations': False, 'automatic_deletion': False}
        messages = {'capture_space_low':'Not enough available space for safe capture; files were retained. Recovery review is required.',
                    'capture_space_unknown':'Available storage could not be verified; capture stopped for review.',
                    'invalid_capture_space_policy':'The capture-space policy needs approval review.'}
        if isinstance(error, BackupError) and error.code in messages:
            result.update(error_code=error.code, message=messages[error.code])
        return result


def run_mass_storage_maintenance(action, *, approved=False, request_id=None):
    """Root/Pi/approval-gated one-shot action; never switches/stops services."""
    if APPROVED_TEST_BUILD is not True or MAINTENANCE_HOLD_ENABLED is not True:
        return {'maintenance_state':'disabled'}
    try:
        _require(approved is True and action in ('request', 'release'))
        settings = _settings();_require(settings is not None);_approved(settings)
        for path in (STATE, PRIVATE_STAGING):
            with _root_directory(path):
                pass
        io = _ApplianceIO(settings);io.require_mutations()
        backend = _ApplianceBackend(settings, io);backend._pin_image()
        if action == 'request':
            _require(request_id is None)
            identifier = MaintenanceHold(STATE, backend, enabled=True).submit()
            return {'maintenance_state':'submitted_waiting_for_safe_eject', 'request_id':identifier}
        with GadgetController(backend, STATE, maintenance_enabled=True).session() as controller:
            result = controller.release_maintenance(request_id, PRIVATE_STAGING, approved=True)
        return {'maintenance_state':result, 'controller_restart_requires_separate_action':True}
    except Exception:
        return {'maintenance_state':'failed_review_required'}


def _enrollment_platform_approved():
    """Recovery gate deliberately does not require an extant canonical image."""
    _require(APPROVED_TEST_BUILD is True and MAINTENANCE_HOLD_ENABLED is True
             and IMAGE_ENROLLMENT_ENABLED is True)
    _require(os.getresuid() == (0, 0, 0) and os.getresgid() == (0, 0, 0))
    _require(platform.system() == 'Linux' and platform.machine() == 'aarch64'
             and Path('/proc/device-tree/model').read_bytes().rstrip(b'\0') == b'Raspberry Pi Zero 2 W Rev 1.0')
    _require(os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'))


class _EnrollmentIO(_ApplianceIO):
    """Image verification only: no export, writable mount, repair or provisioning."""

    def require_mutations(self):
        _enrollment_platform_approved()
        current = _settings()
        _require(current is not None
                 and replace(self.settings, image=IMAGE, capture_space_policy=LEGACY_POLICY)
                     == replace(current, capture_space_policy=LEGACY_POLICY))

    def command(self, arguments):
        self.require_mutations()
        loop = r'/dev/loop[0-9]+'
        partition = loop + 'p1'
        allowed = (
            arguments == ['losetup', '--find', '--show', '--read-only', '--partscan', str(self.settings.image)]
            or len(arguments) == 3 and arguments[:2] == ['losetup', '--detach']
                and re.fullmatch(loop, arguments[2]) is not None
            or len(arguments) == 3 and arguments[:2] == ['fsck.exfat', '-n']
                and re.fullmatch(partition, arguments[2]) is not None
            or len(arguments) == 7 and arguments[:5] == ['mount', '-t', 'exfat', '-o', 'ro,nosuid,nodev,noexec']
                and re.fullmatch(partition, arguments[5]) is not None
                and arguments[6] == str(self.settings.mountpoint)
            or arguments == ['umount', str(self.settings.mountpoint)]
        )
        _require(allowed)
        return self._command(arguments, {'losetup', 'fsck.exfat', 'mount', 'umount'})

    def write(self, path, value):
        raise BackupError('image_enrollment_verifier_export_forbidden')

    def mkdir(self, path):
        raise BackupError('image_enrollment_provisioning_forbidden')

    def symlink(self, source, destination):
        raise BackupError('image_enrollment_provisioning_forbidden')

    def provision_command(self, arguments):
        raise BackupError('image_enrollment_provisioning_forbidden')


def run_mass_storage_enrollment(action, *, approved=False, candidate=None, candidate_approval=None,
                                hold_id=None, transaction_id=None, recovery_action=None):
    """Explicit, disabled-by-default image enrollment; never changes services."""
    if (APPROVED_TEST_BUILD is not True or MAINTENANCE_HOLD_ENABLED is not True
            or IMAGE_ENROLLMENT_ENABLED is not True):
        return {'enrollment_state':'disabled'}
    try:
        _require(approved is True and action in ('prepare', 'commit', 'recover', 'activate'))
        if action == 'prepare':
            _require(candidate is not None and candidate_approval is not None
                     and isinstance(hold_id, str) and re.fullmatch('[0-9a-f]{32}', hold_id)
                     and transaction_id is None and recovery_action is None)
        else:
            _require(candidate is None and candidate_approval is None and hold_id is None
                     and isinstance(transaction_id, str) and re.fullmatch('[0-9a-f]{32}', transaction_id)
                     and (recovery_action in ('commit', 'rollback', 'host') if action == 'recover'
                          else recovery_action is None))
        _enrollment_platform_approved()
        settings = _settings();_require(settings is not None)
        for path in (STATE, PRIVATE_STAGING):
            with _root_directory(path):
                pass
        # Import only after the existing protected-build/platform/explicit-action gates.
        from .image_enrollment import ImageEnrollment

        def factory(path):
            selected = replace(settings, image=Path(path))
            return LinuxGadgetBackend(selected, _EnrollmentIO(selected))

        if action == 'prepare':
            _approved(settings)
            approval = _json(Path(candidate_approval))
            transaction = ImageEnrollment(STATE, IMAGE, Path(candidate), CONFIG, APPROVAL, PRIVATE_STAGING, factory)
            identifier = transaction.prepare(hold_id, approval, approved=True)
            return {'enrollment_state':'prepared_detached', 'transaction_id':identifier}
        # No active-image approval here: an interrupted rename may leave IMAGE absent.
        transaction = ImageEnrollment.open(STATE, IMAGE, CONFIG, APPROVAL, PRIVATE_STAGING, factory)
        if action == 'commit':
            transaction.commit(transaction_id, approved=True)
            state = 'committed_detached'
        elif action == 'recover':
            transaction.recover(transaction_id, action=recovery_action, approved=True)
            state = {'commit':'committed_detached', 'rollback':'rolled_back_detached', 'host':'exported_host_owned'}[recovery_action]
        else:
            _approved(settings)
            backend = _ApplianceBackend(settings, _ApplianceIO(settings));backend._pin_image()
            policy = CaptureSpacePolicy() if settings.capture_space_policy == CAPACITY_POLICY else None
            with GadgetController(backend, STATE, maintenance_enabled=True, space_policy=policy).session() as controller:
                transaction.activate(controller, transaction_id, approved=True)
            state = 'exported_host_owned'
        return {'enrollment_state':state, 'controller_restart_requires_separate_action':True}
    except Exception:
        return {'enrollment_state':'failed_review_required'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--request-maintenance-hold', action='store_true')
    action.add_argument('--release-maintenance-hold', metavar='EXACT_HOLD_ID')
    action.add_argument('--prepare-image-enrollment', metavar='CANDIDATE')
    action.add_argument('--commit-image-enrollment', metavar='EXACT_TRANSACTION_ID')
    action.add_argument('--recover-image-enrollment', metavar='EXACT_TRANSACTION_ID')
    action.add_argument('--activate-image-enrollment', metavar='EXACT_TRANSACTION_ID')
    parser.add_argument('--approve-maintenance-action', action='store_true')
    parser.add_argument('--candidate-image-approval', metavar='APPROVAL_FILE')
    parser.add_argument('--maintenance-hold-id', metavar='EXACT_HOLD_ID')
    parser.add_argument('--enrollment-recovery-action', choices=('commit', 'rollback', 'host'))
    parser.add_argument('--approve-image-enrollment', action='store_true')
    args = parser.parse_args(argv)
    enrollment = [(name, value) for name, value in (
        ('prepare', args.prepare_image_enrollment), ('commit', args.commit_image_enrollment),
        ('recover', args.recover_image_enrollment), ('activate', args.activate_image_enrollment)) if value is not None]
    enrollment_options = (args.approve_image_enrollment or args.candidate_image_approval is not None
                          or args.maintenance_hold_id is not None or args.enrollment_recovery_action is not None)
    if enrollment:
        if args.approve_maintenance_action:
            parser.error('maintenance approval cannot approve image enrollment')
        selected, value = enrollment[0]
        report = run_mass_storage_enrollment(selected, approved=args.approve_image_enrollment,
            candidate=value if selected == 'prepare' else None, candidate_approval=args.candidate_image_approval,
            hold_id=args.maintenance_hold_id, transaction_id=None if selected == 'prepare' else value,
            recovery_action=args.enrollment_recovery_action)
    elif enrollment_options:
        parser.error('an explicit image-enrollment action is required')
    elif args.request_maintenance_hold or args.release_maintenance_hold is not None:
        report = run_mass_storage_maintenance('request' if args.request_maintenance_hold else 'release',
                    approved=args.approve_maintenance_action, request_id=args.release_maintenance_hold)
    else:
        if args.approve_maintenance_action:
            parser.error('an explicit maintenance action is required')
        report = run_mass_storage_appliance()
    print(json.dumps(report))
    return 2 if 'failed_review_required' in report.values() else 0


if __name__ == '__main__':
    raise SystemExit(main())
