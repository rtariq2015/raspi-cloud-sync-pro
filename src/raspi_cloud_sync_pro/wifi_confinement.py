"""Read-only, held inspection of the pinned systemd 257.13 RNI BPF policy.

The trusted root caller independently authenticates the fresh unit, PID and
invocation. This module checks current kernel attachments and map contents; it
performs no canaries and never authorizes credentials or network acceptance.
Retained program/map FDs alone are insufficient: every recheck also opens the
currently named cgroup and queries its current attachments and wlan0 identity.
"""

import ctypes
import errno
import os
import platform
import re
import socket
import stat
from contextlib import contextmanager

from .errors import BackupError

_CGROUP = re.compile(r"/system\.slice/raspi-cloud-google-test-[0-9a-f]{32}\.service")
_READ_COMMANDS = frozenset((1, 4, 13, 14, 15, 16))
_MAX_PROGRAMS = 16
_MAX_MAPS = 16
_BPF_SYSCALL_AARCH64 = 280
_FAILURES = (OSError, ValueError, TypeError, OverflowError, KeyError, AttributeError)


class WifiConfinementError(BackupError):
    """Fixed codes only; never include paths, descriptor numbers or kernel IDs."""


def _fail(code="wifi_confinement_unavailable"):
    raise WifiConfinementError(code) from None


def _need(value):
    if not value:
        _fail()


class _Query(ctypes.Structure):
    _fields_ = [("target_fd", ctypes.c_uint32), ("attach_type", ctypes.c_uint32),
                ("query_flags", ctypes.c_uint32), ("attach_flags", ctypes.c_uint32),
                ("prog_ids", ctypes.c_uint64), ("prog_cnt", ctypes.c_uint32),
                ("pad", ctypes.c_uint32)]


class _Id(ctypes.Structure):
    _fields_ = [("identifier", ctypes.c_uint32), ("next_id", ctypes.c_uint32),
                ("flags", ctypes.c_uint32)]


class _Info(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_uint32), ("length", ctypes.c_uint32), ("info", ctypes.c_uint64)]


class _ProgramInfo(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("identifier", ctypes.c_uint32),
                ("tag", ctypes.c_ubyte * 8), ("jited_len", ctypes.c_uint32),
                ("xlated_len", ctypes.c_uint32), ("jited", ctypes.c_uint64),
                ("xlated", ctypes.c_uint64), ("load_time", ctypes.c_uint64),
                ("uid", ctypes.c_uint32), ("map_count", ctypes.c_uint32),
                ("maps", ctypes.c_uint64), ("name", ctypes.c_char * 16)]


class _MapInfo(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("identifier", ctypes.c_uint32),
                ("key_size", ctypes.c_uint32), ("value_size", ctypes.c_uint32),
                ("maximum", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("name", ctypes.c_char * 16)]


class _Element(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_uint32), ("pad", ctypes.c_uint32), ("key", ctypes.c_uint64),
                ("value", ctypes.c_uint64), ("flags", ctypes.c_uint64)]


def _bpf(number, attribute, *, missing=False):
    # Query/get-FD/get-info/lookup/next only. No load, attach, update or freeze.
    _need(number in _READ_COMMANDS and (not missing or number == 4))
    library = ctypes.CDLL(None, use_errno=True)
    result = library.syscall(_BPF_SYSCALL_AARCH64, number, ctypes.byref(attribute), ctypes.sizeof(attribute))
    if result < 0 and missing and ctypes.get_errno() == errno.ENOENT:
        return None
    _need(result >= 0)
    return result


def _object_info(fd, target):
    _bpf(15, _Info(fd, ctypes.sizeof(target), ctypes.addressof(target)))
    return target


def _current_interface_index():
    # Kernel metadata lookup only; no network data or canary is sent.
    return socket.if_nametoindex("wlan0")


def _validate(control_group, interface_index):
    _need(os.getresuid() == (0, 0, 0)
          and platform.system() == "Linux" and platform.machine() == "aarch64"
          and ctypes.sizeof(ctypes.c_void_p) == 8)
    _need(isinstance(control_group, str) and _CGROUP.fullmatch(control_group) is not None
          and type(interface_index) is int and 0 < interface_index < 2 ** 31)
    _need(_current_interface_index() == interface_index)


class _Descriptors:
    def __init__(self):
        self.fds = []

    def add(self, fd):
        try:
            _need(type(fd) is int and fd >= 0 and fd not in self.fds)
            os.set_inheritable(fd, False)
            self.fds.append(fd)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def close(self):
        failed = False
        while self.fds:
            fd = self.fds.pop()
            try:
                os.close(fd)
            except OSError:
                failed = True
        return not failed


def _directory_identity(fd):
    info = os.fstat(fd)
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    return info.st_dev, info.st_ino


def _open_chain(control_group, owner):
    # Every component is opened relative to the previous retained directory FD.
    fd = owner.add(os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
    identities = [_directory_identity(fd)]
    for part in ("sys", "fs", "cgroup", *control_group.split("/")[1:]):
        fd = owner.add(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd))
        identities.append(_directory_identity(fd))
    return fd, tuple(identities)


def _attachments(cgroup_fd, attach_type):
    identifiers = (ctypes.c_uint32 * _MAX_PROGRAMS)()
    query = _Query(cgroup_fd, attach_type, 0, 0, ctypes.addressof(identifiers), len(identifiers), 0)
    _bpf(16, query)
    values = tuple(identifiers[:query.prog_cnt])
    _need(0 < query.prog_cnt <= len(identifiers) and all(value > 0 for value in values)
          and len(set(values)) == len(values))
    return tuple(sorted(values)), query.attach_flags


