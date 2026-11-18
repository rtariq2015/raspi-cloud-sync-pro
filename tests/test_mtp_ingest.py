"""Disposable MTP completion/FD-broker tests: no USB, services or real cloud."""

import array
from contextlib import contextmanager, redirect_stderr
from dataclasses import replace
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from raspi_cloud_sync_pro import mtp_broker, mtp_ingest
from raspi_cloud_sync_pro.mtp_ingest import CompletionRequest, CompletionSettings, MtpIngestError, publish_completed
from raspi_cloud_sync_pro.staging import ReadyBatch


class _MtpFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mtp-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source, self.work, self.staging = [self.base / name for name in ("source", "work", "staging")]
        for path in (self.source, self.work, self.staging):
            path.mkdir(mode=0o700)
        self.settings = CompletionSettings(self.source, self.work, self.staging,
                                           os.getuid(), os.getuid(), os.getgid(), reserve_bytes=0)
        self.request = CompletionRequest("reports/qa.bin", 8, 1, 2, 3)
        self.file = self.source / self.request.relative
        self.file.parent.mkdir(mode=0o700)
        self.file.write_bytes(b"synthetic"[:8])
        self.file.chmod(0o600)

    @contextmanager
    def descriptor(self, path=None, flags=os.O_RDONLY):
        fd = os.open(path or self.file, flags | os.O_NOFOLLOW)
        try:
            yield fd
        finally:
            os.close(fd)

    def publish(self, request=None, settings=None):
        with self.descriptor() as fd:
            return publish_completed(fd, request or self.request, settings or self.settings)

    def no_ready(self):
        self.assertEqual(list(self.staging.iterdir()), [])
        self.assertEqual(self.file.read_bytes(), b"synthetic"[:8])


