"""Opt-in recovery of one saved Wi-Fi profile; never restart networking or USB."""
import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

APPROVED_TEST_BUILD = False
CONFIG = Path('/etc/raspi-cloud-sync-pro/wifi-recovery.json')
STATE = Path('/var/lib/raspi-cloud-sync-pro/wifi-recovery')
OWNER = 0
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
FIELDS = 'GENERAL.STATE,GENERAL.NM-MANAGED,GENERAL.CON-UUID,IP4.ADDRESS,IP4.GATEWAY'


class Refused(Exception):
    pass


def require(value):
    if not value:
        raise Refused('unsafe_configuration_or_state')


def private(fd, directory=False):
    info = os.fstat(fd)
    require(info.st_uid == OWNER and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)


def parents(path):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER and not info.st_mode & 0o022)


def unique(items):
    result = dict(items)
    require(len(result) == len(items))
    return result


def read_json(path):
    parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        private(fd)
        raw = os.read(fd, 8193)
        require(len(raw) <= 8192)
        return json.loads(raw, object_pairs_hook=unique)
    finally:
        os.close(fd)


def config():
    cfg = read_json(CONFIG)
    require(type(cfg) is dict and set(cfg) == {'schema_version', 'enabled', 'interface', 'approved_uuid', 'grace_failures', 'cooldown_seconds'})
    require(type(cfg['schema_version']) is int and cfg['schema_version'] == 1 and type(cfg['enabled']) is bool)
    require(cfg['interface'] == 'wlan0' and type(cfg['approved_uuid']) is str and str(uuid.UUID(cfg['approved_uuid'])) == cfg['approved_uuid'])
    require(type(cfg['grace_failures']) is int and cfg['grace_failures'] == 3)
    require(type(cfg['cooldown_seconds']) is int and cfg['cooldown_seconds'] == 300)
    return cfg


def guard():
    require(os.geteuid() == 0)
    require(Path('/proc/device-tree/model').read_bytes().rstrip(bytes([0])) == b'Raspberry Pi Zero 2 W Rev 1.0')
    device = Path('/sys/class/block/mmcblk0p2/dev').read_text().strip()
    root = os.stat('/').st_dev
    require(device == f'{os.major(root)}:{os.minor(root)}')


def command(args, timeout=8):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                env=ENV, timeout=timeout, check=False)
        return result.returncode, result.stdout if len(result.stdout) <= 8192 else ''
    except (OSError, subprocess.TimeoutExpired):
        return None, ''


def observe(cfg):
    try:
        return _observe(cfg)
    except (OSError, ValueError, TypeError, KeyError):
        return 'unknown'


def _observe(cfg):
    code, text = command(['/usr/bin/nmcli', 'radio', 'wifi'])
    if code != 0 or text.strip() != 'enabled':
        return 'unknown'
    radios = list(Path('/sys/class/rfkill').glob('rfkill*'))
    wifi = [p for p in radios if (p / 'type').read_text().strip() == 'wlan']
    if not wifi or any((p / flag).read_text().strip() != '0' for p in wifi for flag in ('soft', 'hard')):
        return 'unknown'
    code, text = command(['/usr/bin/nmcli', '-t', '--escape', 'no', '-f', FIELDS, 'device', 'show', 'wlan0'])
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition(':')
        key = key.split('[', 1)[0]
        if separator and key in FIELDS.split(','):
            fields.setdefault(key, []).append(value.strip())
    if code != 0 or fields.get('GENERAL.NM-MANAGED') != ['yes']:
        return 'unknown'
    if any(len(fields.get(key, [])) != 1 for key in ('GENERAL.STATE', 'GENERAL.CON-UUID')):
        return 'unknown'
    state = fields['GENERAL.STATE'][0].split(' ', 1)[0]
    active = fields.get('GENERAL.CON-UUID', [''])[0]
    if state == '100' and active != cfg['approved_uuid']:
        return 'other_profile'
    if state not in ('30', '100'):
        return 'unknown'
    code, text = command(['/usr/bin/nmcli', '-g', 'connection.type,connection.interface-name', 'connection', 'show', 'uuid', cfg['approved_uuid']])
    if code != 0 or text.splitlines() not in (['802-11-wireless', 'wlan0'], ['802-11-wireless', '']):
        return 'unknown'
    if state == '30' and active in ('', '--'):
        return 'disconnected'
    if state != '100' or active != cfg['approved_uuid']:
        return 'unknown'  # Connecting, unavailable, unmanaged and transitional states never trigger recovery.
    gateways = fields.get('IP4.GATEWAY', [])
    if len(gateways) != 1:
        return 'unknown'
    gateway = ipaddress.IPv4Address(gateways[0])
    addresses = [ipaddress.IPv4Interface(x) for x in fields.get('IP4.ADDRESS', []) if ':' not in x]
    if gateway.is_unspecified or gateway.is_multicast or gateway.is_loopback or not any(gateway in x.network for x in addresses):
        return 'unknown'
    code, _ = command(['/usr/bin/ping', '-I', 'wlan0', '-c', '1', '-W', '4', str(gateway)], timeout=6)
    if code == 0:
        return 'healthy'
    if code != 1:
        return 'unknown'  # Missing permissions/tools are not a link failure.
    code, text = command(['/usr/sbin/ip', '-j', 'neigh', 'show', 'to', str(gateway), 'dev', 'wlan0'])
    rows = json.loads(text) if code == 0 else None
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict or rows[0].get('dst') != str(gateway) or rows[0].get('dev', 'wlan0') != 'wlan0':
        return 'unknown'
    neighbour = rows[0].get('state')
    if neighbour == ['FAILED']:
        return 'layer2_failed'
    return 'healthy' if neighbour in ([x] for x in ('REACHABLE', 'STALE', 'DELAY', 'PROBE', 'PERMANENT', 'NOARP')) else 'unknown'


