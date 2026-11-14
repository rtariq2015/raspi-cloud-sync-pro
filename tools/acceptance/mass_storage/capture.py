"""One approved same-process host eject -> read-only capture; never re-export.

Uses protected sibling trial.py for its existing approval/preflight/lock path.
An invocation-local observer adds an armed record; no core gate is changed.
On watch failure no loop/mount/image-content operation is permitted.
"""

import argparse
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

from raspi_cloud_sync_pro.google_broker import _drop_identity, _service_identity

spec = importlib.util.spec_from_file_location('host_only_trial', Path(__file__).with_name('trial.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
MOUNT = Path('/run/raspi-cloud-mass-storage-capture')
STAGING = Path('/var/lib/raspi-cloud-sync-pro/mass-storage-staging')
PYTHON = Path('/opt/raspi-cloud-sync-pro/releases/0.1.0.dev17/venv/bin/python3')
CHILD = r'''
import json,os,stat,sys
from pathlib import Path
from raspi_cloud_sync_pro.staging import stage_snapshot
from raspi_cloud_sync_pro.storage import StorageOwnership
source,destination,output=Path(sys.argv[1]),Path(sys.argv[2]),int(sys.argv[3])
assert os.getuid()!=0 and not os.getgroups() and os.statvfs(source).f_flag & os.ST_RDONLY
assert source.stat().st_uid==os.getuid() and stat.S_IMODE(source.stat().st_mode)==0o700
assert destination.stat().st_uid==os.getuid() and stat.S_IMODE(destination.stat().st_mode)==0o700
os.umask(0o077)
ownership=StorageOwnership()
ownership.confirm_host_access(locally_unmounted=True,loop_detached=True)
ownership.confirm_host_release(clean_eject_observed=True)
ownership.confirm_pi_access(gadget_closed=True,io_drained=True,filesystem_clean=True)
ownership.begin_staging(loop_read_only=True,mount_read_only=True)
batch=stage_snapshot(source,destination,ownership)
value={'schema_version':1,'batch_id':batch.root.stem,'file_count':len(batch.files),'total_bytes':sum(f.size for f in batch.files)}
raw=json.dumps(value,sort_keys=True).encode();os.write(output,len(raw).to_bytes(4,'big')+raw);os.close(output)
'''


def wait_for(check, seconds=5):
    end = time.monotonic() + seconds
    while True:
        if check():
            return
        trial.require(time.monotonic() < end)
        time.sleep(.1)


def clean_header(expected):
    """Read only filesystem metadata, and only AFTER this invocation's eject."""
    fd = os.open(trial.IMAGE, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        trial.require(trial.identity(info)[:3] == expected and stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        header = os.pread(fd, 512, 1024 * 1024)
        trial.require(len(header) == 512 and header[3:11] == b'EXFAT   ' and header[510:] == b'\x55\xaa'
                      and int.from_bytes(header[106:108], 'little') & 6 == 0)
        trial.require(trial.identity(os.fstat(fd))[:3] == expected == trial.identity(trial.IMAGE.lstat())[:3])
    finally:
        os.close(fd)


def watched(backend, timeout, state):
    """The callback runs INSIDE trial.operate's lock and actual approval checks."""
    deadline, armed, announced = time.monotonic() + timeout, False, False
    while time.monotonic() < deadline:
        seen = backend.observe()
        trial.require(seen.bound and seen.profile_ok and seen.power_ok and not seen.loops and not seen.mounts)
        if not seen.configured:
            armed = False
        elif seen.backing == str(trial.IMAGE):
            armed = True
            if not announced:
                trial.save('capture-armed.json', {'schema_version': 1, 'waiting_for_safe_eject': True})
                announced = True
        else:
            trial.require(not seen.backing and armed)
            trial.save('capture-eject-observed.json', {'schema_version': 1, 'same_process_release_observed': True})
            backend.detach_gadget()
            after = backend.observe()
            trial.require(not after.bound and not after.backing and not after.loops and not after.mounts)
            state.append(backend)
            return 'ejected_stopped'
        time.sleep(.2)
    raise RuntimeError('Host ownership retained: no verified eject')


class CaptureIO(trial.TrialIO):
    """After observed eject ONLY: exact RO loop, checker, mount and normal cleanup."""
    def __init__(self, backend_root, account):
        super().__init__(backend_root, approved=True)
        self.loop, self.account = None, account

    def write(self, path, value):
        raise RuntimeError('Capture cannot change the gadget')

    def command(self, args):
        self.require_mutations()
        attach = ['losetup', '--find', '--show', '--read-only', '--partscan', str(trial.IMAGE)]
        if args == attach:
            trial.require(self.loop is None)
            result = trial.command(args).decode().strip()
            trial.require(re.fullmatch(r'/dev/loop[0-9]+', result))
            self.loop = result
            return subprocess.CompletedProcess(args, 0, result, '')
        trial.require(self.loop is not None)
        options = f'ro,nosuid,nodev,noexec,uid={self.account.pw_uid},gid={self.account.pw_gid},fmask=0177,dmask=0077'
        allowed = (['fsck.exfat', '-n', self.loop + 'p1'], ['umount', str(MOUNT)],
                   ['losetup', '--detach', self.loop])
        if args == ['mount', '-t', 'exfat', '-o', 'ro,nosuid,nodev,noexec', self.loop + 'p1', str(MOUNT)]:
            args = ['mount', '-t', 'exfat', '-o', options, self.loop + 'p1', str(MOUNT)]
        else:
            trial.require(args in allowed)
        return subprocess.CompletedProcess(args, 0, trial.command(args).decode(), '')

    def mkdir(self, path):
        raise RuntimeError('Capture cannot change the gadget')

    symlink = mkdir
    provision_command = mkdir


def stage_child(account, timeout=120):
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    process = None
    try:
        # Closed stdio must not turn a private report into a standard stream.
        trial.require(read_fd >= 3 and write_fd >= 3)
        def drop():
            os.umask(0o077);_drop_identity(account.pw_uid, account.pw_gid)
            trial.require(ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) == 0)
        process = subprocess.Popen([str(PYTHON), '-I', '-B', '-c', CHILD, str(MOUNT), str(STAGING), str(write_fd)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=trial.ENV, cwd='/', close_fds=True, pass_fds=(write_fd,), start_new_session=True, preexec_fn=drop)
        os.close(write_fd);write_fd = None
        os.set_blocking(read_fd, False)
        end, raw = time.monotonic() + timeout, bytearray()
        while True:
            trial.require(time.monotonic() < end and len(raw) <= 1028)
            try:
                part = os.read(read_fd, 1029 - len(raw))
            except BlockingIOError:
                time.sleep(.02);continue
            if not part:
                break
            raw.extend(part)
        trial.require(len(raw) >= 4 and 0 < int.from_bytes(raw[:4], 'big') == len(raw) - 4 <= 1024)
        value = trial.parse(raw[4:]);trial.require(process.wait(timeout=max(.001, end - time.monotonic())) == 0)
        trial.require(set(value) == {'schema_version', 'batch_id', 'file_count', 'total_bytes'} and value['schema_version'] == 1
            and re.fullmatch('[0-9a-f]{32}', value['batch_id'])
            and all(type(value[key]) is int and value[key] >= 0 for key in ('file_count', 'total_bytes')))
        return value
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL);process.wait(timeout=3)


def capture_released(backend, account, release_method):
    """Internal continuation; callers must independently prove this invocation's release."""
    loop, mounted, cleaned = None, False, False
    expected = trial.parse(trial.private_file(trial.ROOT / 'plan.json'))['image_identity'][:3]
    trial.require(trial.identity(trial.IMAGE.lstat())[:3] == expected)
    backend.mountpoint = MOUNT
    io = CaptureIO(backend.root, account);backend.io = io
    try:
        trial.protected(MOUNT.parent, directory=True);trial.protected(STAGING.parent, directory=True)
        trial.require(not os.path.lexists(MOUNT) and not os.path.lexists(STAGING))
        MOUNT.mkdir(mode=0o700);STAGING.mkdir(mode=0o700)
        os.chown(STAGING, account.pw_uid, account.pw_gid)
        seen = backend.observe();trial.require(not seen.bound and not seen.backing and not seen.loops and not seen.mounts)
        loop = backend.attach_loop_read_only()
        wait_for(lambda: any(row['name'] == loop + 'p1' for row in backend._block_devices(loop)))
        backend.verify_loop_read_only(loop);backend.check_filesystem(loop);clean_header(expected)
        backend.mount_read_only(loop);mounted = True;backend.verify_mount_read_only(loop)
        trial.require(os.statvfs(MOUNT).f_flag & os.ST_RDONLY and MOUNT.stat().st_uid == account.pw_uid
                      and stat.S_IMODE(MOUNT.stat().st_mode) == 0o700)
        result = stage_child(account)
        backend.verify_mount_read_only(loop);backend.unmount();mounted = False
        backend.detach_loop(loop)
        wait_for(lambda: not backend._loops());loop = None;cleaned = True
        trial.require(trial.identity(trial.IMAGE.lstat())[:3] == expected)
        trial.save('capture-complete.json', {**result, 'local_capture_verified': True, 'cloud_verified': False,
                   'release_method': release_method, 'live_session_eject_observed': release_method == 'live_session_eject'})
        return {'status': 'captured', 'file_count': result['file_count'], 'total_bytes': result['total_bytes'],
                'original_retained': True, 'cloud_verified': False, 'gadget_reattached': False, 'release_method': release_method}
    finally:
        # Never force/lazy unmount, never detach a still-mounted loop, never re-export.
        if not cleaned:
            if mounted:
                backend.verify_mount_read_only(loop);backend.unmount();mounted = False
            if loop is not None:
                backend.detach_loop(loop);wait_for(lambda: not backend._loops())


def capture(peer, timeout=600):
    trial.require(type(timeout) is int and 1 <= timeout <= 600)
    trial.protected(Path(__file__).resolve());trial.protected(PYTHON)
    account = _service_identity('raspi-backup')
    released, original = [], trial.observe_eject
    try:
        trial.observe_eject = lambda backend, limit: watched(backend, limit, released)
        trial.require(trial.operate('watch-eject', peer, timeout) == 'ejected_stopped' and len(released) == 1)
    finally:
        trial.observe_eject = original
    return capture_released(released[0], account, 'live_session_eject')


def confirmed_handoff(peer):
    """Explicit human-confirmed route; NEVER claims an uninterrupted watch proof."""
    observed = trial.preflight(peer, observing=True)
    trial.protected(Path(__file__).resolve());trial.protected(PYTHON)
    account = _service_identity('raspi-backup')
    fd = os.open(trial.ROOT / 'operator.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        trial.require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = trial.private_file(trial.ROOT / 'plan.json');plan = trial.parse(raw);digest = hashlib.sha256(raw).hexdigest()
        expected = {'schema_version': 1, 'plan_sha256': digest, 'user_safe_eject_confirmed': True,
                    'previous_loaded_observation_confirmed': True, 'no_admin_forced_eject': True}
        trial.require(trial.canonical(trial.parse(trial.private_file(trial.ROOT / 'MANUAL_CAPTURE_APPROVED.json'))) == trial.canonical(expected))
        trial.require(trial.canonical(trial.parse(trial.private_file(trial.ROOT / 'ATTACH_APPROVED.json'))) ==
                      trial.canonical({'schema_version': 1, 'plan_sha256': digest, 'approve_host_export': True}))
        trial.require(trial.parse(trial.private_file(trial.ROOT / 'attach-intent.json')) == {'schema_version': 1, 'plan_sha256': digest})
        trial.require(not observed['legacy_modules'] and trial.identity(trial.IMAGE.lstat())[:3] == plan['image_identity'][:3])
        current, prior = [dict(line.split('=', 1) for line in value['ics']) for value in (observed, plan['observed'])]
        trial.require(all(len(value['ics']) == 2 for value in (observed, plan['observed']))
            and set(current) == set(prior) == {'ActiveState', 'UnitFileState'}
            and current['ActiveState'] == 'inactive' and current['UnitFileState'] == prior['UnitFileState'])
        trial.require({k: v for k, v in observed.items() if k not in ('ics', 'legacy_modules')} ==
                      {k: v for k, v in plan['observed'].items() if k not in ('ics', 'legacy_modules')})
        settings = trial.gadget.GadgetSettings(trial.IMAGE, trial.ROOT / 'unused-mount', trial.ROOT / 'unused-state',
            trial.ROOT / 'unused-staging', observed['udc'], name=trial.NAME, serial='RCSP-DISPOSABLE-ONLY')
        backend = trial.gadget.LinuxGadgetBackend(settings)
        backend.io = trial.TrialIO(backend.root, approved=True)
        state = backend.observe()
        trial.require(state.bound and state.configured and state.profile_ok and state.power_ok
                      and not state.backing and not state.loops and not state.mounts)
        trial.save('manual-release.json', {**expected, 'release_method': 'operator_confirmed_host_safe_eject',
                                         'live_session_eject_observed': False})
        backend.detach_gadget();after = backend.observe()
        trial.require(not after.bound and not after.backing and not after.loops and not after.mounts)
        return capture_released(backend, account, 'operator_confirmed_host_safe_eject')
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-peer', required=True)
    parser.add_argument('--timeout', type=int, choices=range(1, 601), default=600, metavar='1..600')
    parser.add_argument('--operator-confirmed-handoff', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = confirmed_handoff(args.ssh_peer) if args.operator_confirmed_handoff else capture(args.ssh_peer, args.timeout)
        print(json.dumps(result));return 0
    except Exception:
        print('{"status":"capture_refused_or_incomplete","cloud_verified":false,"gadget_reattached":false}');return 2


if __name__ == '__main__':
    sys.exit(main())