def _program(fd, identifier):
    maps = (ctypes.c_uint32 * _MAX_MAPS)()
    info = _ProgramInfo()
    info.map_count, info.maps = len(maps), ctypes.addressof(maps)
    _object_info(fd, info)
    _need(info.identifier == identifier and info.type == 8 and info.uid == 0
          and info.map_count <= len(maps))
    values = tuple(maps[:info.map_count])
    _need(all(value > 0 for value in values) and len(set(values)) == len(values))
    return bytes(info.name).split(b"\0")[0], values


def _map(fd, identifier, interface_index):
    info = _object_info(fd, _MapInfo())
    _need(info.identifier == identifier and info.key_size == 4 and info.value_size == 1 and info.maximum == 1)
    name = bytes(info.name).split(b"\0")[0]
    key, value = ctypes.c_uint32(), ctypes.c_ubyte()
    if name == b"sd_restrictif":
        _need(info.type == 1 and info.flags == 0)
        key.value = interface_index
        _bpf(1, _Element(fd, 0, ctypes.addressof(key), ctypes.addressof(value), 0))
        _need(value.value == 0)
        first, second = ctypes.c_uint32(), ctypes.c_uint32()
        _bpf(4, _Element(fd, 0, 0, ctypes.addressof(first), 0))
        _need(first.value == interface_index)
        _need(_bpf(4, _Element(fd, 0, ctypes.addressof(first), ctypes.addressof(second), 0), missing=True) is None)
        return "interfaces"
    if name == b"restrict.rodata":
        _need(info.type == 2 and info.flags in (0x80, 0x480))
        _bpf(1, _Element(fd, 0, ctypes.addressof(key), ctypes.addressof(value), 0))
        _need(value.value == 1)
        return "allowlist"
    _fail()


class WifiConfinementProof:
    """Private held kernel references; callers must use recheck before relying on them."""

    def __init__(self, control_group, interface_index):
        self._control_group = control_group
        self._interface_index = interface_index
        self._owner = _Descriptors()
        self._program_fds = {}
        self._map_fds = {}
        self._snapshot = None
        self._closed = False
        self._valid = False

    def __repr__(self):
        return "<WifiConfinementProof private>"

    def safe_projection(self):
        return {"bpf_policy_observed": self._valid, "inspection_held": not self._closed,
                "canaries_performed": False, "strict_egress_accepted": False,
                "credential_release_authorized": False}

    def _inspect(self, initial):
        programs = []
        selected = []
        for attach_type, expected_name in ((0, b"sd_restrictif_i"), (1, b"sd_restrictif_e")):
            identifiers, flags = _attachments(self._cgroup_fd, attach_type)
            programs.append((identifiers, flags))
            matched = []
            for identifier in identifiers:
                if identifier not in self._program_fds:
                    _need(initial)
                    self._program_fds[identifier] = self._owner.add(_bpf(13, _Id(identifier, 0, 0)))
                name, maps = _program(self._program_fds[identifier], identifier)
                if name != expected_name:
                    continue
                _need(len(maps) == 2)
                roles = {}
                for map_id in maps:
                    if map_id not in self._map_fds:
                        _need(initial)
                        self._map_fds[map_id] = self._owner.add(_bpf(14, _Id(map_id, 0, 0)))
                    role = _map(self._map_fds[map_id], map_id, self._interface_index)
                    _need(role not in roles)
                    roles[role] = map_id
                _need(set(roles) == {"interfaces", "allowlist"})
                matched.append((identifier, roles["interfaces"], roles["allowlist"]))
            _need(len(matched) == 1)
            selected.append(matched[0])
        _need(selected[0][0] != selected[1][0] and selected[0][1:] == selected[1][1:])
        return tuple(programs), tuple(selected)

    def _acquire(self):
        self._cgroup_fd, self._chain = _open_chain(self._control_group, self._owner)
        self._snapshot = self._inspect(True)
        self.recheck()

    def recheck(self):
        """Re-query current named cgroup attachments and map bytes; failure closes and latches."""
        if self._closed:
            _fail("wifi_confinement_closed")
        try:
            _validate(self._control_group, self._interface_index)
            temporary = _Descriptors()
            try:
                _, identities = _open_chain(self._control_group, temporary)
                _need(identities == self._chain)
            finally:
                _need(temporary.close())
            _need(self._inspect(False) == self._snapshot)
            self._valid = True
            return self.safe_projection()
        except BaseException as error:
            self._valid = False
            self._close()
            if isinstance(error, WifiConfinementError):
                raise
            if isinstance(error, _FAILURES):
                _fail()
            raise

    def _close(self):
        self._valid = False
        self._closed = True
        return self._owner.close()


@contextmanager
def hold_wifi_confinement(control_group, interface_index):
    """Hold read-only kernel references for one exact fresh root-authenticated unit.

    This accepts only Linux/aarch64 root callers and the current numeric wlan0
    index. A disappearing/replaced cgroup or detached/replaced policy is refused,
    even when an older object remains alive through one of our retained FDs.
    Unit/PID/invocation authentication and active enforcement canaries remain the
    caller's responsibility. No successful return authorizes credential release.
    """
    proof = None
    try:
        _validate(control_group, interface_index)
        proof = WifiConfinementProof(control_group, interface_index)
        proof._acquire()
    except BaseException as error:
        if proof is not None:
            proof._close()
        if isinstance(error, WifiConfinementError):
            raise
        if isinstance(error, _FAILURES):
            _fail()
        raise
    try:
        yield proof
    finally:
        if not proof._close():
            _fail("wifi_confinement_cleanup_failed")