def load_state(boot, now):
    try:
        state = read_json(STATE / 'state.json')
    except FileNotFoundError:
        state = {'schema_version': 1, 'boot_id': boot, 'failures': 0, 'last_attempt': None}
    require(type(state) is dict and set(state) == {'schema_version', 'boot_id', 'failures', 'last_attempt'})
    require(type(state['schema_version']) is int and state['schema_version'] == 1)
    require(type(state['failures']) is int and 0 <= state['failures'] <= 3)
    require(type(state['boot_id']) is str and str(uuid.UUID(state['boot_id'])) == state['boot_id'])
    last = state['last_attempt']
    require(last is None or type(last) in (float, int) and 0 <= last <= 10**12 and math.isfinite(last))
    if state['boot_id'] == boot:
        require(last is None or last <= now)
    return state


def save(state):
    temporary = STATE / ('.state-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(state, stream, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, STATE / 'state.json')
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def tick(cfg, boot, now):
    state = load_state(boot, now)
    before = dict(state)
    if state['boot_id'] != boot:
        state.update(boot_id=boot, failures=0, last_attempt=None)
    health = observe(cfg)
    last = state['last_attempt']
    recovering = health in {'disconnected', 'layer2_failed'}
    cooldown = last is not None and now - last < cfg['cooldown_seconds']
    state['failures'] = min(3, state['failures'] + 1) if recovering and not cooldown else 0
    attempt = state['failures'] == cfg['grace_failures']
    if attempt and observe(cfg) not in {'disconnected', 'layer2_failed'}:
        state['failures'] = 0
        attempt = False
        health = 'changed_before_activation'
    if attempt:
        state.update(failures=0, last_attempt=now)
    if state != before:
        save(state)  # Durable cooldown precedes activation, including timeout/failure.
    if not attempt:
        return {'status': 'cooldown' if recovering and cooldown else health, 'failures': state['failures']}
    code, _ = command(['/usr/bin/nmcli', '--wait', '25', 'connection', 'up', 'uuid', cfg['approved_uuid'], 'ifname', 'wlan0'], timeout=30)
    return {'status': 'activation_requested' if code == 0 else 'activation_failed', 'returncode': code}


def main():
    if APPROVED_TEST_BUILD is not True:
        return {'status': 'hardware_disabled'}
    try:
        guard()
        cfg = config()
        if not cfg['enabled']:
            return {'status': 'configuration_disabled'}
        parents(STATE)
        STATE.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            private(fd, directory=True)
        finally:
            os.close(fd)
        lock_fd = os.open(STATE / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            private(lock.fileno())
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            return tick(cfg, boot, time.monotonic())
    except (OSError, ValueError, TypeError, Refused):
        return {'status': 'refused'}


if __name__ == '__main__':
    print(json.dumps(main(), sort_keys=True))