class MtpCompletionTests(_MtpFixture, unittest.TestCase):
    def test_real_ready_batch_and_independent_hash(self):
        with self.descriptor() as fd:
            batch = publish_completed(fd, self.request, self.settings)
            self.assertEqual(os.pread(fd, 8, 0), b"synthetic"[:8])
        self.assertEqual(ReadyBatch.load(batch.root), batch)
        self.assertEqual(batch.files[0].sha256, hashlib.sha256(b"synthetic"[:8]).hexdigest())
        snapshot = batch.root / "files" / self.request.relative
        self.assertNotEqual(snapshot.stat().st_ino, self.file.stat().st_ino)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        self.assertEqual(batch.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(batch.root.name), 38)
        self.assertEqual(json.loads((batch.root / "mtp-completion.json").read_text())["transaction"], 2)

    def test_zero_byte_object_is_protocol_completed_not_quiet_time(self):
        self.file.write_bytes(b"")
        batch = self.publish(replace(self.request, size=0))
        self.assertEqual(batch.files[0].size, 0)
        self.assertEqual(batch.files[0].sha256, hashlib.sha256(b"").hexdigest())

    def test_repeated_filename_retains_distinct_snapshots(self):
        first = self.publish()
        self.file.write_bytes(b"changed!")
        second = self.publish(replace(self.request, transaction=3))
        self.assertNotEqual(first.root, second.root)
        self.assertEqual(ReadyBatch.load(first.root), first)
        self.assertEqual(ReadyBatch.load(second.root), second)
        self.assertNotEqual(first.files[0].sha256, second.files[0].sha256)

    def test_wrong_size_never_publishes(self):
        with self.assertRaises(MtpIngestError):
            self.publish(replace(self.request, size=9))
        self.no_ready()

    def test_oversize_refused_without_reading_object(self):
        with self.assertRaises(MtpIngestError) as caught:
            self.publish(replace(self.request, size=0xffffffff))
        self.assertEqual(caught.exception.code, "mtp_object_too_large")
        self.no_ready()

    def test_write_descriptor_refused(self):
        with self.descriptor(flags=os.O_RDWR) as fd, self.assertRaises(MtpIngestError):
            publish_completed(fd, self.request, self.settings)
        self.no_ready()

    def test_wrong_descriptor_path_refused(self):
        other = self.source / "other.bin"
        other.write_bytes(self.file.read_bytes())
        with self.descriptor(other) as fd, self.assertRaises(MtpIngestError):
            publish_completed(fd, self.request, self.settings)
        self.no_ready()

    def test_hardlink_alias_refused(self):
        os.link(self.file, self.source / "alias.bin")
        with self.assertRaises(MtpIngestError):
            self.publish()
        self.no_ready()

    def test_symlink_path_refused_even_with_real_file_fd(self):
        (self.source / "alias").symlink_to(self.file.parent, target_is_directory=True)
        with self.assertRaises(MtpIngestError):
            self.publish(replace(self.request, relative="alias/qa.bin"))
        self.no_ready()

    def test_traversal_and_invalid_transaction_types_refused(self):
        for request in (replace(self.request, relative="../qa.bin"), replace(self.request, relative="/qa.bin"),
                        replace(self.request, transaction=True), replace(self.request, object_id=0)):
            with self.subTest(request=request), self.assertRaises(MtpIngestError):
                self.publish(request)
        self.no_ready()

    def test_unsafe_root_or_owner_refused(self):
        with self.assertRaises(MtpIngestError):
            self.publish(settings=replace(self.settings, source_uid=os.getuid() + 1))
        self.source.chmod(0o755)
        with self.assertRaises(MtpIngestError):
            self.publish()
        self.no_ready()

    def test_overlapping_roots_refused(self):
        with self.assertRaises(MtpIngestError):
            self.publish(settings=replace(self.settings, work=self.source))
        self.no_ready()

    def test_source_mutation_during_copy_refused_and_retained(self):
        original = os.pread
        changed = False
        def mutate(fd, count, offset):
            nonlocal changed
            data = original(fd, count, offset)
            if not changed:
                changed = True
                self.file.write_bytes(b"changed!")
            return data
        with patch.object(mtp_ingest.os, "pread", side_effect=mutate), self.assertRaises(MtpIngestError):
            self.publish()
        self.assertEqual(list(self.staging.iterdir()), [])
        self.assertEqual(len(list(self.work.glob("*.incomplete"))), 1)
        self.assertTrue(self.file.exists())

    def test_low_space_refuses_without_deleting(self):
        with patch.object(mtp_ingest.os, "fstatvfs", return_value=SimpleNamespace(f_bavail=0, f_frsize=4096)):
            with self.assertRaises(MtpIngestError) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, "mtp_space_low")
        self.no_ready()

    def test_enospc_keeps_incomplete_evidence(self):
        with patch.object(mtp_ingest, "_write_all", side_effect=OSError(errno.ENOSPC, "fixture")):
            with self.assertRaises(MtpIngestError) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, "mtp_space_low")
        self.assertEqual(len(list(self.work.glob("*.incomplete"))), 1)
        self.no_ready()

    def test_zero_write_refused_not_success(self):
        with patch.object(mtp_ingest.os, "write", return_value=0), self.assertRaises(MtpIngestError):
            self.publish()
        self.assertEqual(len(list(self.work.glob("*.incomplete"))), 1)
        self.no_ready()

    def test_interrupted_fsync_retains_incomplete(self):
        with patch.object(mtp_ingest.os, "fsync", side_effect=OSError(errno.EIO, "fixture")):
            with self.assertRaises(MtpIngestError):
                self.publish()
        self.assertEqual(len(list(self.work.glob("*.incomplete"))), 1)
        self.no_ready()

    def test_failed_ack_boundary_can_retain_valid_published_batch(self):
        original = mtp_ingest._publish
        def lost_ack(*args):
            original(*args)
            raise OSError(errno.EIO, "fixture after publication")
        with patch.object(mtp_ingest, "_publish", side_effect=lost_ack), self.assertRaises(MtpIngestError):
            self.publish()
        batches = list(self.staging.glob("*.ready"))
        self.assertEqual(len(batches), 1)
        self.assertEqual(ReadyBatch.load(batches[0]).files[0].size, 8)
        self.assertTrue(self.file.exists())

    def test_publication_never_replaces_existing_directory(self):
        collision = self.staging / ("1" * 32 + ".ready")
        collision.mkdir(mode=0o700)
        (collision / "retained.txt").write_text("retained")
        with patch.object(mtp_ingest.uuid, "uuid4", return_value=SimpleNamespace(hex="1" * 32)):
            with self.assertRaises(MtpIngestError):
                self.publish()
        self.assertEqual((collision / "retained.txt").read_text(), "retained")
        self.assertEqual(len(list(self.work.glob("*.incomplete"))), 1)


