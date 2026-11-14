"""Actual Samba share-mode acceptance in an isolated, disposable Linux container.

No host port, disk image, real cloud account, gadget operation or hardware gate is
used. Run only with Docker --network none and a read-only project mount. The
container is discarded; local failure evidence remains until container removal.
"""

import contextlib
import hashlib
import json
import os
import pwd
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.smb_ingest import stage_smb_version


PORT = 1445
SHARE = "Fixture"
SERVER = "127.0.0.1"
SHARING_VIOLATION = 0xC0000043
ACCESS_DENIED = 0xC0000022


def require_fixture():
    if os.geteuid() != 0 or not Path('/.dockerenv').exists():
        raise RuntimeError("This helper requires the disposable Docker fixture, not a host or Pi")
    # --network none should expose only loopback. Never bind while another link exists.
    if {entry.name for entry in Path('/sys/class/net').iterdir()} != {'lo'}:
        raise RuntimeError("Refusing to run without Docker --network none")


def status(error):
    return getattr(error, 'ntstatus', None)


def denied(action, expected):
    try:
        result = action()
    except OSError as error:
        if status(error) != expected:
            raise AssertionError("Unexpected SMB error status") from error
        return
    if hasattr(result, 'close'):
        result.close()
    raise AssertionError("Conflicting or unauthorized SMB operation unexpectedly succeeded")


class FixtureReader:
    """Separate loopback high-port client; production hardware gates stay unchanged."""

    def __init__(self, smbclient, password, *, held=None, proceed=None, interrupt=False):
        self.client, self.password = smbclient, password
        self.held, self.proceed, self.interrupt = held, proceed, interrupt

    @contextlib.contextmanager
    def open_version(self, relative):
        path = f'\\\\{SERVER}\\{SHARE}\\{relative}'
        try:
            with self.client.open_file(path, mode='rb', share_access='r', buffering=0,
                                       username='fixture_read', password=self.password,
                                       port=PORT, encrypt=True, connection_timeout=5) as stream:
                if self.held:
                    self.held.set()
                    if not self.proceed.wait(10):
                        raise RuntimeError('Fixture synchronization timeout')
                if self.interrupt:
                    class InterruptedStream:
                        def __init__(self, source):
                            self.source, self.reads = source, 0
                        def seek(self, *args):
                            return self.source.seek(*args)
                        def read(self, size):
                            self.reads += 1
                            if self.reads > 1:
                                raise OSError('Injected fixture read interruption')
                            return self.source.read(min(size, 8192))
                    yield InterruptedStream(stream)
                else:
                    yield stream
        except OSError as error:
            raise BackupError('smb_version_unavailable', retryable=True) from error


