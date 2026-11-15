"""Read-only kernel proof for the one supported unrelated loop: active zram backing.

No loop number, filename, process name or userspace label grants an exception.
The ioctl is LOOP_GET_STATUS64 only. No device contents or swap configuration
are read/written. Changing/missing evidence always refuses enrollment.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import struct

from .errors import BackupError

_QUERY = ['losetup','--list','--json','--output','NAME,BACK-FILE,RO']
_STATUS = struct.Struct('=5Q4I64s64s32s2Q')  # Linux UAPI struct loop_info64.


def _require(value):
    if not value:
        raise BackupError('image_enrollment_loop_unverified')


class KernelLoopProbe:
    """Small injected read-only boundary; synthetic tests never open real devices."""
    def read(self, path):
        return Path(path).read_text().strip()

    def children(self, path):
        return sorted(Path(path).iterdir())

    @contextmanager
    def _block(self, path):
        path = Path(path)
        _require(path.parent == Path('/dev') and re.fullmatch(r'(?:loop|zram)[0-9]+',path.name))
        fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            s = os.fstat(fd)
            signature = lambda v:(v.st_dev,v.st_ino,v.st_rdev,v.st_mode,v.st_uid,v.st_gid)
            _require(stat.S_ISBLK(s.st_mode) and s.st_uid == 0 and signature(s) == signature(path.lstat()))
            yield fd,s
            _require(signature(s) == signature(os.fstat(fd)) == signature(path.lstat()))
        finally:
            os.close(fd)

    def block_identity(self, path):
        with self._block(path) as (_,s):
            return os.major(s.st_rdev),os.minor(s.st_rdev)

    def virtual_binding(self, name, identity):
        _require(re.fullmatch(r'(?:loop|zram)[0-9]+',name))
        number = '%d:%d' % tuple(identity)
        expected = Path('/sys/devices/virtual/block')/name
        _require(Path('/sys/dev/block',number).resolve(strict=True) == expected
                 and Path('/sys/class/block',name).resolve(strict=True) == expected
                 and self.read(Path('/sys/class/block',name,'dev')) == number)

    def configured_loops(self):
        result = []
        for entry in self.children('/sys/class/block'):
            if not re.fullmatch(r'loop[0-9]+',entry.name):
                continue
            # Unconfigured loops lack the kernel's loop attribute directory.
            # Enumeration/read errors are never interpreted as absence.
            if any(child.name == 'loop' for child in self.children(entry)):
                path = '/dev/'+entry.name
                self.virtual_binding(entry.name,self.block_identity(path))
                _require(self.read(entry/'loop/backing_file'))
                result.append(path)
        return sorted(result)

    def loop_status(self, path):
        with self._block(path) as (fd,s):
            _require(os.major(s.st_rdev) == 7)
            data = bytearray(_STATUS.size)
            fcntl.ioctl(fd,0x4C05,data,True)  # LOOP_GET_STATUS64: never a SET/CLEAR ioctl.
            value = _STATUS.unpack(data)
            _require(value[5] == os.minor(s.st_rdev) and value[6] == 0 and value[7] == 0)
            return {'device':value[0],'inode':value[1],'number':value[5],
                    'offset':value[3],'sizelimit':value[4],'flags':value[8]}

    def backing_identity(self, path):
        path = Path(path)
        _require(path.is_absolute() and path.resolve(strict=True) == path)
        for parent in path.parents:
            s = parent.lstat()
            _require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022)
        fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            identity = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode),s.st_nlink)
            s = os.fstat(fd)
            _require(stat.S_ISREG(s.st_mode) and identity(s) == identity(path.lstat()))
            result = identity(s)
            _require(result == identity(os.fstat(fd)) == identity(path.lstat()))
            return result
        finally:
            os.close(fd)


def _active_zram(probe):
    lines = probe.read('/proc/swaps').splitlines()
    _require(lines and lines[0].split() == ['Filename','Type','Size','Used','Priority'])
    result = {}
    for line in lines[1:]:
        parts = line.split();_require(len(parts) == 5)
        path = parts[0]
        if not re.fullmatch(r'/dev/zram[0-9]+',path):
            continue
        _require(path not in result and parts[1] == 'partition'
                 and int(parts[2]) > 0 and 0 <= int(parts[3]) <= int(parts[2]))
        name = Path(path).name;device = tuple(probe.block_identity(path))
        probe.virtual_binding(name,device)
        backing = probe.read('/sys/class/block/'+name+'/backing_dev').strip()
        if backing == 'none':
            continue
        _require(re.fullmatch(r'/dev/loop[0-9]+',backing) is not None)
        size = int(probe.read('/sys/class/block/'+name+'/disksize'))
        _require(size > 0 and int(parts[2])*1024 <= size
                 and not probe.children('/sys/class/block/'+name+'/holders'))
        result[path] = (device,backing,size)
    return result


def _mounted(probe):
    result = set()
    for line in probe.read('/proc/self/mountinfo').splitlines():
        parts = line.split()
        _require(len(parts) >= 10 and '-' in parts and re.fullmatch('[0-9]+:[0-9]+',parts[2]))
        result.add(parts[2])
    return result


def collect_loop_evidence(rows, probe=None):
    """Cross-check independent kernel views; returns stable, path-free fingerprints."""
    probe = KernelLoopProbe() if probe is None else probe
    try:
        _require(type(rows) is list)
        configured = probe.configured_loops()
        _require(configured == sorted(row['name'] for row in rows))
        active = _active_zram(probe);mounted = _mounted(probe);result = [];seen = set()
        for row in rows:
            _require(type(row) is dict and set(row) == {'name','back-file','ro'})
            path = row['name']
            _require(type(path) is str and re.fullmatch(r'/dev/loop[0-9]+',path) and path not in seen
                     and type(row['ro']) in (bool,int) and row['ro'] == 0)
            seen.add(path);name = Path(path).name;device = tuple(probe.block_identity(path))
            _require(device == (7,int(name[4:])))
            probe.virtual_binding(name,device)
            status = probe.loop_status(path)
            _require(type(status) is dict and set(status) == {'device','inode','number','offset','sizelimit','flags'}
                     and all(type(v) is int and v >= 0 for v in status.values())
                     and status['number'] == device[1] and status['inode'] > 0
                     and status['offset'] == status['sizelimit'] == 0
                     and not status['flags'] & ~0x1d and not status['flags'] & 1)
            relationships = [(zram,proof) for zram,proof in active.items() if proof[1] == path]
            _require(len(relationships) == 1)
            zram,(zdevice,_,zsize) = relationships[0]
            _require('%d:%d' % device not in mounted and '%d:%d' % zdevice not in mounted
                     and not probe.children('/sys/class/block/'+name+'/holders')
                     and not any(re.fullmatch(name+r'p[0-9]+',p.name)
                                 for p in probe.children('/sys/class/block/'+name)))
            backing = probe.read('/sys/class/block/'+name+'/loop/backing_file').strip()
            _require(type(row['back-file']) is str and row['back-file'] == backing)
            info = tuple(probe.backing_identity(backing))
            _require(len(info) == 7 and all(type(v) is int and v >= 0 for v in info)
                     and info[:2] == (status['device'],status['inode']) and info[2] > 0
                     and info[3] == 0 and info[5] == 0o600 and info[6] == 1)
            _require(tuple(probe.block_identity(path)) == device and probe.loop_status(path) == status
                     and tuple(probe.backing_identity(backing)) == info
                     and probe.read('/sys/class/block/'+name+'/loop/backing_file').strip() == backing)
            probe.virtual_binding(name,device)
            result.append({'loop':list(device),'backing':list(info),'zram':list(zdevice),
                           'offset':0,'sizelimit':0,'flags':status['flags'],'zram_size':zsize})
        _require(_active_zram(probe) == active)
        _require(probe.configured_loops() == configured)
        after_mounts = _mounted(probe)
        _require(all('%d:%d' % tuple(v[key]) not in after_mounts for v in result for key in ('loop','zram')))
        return sorted(result,key=lambda row:row['loop'])
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError,struct.error) as error:
        raise BackupError('image_enrollment_loop_unverified') from error


def verified_loop_inventory(io, protected_identities):
    """Two complete observations, bound to BOTH images regardless of pathname."""
    try:
        protected = {tuple(identity[:2]) for identity in protected_identities}
        _require(len(protected) == 2)
        def sample():
            response = io.query(list(_QUERY))
            _require(response.returncode == 0)
            value = json.loads(response.stdout)
            _require(type(value) is dict and set(value) == {'loopdevices'} and type(value['loopdevices']) is list)
            rows = value['loopdevices']
            proof = io.enrollment_loop_evidence(rows)
            _require(type(proof) is list and len(proof) == len(rows)
                     and all(tuple(item['backing'][:2]) not in protected for item in proof))
            return proof
        first = sample();second = sample();_require(first == second)
        return first
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as error:
        raise BackupError('image_enrollment_loop_unverified') from error