class MtpBrokerTests(_MtpFixture, unittest.TestCase):
    def exchange(self, payload, descriptors=(), settings=None):
        client, server = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        result = {}
        def run():
            with server:
                result.update(mtp_broker.handle_connection(server, settings or self.settings, timeout=2))
        thread = threading.Thread(target=run)
        thread.start()
        try:
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", descriptors))] if descriptors else []
            client.sendmsg([payload], ancillary)
            answer = json.loads(client.recv(8192))
        finally:
            client.close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        return answer

    def test_real_fd_broker_publishes_and_preserves_original_fd(self):
        with self.descriptor() as fd:
            result = self.exchange(json.dumps(vars(self.request)).encode(), [fd])
            self.assertEqual(result["status"], "ready")
            self.assertEqual(os.pread(fd, 8, 0), b"synthetic"[:8])
        self.assertEqual(ReadyBatch.load(self.staging / result["batch"]).files[0].sha256, result["sha256"])

    def test_no_fd_or_multiple_fds_refused(self):
        payload = json.dumps(vars(self.request)).encode()
        with self.descriptor() as fd:
            for descriptors in ([], [fd, fd]):
                self.assertEqual(self.exchange(payload, descriptors)["status"], "refused")
        self.no_ready()

    def test_wrong_client_uid_refused(self):
        client, server = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            with self.descriptor() as fd:
                client.sendmsg([json.dumps(vars(self.request)).encode()],
                               [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
                result = mtp_broker.handle_connection(server, replace(self.settings, source_uid=os.getuid() + 1), timeout=1)
                self.assertEqual(result, {"status": "refused", "error": "mtp_broker_peer_refused"})
                self.assertEqual(json.loads(client.recv(8192)), result)
        finally:
            client.close()
            server.close()
        self.no_ready()

    def test_received_descriptor_cloexec_and_closed_after_use(self):
        seen = []
        original = mtp_broker.publish_completed
        def observe(fd, request, settings):
            seen.append(fd)
            self.assertFalse(os.get_inheritable(fd))
            return original(fd, request, settings)
        with patch.object(mtp_broker, "publish_completed", side_effect=observe), self.descriptor() as fd:
            self.assertEqual(self.exchange(json.dumps(vars(self.request)).encode(), [fd])["status"], "ready")
        self.assertEqual(len(seen), 1)
        with self.assertRaises(OSError):
            os.fstat(seen[0])

    def test_unexpected_completion_error_is_sanitized(self):
        with patch.object(mtp_broker, "publish_completed", side_effect=RuntimeError("private-fixture-name")), self.descriptor() as fd:
            result = self.exchange(json.dumps(vars(self.request)).encode(), [fd])
        self.assertEqual(result, {"status": "refused", "error": "mtp_broker_completion_failed"})
        self.assertNotIn("private-fixture-name", json.dumps(result))
        self.no_ready()

    def test_duplicate_json_keys_and_unknown_fields_refused(self):
        payloads = [b'{"relative":"a","relative":"b"}', json.dumps({**vars(self.request), "command": "unsafe"}).encode(), b'[' * 9000]
        with self.descriptor() as fd:
            for payload in payloads:
                self.assertEqual(self.exchange(payload, [fd])["status"], "refused")
        self.no_ready()

    def test_client_authenticates_server_peer_uid(self):
        address = self.base / "broker.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        listener.bind(str(address))
        listener.listen(1)
        def accept():
            connection, _ = listener.accept()
            with connection:
                mtp_broker.handle_connection(connection, self.settings, timeout=1)
        thread = threading.Thread(target=accept)
        thread.start()
        try:
            with self.descriptor() as fd, self.assertRaises(MtpIngestError):
                mtp_broker.send_completion(fd, self.request, address, expected_server_uid=os.getuid() + 1, timeout=1)
        finally:
            listener.close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.no_ready()

    def test_client_server_roundtrip_with_local_fixture_identity(self):
        address = self.base / "broker.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        listener.bind(str(address))
        listener.listen(1)
        def accept():
            connection, _ = listener.accept()
            with connection:
                mtp_broker.handle_connection(connection, self.settings, timeout=2)
        thread = threading.Thread(target=accept)
        thread.start()
        try:
            with self.descriptor() as fd:
                result = mtp_broker.send_completion(fd, self.request, address, expected_server_uid=os.getuid(), timeout=2)
            self.assertEqual(result["status"], "ready")
        finally:
            listener.close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_cli_argument_error_does_not_print_filename(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(mtp_broker.client_main(["--relative", "private-fixture-name"]), 1)
        self.assertNotIn("private-fixture-name", stderr.getvalue())


class MtpBrokerConfigurationTests(_MtpFixture, unittest.TestCase):
    @staticmethod
    def root_file(info, **changes):
        names = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_gid", "st_nlink")
        value = {name: getattr(info, name) for name in names}
        value.update(st_uid=0, st_gid=0)
        value.update(changes)
        return SimpleNamespace(**value)

    def test_runtime_parent_requires_root_nonwritable_source_group(self):
        valid = SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=0, st_gid=42)
        mtp_broker._runtime_metadata(valid, 42)
        variants = [dict(st_uid=os.getuid() or 1001), dict(st_mode=stat.S_IFDIR | 0o770),
                    dict(st_mode=stat.S_IFDIR | 0o700), dict(st_gid=43), dict(st_mode=stat.S_IFLNK | 0o750)]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(MtpIngestError):
                mtp_broker._runtime_metadata(SimpleNamespace(**{**vars(valid), **changes}), 42)

    def test_untrusted_ancestor_refused(self):
        good = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        with patch.object(Path, "lstat", return_value=good):
            mtp_broker._protected_ancestors(Path("/fixture/runtime/completion.sock"))
        for info in (SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1001),
                     SimpleNamespace(st_mode=stat.S_IFDIR | 0o775, st_uid=0),
                     SimpleNamespace(st_mode=stat.S_IFLNK | 0o755, st_uid=0)):
            with patch.object(Path, "lstat", return_value=info), self.assertRaises(MtpIngestError):
                mtp_broker._protected_ancestors(Path("/fixture/runtime/completion.sock"))

    @contextmanager
    def root_configuration_fixture(self, contents):
        path = self.base / "broker.json"
        path.write_text(contents)
        path.chmod(0o600)
        real_lstat, real_fstat = Path.lstat, os.fstat
        def metadata(value):
            info = real_lstat(value)
            return self.root_file(info, st_mode=stat.S_IFDIR | 0o755) if value != path else self.root_file(info)
        with patch.object(Path, "lstat", metadata), patch.object(mtp_broker.os, "fstat", side_effect=lambda fd: self.root_file(real_fstat(fd))):
            yield path

    def test_protected_config_held_read_passes_without_root(self):
        with self.root_configuration_fixture('{"schema_version":1}') as path:
            self.assertEqual(mtp_broker._read_configuration(path), {"schema_version": 1})

    def test_config_duplicate_keys_refused(self):
        with self.root_configuration_fixture('{"enabled":false,"enabled":true}') as path:
            with self.assertRaises(ValueError):
                mtp_broker._read_configuration(path)

    def test_config_change_after_open_refused_before_read(self):
        with self.root_configuration_fixture('{"schema_version":1}') as path:
            real = mtp_broker.os.fstat
            # Capture one valid mocked signature, then make the held FD differ.
            with self.descriptor(path) as fd:
                valid = real(fd)
            changed = self.root_file(valid, st_ino=valid.st_ino + 1)
            with patch.object(mtp_broker.os, "fstat", return_value=changed), patch.object(mtp_broker.os, "read") as read:
                with self.assertRaises(MtpIngestError):
                    mtp_broker._read_configuration(path)
                read.assert_not_called()

    def test_source_owned_runtime_refused_before_bind_or_chown(self):
        config = {"schema_version": 1, "enabled": True, "socket_path": "/fixture/runtime/completion.sock",
                  "source": "/fixture/source", "work": "/fixture/work", "staging": "/fixture/staging",
                  "source_uid": 1001, "target_uid": 1002, "target_gid": 1002, "reserve_bytes": 0,
                  "max_bytes": 0xfffffff2}
        bad = SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=1001, st_gid=42)
        with patch.object(mtp_broker.os, "geteuid", return_value=0), \
             patch.object(mtp_broker, "_read_configuration", return_value=config), \
             patch.object(mtp_broker, "_protected_ancestors"), \
             patch.object(mtp_broker.pwd, "getpwuid", return_value=SimpleNamespace(pw_gid=42)), \
             patch.object(Path, "resolve", lambda value, **kwargs: value), \
             patch.object(mtp_broker.os, "open", return_value=77), \
             patch.object(mtp_broker.os, "close"), \
             patch.object(mtp_broker.os, "fstat", return_value=bad), \
             patch.object(mtp_broker.os, "chown") as chown, \
             patch.object(mtp_broker.socket, "socket") as listener, redirect_stderr(io.StringIO()):
            self.assertEqual(mtp_broker.server_main(["--config", "/fixture/config.json"]), 1)
            listener.assert_not_called()
            chown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