def main():
    require_fixture()
    import smbclient
    root = Path(tempfile.mkdtemp(prefix='raspi-samba-protocol-'))
    root.chmod(0o755)  # Unix accounts may traverse to the deliberately shared subdirectory.
    for name in ('private', 'state', 'lock', 'cache', 'run', 'share'):
        (root / name).mkdir(mode=0o700)
    subprocess.run(['groupadd', 'fixture_sync'], check=True, capture_output=True)
    for name in ('fixture_write', 'fixture_read'):
        subprocess.run(['useradd', '--no-create-home', '--shell', '/usr/sbin/nologin',
                        '--gid', 'fixture_sync', name], check=True, capture_output=True)
    writer = pwd.getpwnam('fixture_write')
    os.chown(root / 'share', writer.pw_uid, writer.pw_gid)
    (root / 'share').chmod(0o750)
    config = root / 'smb.conf'
    config.write_text(f'''[global]
server role = standalone server
security = user
workgroup = WORKGROUP
interfaces = 127.0.0.1
bind interfaces only = yes
hosts allow = 127.0.0.1
smb ports = {PORT}
server min protocol = SMB3_00
server max protocol = SMB3_11
server signing = mandatory
smb encrypt = required
map to guest = Never
restrict anonymous = 2
load printers = no
disable spoolss = yes
printing = bsd
printcap name = /dev/null
usershare max shares = 0
passdb backend = tdbsam:{root}/private/passdb.tdb
private dir = {root}/private
state directory = {root}/state
lock directory = {root}/lock
cache directory = {root}/cache
pid directory = {root}/run
ncalrpc dir = {root}/run/ncalrpc
log file = {root}/server.log
log level = 0
[Fixture]
path = {root}/share
read only = yes
guest ok = no
valid users = fixture_write fixture_read
write list = fixture_write
force group = fixture_sync
create mask = 0640
force create mode = 0640
directory mask = 0750
force directory mode = 0750
follow symlinks = no
wide links = no
strict locking = yes
strict sync = yes
csc policy = disable
''')
    config.chmod(0o600)
    subprocess.run(['testparm', '-s', str(config)], check=True, capture_output=True)
    passwords = {name: secrets.token_urlsafe(32) for name in ('fixture_write', 'fixture_read')}
    for name, password in passwords.items():
        subprocess.run(['smbpasswd', '-s', '-a', '-c', str(config), name],
                       input=password+'\n'+password+'\n', text=True, capture_output=True, check=True)
    server = subprocess.Popen(['smbd', '--foreground', '--no-process-group', '--configfile='+str(config)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {'environment': 'isolated Linux container; not Pi, Windows or USB acceptance',
              'samba': subprocess.check_output(['smbd', '--version'], text=True).strip()}
    try:
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError('Fixture smbd exited before listening; inspect retained container log')
            try:
                with socket.create_connection((SERVER, PORT), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Fixture smbd did not listen')
        writer_session = smbclient.register_session(SERVER, username='fixture_write', password=passwords['fixture_write'],
                                   port=PORT, encrypt=True, connection_timeout=5)
        smbclient.register_session(SERVER, username='fixture_read', password=passwords['fixture_read'],
                                   port=PORT, encrypt=True, connection_timeout=5)
        assert writer_session.encrypt_data is True
        assert writer_session.connection.dialect >= 0x0300
        report.update(encrypted_smb3_session=True, dialect=hex(writer_session.connection.dialect))
        path = f'\\\\{SERVER}\\{SHARE}\\QA.bin'
        def open_writer(mode='r+b'):
            return smbclient.open_file(path, mode=mode, share_access='rwd', buffering=0,
                                       username='fixture_write', password=passwords['fixture_write'],
                                       port=PORT, encrypt=True)
        reader = FixtureReader(smbclient, passwords['fixture_read'])
        payload = b'QA protocol fixture\n' * 8192
        with open_writer('w+b') as stream:
            stream.write(payload)
            stream.flush()
            try:
                stage_smb_version(reader, 'QA.bin', root / 'staging', reserve_bytes=0)
            except BackupError as error:
                assert error.retryable and error.__cause__ is not None
                assert status(error.__cause__) == SHARING_VIOLATION
            else:
                raise AssertionError('An active writer was staged')
            assert not (root / 'staging').exists()
        report['active_writer_defers_before_batch_creation'] = True
        held, proceed = threading.Event(), threading.Event()
        locked_reader = FixtureReader(smbclient, passwords['fixture_read'], held=held, proceed=proceed)
        results, errors = [], []
        def capture():
            try:
                results.append(stage_smb_version(locked_reader, 'QA.bin', root / 'staging', reserve_bytes=0))
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=capture, daemon=True)
        worker.start()
        assert held.wait(10), 'Snapshot reader did not acquire the protected handle'
        try:
            denied(open_writer, SHARING_VIOLATION)
            denied(lambda: smbclient.remove(path, username='fixture_write', password=passwords['fixture_write'],
                                             port=PORT), SHARING_VIOLATION)
        finally:
            proceed.set()
            worker.join(10)
        assert not worker.is_alive() and not errors and len(results) == 1
        denied(lambda: smbclient.open_file(path, mode='r+b', username='fixture_read',
                                           password=passwords['fixture_read'], port=PORT), ACCESS_DENIED)
        batch = results[0]
        assert (batch.root / 'files/QA.bin').read_bytes() == payload
        assert batch.files[0].sha256 == hashlib.sha256(payload).hexdigest()
        report.update(snapshot_blocks_new_write_and_delete=True, backup_account_cannot_write=True,
                      post_close_staged_bytes_and_sha256_match=True)
        ready_before = set((root / 'staging').glob('*.ready'))
        try:
            stage_smb_version(FixtureReader(smbclient, passwords['fixture_read'], interrupt=True),
                              'QA.bin', root / 'staging', reserve_bytes=0)
        except BackupError:
            pass
        else:
            raise AssertionError('Injected read interruption did not fail')
        assert set((root / 'staging').glob('*.ready')) == ready_before
        assert len(list((root / 'staging').glob('*.incomplete'))) == 1
        report['injected_read_failure_retains_incomplete_not_ready'] = True
        with reader.open_version('QA.bin') as original:
            assert original.read() == payload
        with open_writer() as stream:
            stream.write(b'Updated')
            stream.flush()
        changed = stage_smb_version(reader, 'QA.bin', root / 'staging', reserve_bytes=0)
        assert changed.files[0].sha256 != batch.files[0].sha256
        assert (batch.root / 'files/QA.bin').read_bytes() == payload
        report.update(original_retained=True, changed_version_keeps_previous_snapshot=True,
                      cloud_accounts_used=False, hardware_gate_changed=False)
        print(json.dumps(report, indent=2))
    finally:
        smbclient.reset_connection_cache()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == '__main__':
    main()
