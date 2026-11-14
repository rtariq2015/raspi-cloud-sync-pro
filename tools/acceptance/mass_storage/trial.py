"""Manual disposable MSC export; no mounting, capture, driver switch or cloud.

Root supplies a fresh, preformatted 1 GiB MBR/exFAT image at IMAGE. Run ``plan``
before a separately reviewed g_ether switch; explicitly approve that saved plan
before ``attach``. An attached image remains host-owned even if this process
exits or reports failure. Never clear its LUN or unbind it to recover a failure.
"""

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from raspi_cloud_sync_pro import linux_gadget as gadget

ROOT = Path('/var/lib/raspi-cloud-sync-pro/mass-storage-trial')
IMAGE = ROOT / 'disk.img'
NAME = 'raspi-cloud-disposable-msc'
SIZE = 1024 ** 3
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': '/nonexistent'}


def require(value):
    if not value:
        raise RuntimeError('Disposable mass-storage trial refused')


def identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def protected(path, *, directory=False):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode))
        require(stat.S_ISDIR(info.st_mode) if parent != path or directory else stat.S_ISREG(info.st_mode))
    if not directory:
        require(path.lstat().st_nlink == 1)


def private_file(path, maximum=65536):
    protected(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and before.st_size <= maximum)
        raw = os.read(fd, maximum + 1)
        require(len(raw) == before.st_size and identity(os.fstat(fd)) == identity(before) == identity(path.lstat()))
        return raw
    finally:
        os.close(fd)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def save(name, value):
    raw = canonical(value)
    fd = os.open(ROOT / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(args):
    executable = Path(shutil.which(args[0], path=ENV['PATH']) or '/missing').resolve(strict=True)
    protected(executable)
    result = subprocess.run([str(executable)] + args[1:], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=10, env=ENV, cwd='/', check=False)
    require(result.returncode == 0 and len(result.stdout) <= 65536 and len(result.stderr) <= 65536)
    return result.stdout


def image_layout(path):
    """Only before export: verify a whole image, MBR partition 1, and exFAT VBR."""
    protected(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == SIZE)
        mbr = os.pread(fd, 512, 0)
        entry = mbr[446:462]
        start, count = int.from_bytes(entry[8:12], 'little'), int.from_bytes(entry[12:16], 'little')
        require(len(mbr) == 512 and mbr[510:] == b'\x55\xaa' and entry[4] == 7
                and start == 2048 and count > 0 and start + count <= SIZE // 512 and not any(mbr[462:510]))
        boot = os.pread(fd, 512, start * 512)
        require(len(boot) == 512 and boot[3:11] == b'EXFAT   ' and boot[510:] == b'\x55\xaa'
                and boot[108] == 9 and int.from_bytes(boot[64:72], 'little') in (0, start)
                and int.from_bytes(boot[72:80], 'little') == count)
        require(identity(info) == identity(os.fstat(fd)) == identity(path.lstat()))
        return identity(info)
    finally:
        os.close(fd)


def preflight(peer, observing=False):
    require(os.getresuid() == (0, 0, 0) and os.getresgid() == (0, 0, 0))
    require(platform.system() == 'Linux' and platform.machine() == 'aarch64')
    require(Path('/proc/device-tree/model').read_bytes().rstrip(b'\0') == b'Raspberry Pi Zero 2 W Rev 1.0')
    require(os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt')
            and os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'))
    protected(Path(__file__).resolve());protected(Path(gadget.__file__).resolve())
    protected(ROOT, directory=True)
    require(stat.S_IMODE(ROOT.lstat().st_mode) == 0o700)
    require(str(ipaddress.ip_address(peer)) == peer)
    # Require an actual SSH ancestry and a wlan0 route to the operator-supplied peer.
    current = os.getpid()
    for _ in range(20):
        base = Path('/proc') / str(current)
        if (base / 'comm').read_text().startswith('sshd'):
            break
        current = int((base / 'stat').read_bytes().rsplit(b') ', 1)[1].split()[1]);require(current > 1)
    else:
        require(False)
    route = parse(command(['ip', '-j', 'route', 'get', peer]))
    require(len(route) == 1 and route[0]['dev'] == 'wlan0')
    defaults = parse(command(['ip', '-j', '-4', 'route', 'show', 'default']))
    require(defaults and min(defaults, key=lambda row: row.get('metric', 0))['dev'] == 'wlan0')
    require(command(['nmcli', '-g', 'GENERAL.STATE', 'device', 'show', 'wlan0']).startswith(b'100'))
    require(command(['vcgencmd', 'get_throttled']).strip() == b'throttled=0x0')
    loops = parse(command(['losetup', '--list', '--json', '--output', 'NAME,BACK-FILE,RO']))
    require(not any(row.get('back-file') == str(IMAGE) for row in loops['loopdevices']))
    disks = []
    for target in ('/', str(ROOT)):
        rows = parse(command(['findmnt', '-J', '-T', target, '-o', 'SOURCE,FSTYPE,OPTIONS']))['filesystems']
        require(len(rows) == 1 and rows[0]['source'] == '/dev/mmcblk0p2' and rows[0]['fstype'] == 'ext4'
                and 'rw' in rows[0]['options'].split(','));disks.append(rows[0]['source'])
    udcs = list(Path('/sys/class/udc').iterdir());require(len(udcs) == 1)
    require(Path('/sys/kernel/config/usb_gadget').is_dir())
    for root in Path('/sys/kernel/config/usb_gadget').iterdir():
        require(not (root / 'UDC').read_text().strip() or observing and root.name == NAME)
    modules = [name for name in gadget.LEGACY_GADGET_MODULES if Path('/sys/module', name).exists()]
    require(set(modules) <= {'g_ether'})
    return {'ssh_peer': peer, 'udc': udcs[0].name, 'root_disks': disks,
            'model': 'Raspberry Pi Zero 2 W Rev 1.0',
            'wifi_ipv4': command(['nmcli', '-g', 'IP4.ADDRESS', 'device', 'show', 'wlan0']).decode().strip(),
            'default4': [{k: v for k, v in row.items() if k not in ('expires', 'cacheinfo')} for row in defaults],
            'ics': command(['systemctl', 'show', '--all', '--property=ActiveState,UnitFileState',
                            'rpi-usb-gadget-ics.service']).decode().splitlines(),
            'wifi_profile': command(['nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', 'wlan0']).decode().strip(),
            'legacy_modules': modules}


class TrialIO(gadget.LiveLinuxIO):
    """Only the exact fresh configfs profile may change; every shell command is read-only."""
    def __init__(self, backend_root, approved=False):
        self.root, self.approved = backend_root, approved

    def require_mutations(self):
        require(self.approved and os.geteuid() == 0)
        require(os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'))

    def _command(self, arguments, allowed):
        require(arguments[0] in {'vcgencmd', 'losetup', 'lsblk'} and arguments[0] in allowed)
        return subprocess.CompletedProcess(arguments, 0, command(arguments).decode(), '')

    def command(self, arguments):
        raise RuntimeError('No image, mount or provisioning commands are permitted')

    provision_command = command

    def write(self, path, value):
        self.require_mutations();require(self.root in Path(path).parents)
        super().write(path, value)

    def mkdir(self, path):
        self.require_mutations();require(Path(path) == self.root or self.root in Path(path).parents)
        super().mkdir(path)

    def symlink(self, source, destination):
        self.require_mutations();require(self.root in Path(source).parents and self.root in Path(destination).parents)
        super().symlink(source, destination)


def observe_eject(backend, timeout):
    require(type(timeout) is int and 1 <= timeout <= 600)
    deadline, armed = time.monotonic() + timeout, False
    while time.monotonic() < deadline:
        state = backend.observe()
        require(state.bound and state.profile_ok and state.power_ok and not state.loops and not state.mounts)
        if not state.configured:
            armed = False
        elif state.backing == str(IMAGE):
            armed = True
        else:
            require(not state.backing and armed)
            save('eject-observed.json', {'schema_version': 1, 'loaded_configured_then_empty_in_one_session': True})
            backend.detach_gadget()
            after = backend.observe()
            require(not after.bound and not after.backing and not after.loops and not after.mounts)
            save('ejected-stopped.json', {'schema_version': 1, 'gadget_unbound': True, 'local_capture_enabled': False})
            return 'ejected_stopped'
        time.sleep(.2)
    raise RuntimeError('No verified eject was observed; host ownership retained')


def operate(action, peer, timeout=180):
    observed = preflight(peer, observing=action == 'watch-eject')
    fd = os.open(ROOT / 'operator.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'plan':
            require(not (ROOT / 'attach-intent.json').exists())
            plan = {'schema_version': 1, 'image_identity': image_layout(IMAGE), 'observed': observed}
            save('rollback.json', {'steps': ['Never detach a loaded LUN or unload a driver during host I/O.',
                'Retain this image and all trial records on every failure.',
                'After separately verified host eject and empty LUN only: unbind this exact trial UDC.',
                'Review removal of only this trial configfs tree, then separately restore saved g_ether configuration.'],
                'g_ether_parameters': {p.name: p.read_text().strip() for p in Path('/sys/module/g_ether/parameters').glob('*')}})
            save('plan.json', plan)
            return 'planned'
        require(action in ('attach', 'watch-eject') and not observed['legacy_modules'])
        raw = private_file(ROOT / 'plan.json');plan = parse(raw)
        require(set(plan) == {'schema_version', 'image_identity', 'observed'}
                and type(plan['schema_version']) is int and plan['schema_version'] == 1)
        approval = parse(private_file(ROOT / 'ATTACH_APPROVED.json'))
        require(approval == {'schema_version': 1, 'approve_host_export': True, 'plan_sha256': hashlib.sha256(raw).hexdigest()}
                and type(approval['schema_version']) is int and approval['approve_host_export'] is True)
        require(private_file(ROOT / 'rollback.json'))
        actual = identity(IMAGE.lstat())
        require(actual == plan['image_identity'] if action == 'attach' else actual[:3] == plan['image_identity'][:3])
        current_ics, prior_ics = [dict(line.split('=', 1) for line in value['ics']) for value in (observed, plan['observed'])]
        require(all(len(value['ics']) == 2 for value in (observed, plan['observed']))
                and set(current_ics) == set(prior_ics) == {'ActiveState', 'UnitFileState'}
                and current_ics['ActiveState'] == 'inactive' and current_ics['UnitFileState'] == prior_ics['UnitFileState'])
        require({k: v for k, v in observed.items() if k not in ('legacy_modules', 'ics')} ==
                {k: v for k, v in plan['observed'].items() if k not in ('legacy_modules', 'ics')})
        settings = gadget.GadgetSettings(IMAGE, ROOT / 'unused-mount', ROOT / 'unused-state', ROOT / 'unused-staging',
                                        observed['udc'], name=NAME, serial='RCSP-DISPOSABLE-ONLY')
        backend = gadget.LinuxGadgetBackend(settings)
        io = TrialIO(backend.root, approved=True);backend.io = io
        if action == 'watch-eject':
            require(private_file(ROOT / 'attach-intent.json') and backend.root.is_dir())
            return observe_eject(backend, timeout)
        require(not backend.root.exists())
        save('attach-intent.json', {'schema_version': 1, 'plan_sha256': hashlib.sha256(raw).hexdigest()})
        backend.prepare();backend.attach_gadget()
        state = backend.observe()
        require(state.bound and state.backing == str(IMAGE) and not state.loops and not state.mounts and state.profile_ok)
        save('host-owned.json', {'schema_version': 1, 'host_owned': True, 'local_reads_permitted': False})
        return 'host_owned'
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'attach', 'watch-eject'))
    parser.add_argument('--ssh-peer', required=True)
    parser.add_argument('--timeout', type=int, choices=range(1, 601), default=180, metavar='1..600')
    args = parser.parse_args(argv)
    try:
        result = operate(args.action, args.ssh_peer, args.timeout)
        print(json.dumps({'status': result, 'cloud_enabled': False, 'local_capture_enabled': False}))
        return 0
    except Exception:
        print('{"status":"refused_or_incomplete","cloud_enabled":false,"local_capture_enabled":false}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
