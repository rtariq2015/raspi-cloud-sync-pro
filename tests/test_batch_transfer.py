"""Private completed web-batch transfers use synthetic local data and inherited FDs."""

import copy
import errno
import fcntl
import hashlib
import io
import json
import math
import os
import stat
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import batch_transfer as transfer
from raspi_cloud_sync_pro.staging import ReadyBatch
from raspi_cloud_sync_pro.web_ingest import stage_upload


@unittest.skipIf(os.geteuid() == 0, 'Transfer requires ordinary identities')
class BatchTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.web = self.root / 'web-inbox'
        self.target = self.root / 'staging'
        self.web.mkdir(mode=0o700)
        self.target.mkdir(mode=0o700)
        self.sequence = 0
        self.trap = self.root / 'auth.json'
        self.trap.write_bytes(b'PRIVATE-dashboard-auth-not-part-of-any-batch')
        self.trap.chmod(0)
        self.addCleanup(self.trap.chmod, 0o600)

    @staticmethod
    def anchor(path):
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def make(self, values=None, *, manifest=None, raw=None):
        self.sequence += 1
        batch_id = '%032x' % self.sequence
        batch = self.web / (batch_id + '.ready')
        batch.mkdir(mode=0o700)
        files = batch / 'files'
        files.mkdir(mode=0o700)
        entries = []
        for name, value in ({} if values is None else values).items():
            path = files / name
            parent = files
            for component in Path(name).parts[:-1]:
                parent = parent / component
                parent.mkdir(mode=0o700, exist_ok=True)
            path.write_bytes(value)
            path.chmod(0o600)
            entries.append({'relative': name, 'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()})
        record = {'schema_version': 1, 'files': entries} if manifest is None else manifest
        payload = json.dumps(record, ensure_ascii=False, indent=2).encode() if raw is None else raw
        (batch / 'manifest.json').write_bytes(payload)
        (batch / 'manifest.json').chmod(0o600)
        return batch_id, batch

    def describe(self, batch_id):
        fd = self.anchor(self.web)
        result = transfer.describe_web_batch(self.web, batch_id, fd)
        self.assert_closed(fd)
        return result

    def run_transfer(self, batch_id, *, expected=None, timeout=3):
        expected = self.describe(batch_id)['manifest_sha256'] if expected is None else expected
        reader, writer = os.pipe()
        source_anchor, target_anchor = self.anchor(self.web), self.anchor(self.target)
        exports, failures = [], []
        def source():
            try:
                exports.append(transfer.export_web_batch(self.web, batch_id, source_anchor, writer, expected, timeout=timeout))
            except BaseException as error:
                failures.append(error)
        producer = threading.Thread(target=source)
        producer.start()
        try:
            result = transfer.import_web_batch(self.target, batch_id, target_anchor, reader, expected, timeout=timeout)
        finally:
            producer.join(timeout + 2)
            self.assertFalse(producer.is_alive(), 'Exporter did not finish within its deadline')
        for fd in (reader, writer, source_anchor, target_anchor):
            self.assert_closed(fd)
        if failures:
            raise failures[0]
        self.assertEqual(len(exports), 1)
        self.assert_public(exports[0])
        self.assert_public(result)
        return result

    def packet(self, batch_id, manifest, *, payload=b'', completion=True, tail=b'', header=None):
        canonical = transfer._canonical(manifest)
        digest = hashlib.sha256(canonical).hexdigest()
        header = transfer._header(batch_id, manifest, digest) if header is None else header
        encoded = transfer._canonical(header)
        result = len(encoded).to_bytes(4, 'big') + encoded + payload
        if completion:
            footer = transfer._canonical(transfer._completion(batch_id, digest))
            result += len(footer).to_bytes(4, 'big') + footer
        return digest, result + tail

    def import_packet(self, batch_id, expected, packet, *, timeout=1):
        self.assertLess(len(packet), 8192)
        reader, writer = os.pipe()
        os.write(writer, packet)
        os.close(writer)
        anchor = self.anchor(self.target)
        try:
            return transfer.import_web_batch(self.target, batch_id, anchor, reader, expected, timeout=timeout)
        finally:
            self.assert_closed(anchor)
            self.assert_closed(reader)

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as error:
            os.fstat(fd)
        self.assertEqual(error.exception.errno, errno.EBADF)

    def assert_public(self, report):
        self.assertEqual(set(report), {'schema_version', 'transfer_state', 'already_present',
                                      'uploads_enabled', 'automatic_deletion'})
        self.assertIs(report['uploads_enabled'], False)
        self.assertIs(report['automatic_deletion'], False)
        value = json.dumps(report)
        self.assertNotIn(str(self.root), value)
        self.assertNotIn('PRIVATE', value)
        self.assertNotIn('manifest_sha256', value)
        self.assertNotIn('batch_id', value)

    def assert_no_ready(self):
        self.assertEqual(list(self.target.glob('*.ready')), [])

    def test_actual_web_upload_empty_unicode_nested_and_manifest_formatting_roundtrip(self):
        actual = stage_upload(io.BytesIO(b'actual completed web data'), 'report.txt', self.web)
        source = {path: path.read_bytes() for path in actual.root.rglob('*') if path.is_file()}
        result = self.run_transfer(actual.root.stem)
        self.assertFalse(result['already_present'])
        for path, data in source.items():
            self.assertEqual(path.read_bytes(), data)
        batch_id, batch = self.make({'empty': b'', 'nested/QA α.csv': b'hello unicode\n', 'batch': b'no reserved collision'})
        original = (batch / 'manifest.json').read_bytes()
        result = self.run_transfer(batch_id)
        target = self.target / (batch_id + '.ready')
        self.assertEqual(ReadyBatch.load(target).files, ReadyBatch.load(batch).files)
        canonical = transfer._canonical(json.loads(original))
        self.assertEqual((target / 'manifest.json').read_bytes(), canonical)
        self.assertEqual((batch / 'manifest.json').read_bytes(), original)
        for path in target.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_large_fixture_streams_in_bounded_chunks_without_payload_ram_buffer(self):
        batch_id, batch = self.make({'large.bin': b''})
        payload = batch / 'files/large.bin'
        block = b'large synthetic stream block\n' * 2048
        digest = hashlib.sha256()
        with payload.open('wb') as stream:
            for _ in range(128):
                stream.write(block)
                digest.update(block)
        manifest = {'schema_version': 1, 'files': [{'relative': 'large.bin', 'size': len(block) * 128,
                                                    'sha256': digest.hexdigest()}]}
        (batch / 'manifest.json').write_text(json.dumps(manifest))
        read_sizes, write_sizes = [], []
        read, write = os.read, os.write
        def bounded_read(fd, size):
            read_sizes.append(size)
            return read(fd, size)
        def bounded_write(fd, value):
            write_sizes.append(len(value))
            return write(fd, value)
        with mock.patch.object(transfer.os, 'read', side_effect=bounded_read), \
             mock.patch.object(transfer.os, 'write', side_effect=bounded_write):
            self.run_transfer(batch_id, timeout=10)
        self.assertLessEqual(max(read_sizes), transfer.CHUNK_BYTES)
        self.assertLessEqual(max(write_sizes), transfer.CHUNK_BYTES)
        ReadyBatch.load(self.target / (batch_id + '.ready'))

    def test_describe_hash_is_strict_canonical_private_authoritative_metadata(self):
        batch_id, batch = self.make({'QA α.txt': b'content'})
        metadata = self.describe(batch_id)
        expected = hashlib.sha256(transfer._canonical(json.loads((batch / 'manifest.json').read_bytes()))).hexdigest()
        self.assertEqual(metadata, {'schema_version': 1, 'batch_id': batch_id, 'manifest_sha256': expected,
                                    'file_count': 1, 'total_bytes': 7})
        raw = json.loads((batch / 'manifest.json').read_bytes())
        (batch / 'manifest.json').write_text(json.dumps(raw, separators=(',', ':'), ensure_ascii=True))
        self.assertEqual(self.describe(batch_id), metadata)
        self.assertEqual(self.trap.stat().st_size, len(b'PRIVATE-dashboard-auth-not-part-of-any-batch'))

    def test_duplicate_deep_oversize_and_unsafe_manifest_data_are_refused(self):
        entry = {'relative': 'file', 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
        invalid = [b'{"schema_version":1,"schema_version":1,"files":[]}',
                   b'[' * 1200 + b'0' + b']' * 1200, b' ' * 65537]
        for raw in invalid:
            batch_id, _ = self.make(raw=raw)
            with self.subTest(size=len(raw)), self.assertRaises(transfer.BatchTransferError):
                self.describe(batch_id)
        manifests = [{'schema_version': True, 'files': []}, {'schema_version': 1, 'files': [entry, entry]},
                     {'schema_version': 1, 'files': [{**entry, 'relative': '../auth.json'}]},
                     {'schema_version': 1, 'files': [{**entry, 'relative': '/auth.json'}]},
                     {'schema_version': 1, 'files': [{**entry, 'relative': 'a/' * 8 + 'file'}]},
                     {'schema_version': 1, 'files': [{**entry, 'size': True}]},
                     {'schema_version': 1, 'files': [{**entry, 'size': transfer.MAX_FILE_BYTES + 1}]},
                     {'schema_version': 1, 'files': [{**entry, 'relative': 'a' * 256}]},
                     {'schema_version': 1, 'files': [{**entry, 'relative': 'a'}, {**entry, 'relative': 'a/b'}]},
                     {'schema_version': 1, 'files': [{**entry, 'relative': str(i)} for i in range(129)]}]
        for manifest in manifests:
            batch_id, _ = self.make(manifest=manifest)
            with self.subTest(manifest=manifest), self.assertRaises(transfer.BatchTransferError):
                self.describe(batch_id)
        self.assert_no_ready()

    def test_source_permissions_symlinks_hardlinks_and_extra_entries_fail_closed(self):
        for kind in ('manifest_mode', 'payload_mode', 'payload_symlink', 'payload_hardlink', 'extra', 'nested_link'):
            batch_id, batch = self.make({'file': b'keep source'})
            path = batch / 'files/file'
            if kind == 'manifest_mode':
                (batch / 'manifest.json').chmod(0o664)
            elif kind == 'payload_mode':
                path.chmod(0o644)
            elif kind == 'payload_symlink':
                path.rename(self.root / ('retained-' + batch_id))
                path.symlink_to(self.root / ('retained-' + batch_id))
            elif kind == 'payload_hardlink':
                os.link(path, self.root / ('retained-' + batch_id))
            elif kind == 'extra':
                (batch / 'files/extra').write_bytes(b'not declared')
            else:
                (batch / 'files/nested').symlink_to(self.root, target_is_directory=True)
            with self.subTest(kind=kind), self.assertRaises(transfer.BatchTransferError):
                self.describe(batch_id)
            self.assertTrue(batch.exists())
        self.assert_no_ready()

    def test_header_cannot_self_authorize_batch_id_or_manifest_binding(self):
        batch_id, batch = self.make({'file': b'data'})
        manifest = json.loads((batch / 'manifest.json').read_bytes())
        expected, packet = self.packet(batch_id, manifest, payload=b'data')
        with self.assertRaises(transfer.BatchTransferError):
            self.import_packet(batch_id, '0' * 64, packet)
        with self.assertRaises(transfer.BatchTransferError):
            self.import_packet('f' * 32, expected, packet)
        changed = copy.deepcopy(manifest)
        changed['files'][0]['relative'] = 'other'
        header = transfer._header(batch_id, changed, expected)
        _, packet = self.packet(batch_id, manifest, payload=b'data', header=header)
        with self.assertRaises(transfer.BatchTransferError):
            self.import_packet(batch_id, expected, packet)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_partial_missing_completion_corruption_and_extra_bytes_retain_data(self):
        for kind in ('disconnect', 'missing_completion', 'corrupt', 'extra'):
            batch_id, batch = self.make({'file': b'original'})
            manifest = json.loads((batch / 'manifest.json').read_bytes())
            expected, packet = self.packet(batch_id, manifest, payload=b'original')
            if kind == 'disconnect':
                packet = packet[:len(packet) - 200]
            elif kind == 'missing_completion':
                expected, packet = self.packet(batch_id, manifest, payload=b'original', completion=False)
            elif kind == 'corrupt':
                expected, packet = self.packet(batch_id, manifest, payload=b'corrupt!')
            else:
                packet += b'not EOF'
            with self.subTest(kind=kind), self.assertRaises(transfer.BatchTransferError):
                self.import_packet(batch_id, expected, packet)
            self.assert_no_ready()
            self.assertEqual((batch / 'files/file').read_bytes(), b'original')
        self.assertTrue(list(self.target.glob('*.incomplete')))

    def test_disconnect_keeps_even_a_short_received_payload_fragment(self):
        payload = b'x' * 4096
        batch_id, batch = self.make({'file': payload})
        manifest = json.loads((batch / 'manifest.json').read_bytes())
        expected, packet = self.packet(batch_id, manifest, payload=payload[:1024], completion=False)
        with self.assertRaises(transfer.BatchTransferError):
            self.import_packet(batch_id, expected, packet)
        partial = next(self.target.glob('*.incomplete'))
        self.assertEqual((partial / 'files/file').read_bytes(), payload[:1024])
        self.assert_no_ready()
        self.assertEqual((batch / 'files/file').read_bytes(), payload)

    def test_corrupt_source_cannot_complete_an_authoritative_manifest(self):
        batch_id, batch = self.make({'file': b'original'})
        (batch / 'files/file').write_bytes(b'corrupt!')
        metadata = self.describe(batch_id)  # Description is syntax/stat-only.
        with self.assertRaises(transfer.BatchTransferError):
            self.run_transfer(batch_id, expected=metadata['manifest_sha256'])
        self.assert_no_ready()
        self.assertEqual((batch / 'files/file').read_bytes(), b'corrupt!')

    def test_total_bound_and_low_space_refuse_before_target_creation(self):
        batch_id, batch = self.make({'file': b'data'})
        with mock.patch.object(transfer, 'MAX_TOTAL_BYTES', 3):
            with self.assertRaises(transfer.BatchTransferError):
                self.describe(batch_id)
        manifest = json.loads((batch / 'manifest.json').read_bytes())
        expected, packet = self.packet(batch_id, manifest, payload=b'data')
        with mock.patch.object(transfer.os, 'fstatvfs', return_value=mock.Mock(f_bavail=0, f_frsize=4096)):
            with self.assertRaises(transfer.BatchTransferError) as error:
                self.import_packet(batch_id, expected, packet)
        self.assertEqual(error.exception.code, 'batch_transfer_space_low')
        self.assertEqual(list(self.target.iterdir()), [])

    def test_completion_identity_and_duplicate_headers_never_publish(self):
        batch_id, batch = self.make({'file': b'data'})
        manifest = json.loads((batch / 'manifest.json').read_bytes())
        expected, packet = self.packet(batch_id, manifest, payload=b'data', completion=False)
        for value in ({**transfer._completion(batch_id, expected), 'schema_version': True},
                      {**transfer._completion(batch_id, expected), 'batch_id': 'f' * 32},
                      {**transfer._completion(batch_id, expected), 'extra': True}):
            footer = transfer._canonical(value)
            with self.subTest(value=value), self.assertRaises(transfer.BatchTransferError):
                self.import_packet(batch_id, expected, packet + len(footer).to_bytes(4, 'big') + footer)
        duplicate = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaises(transfer.BatchTransferError):
            self.import_packet(batch_id, expected, len(duplicate).to_bytes(4, 'big') + duplicate)
        with self.assertRaises(transfer.BatchTransferError):
            self.import_packet(batch_id, expected, (65537).to_bytes(4, 'big'))
        self.assert_no_ready()

    def test_source_change_after_payload_withholds_completion_and_publication(self):
        batch_id, batch = self.make({'file': b'original'})
        original = transfer._hash_file
        def changed(files, item, deadline, outgoing=None):
            result = original(files, item, deadline, outgoing)
            if outgoing is not None:
                path = batch / 'files/file'
                retained = self.root / 'source-retained'
                path.rename(retained)
                path.write_bytes(b'original')
                path.chmod(0o600)
            return result
        with mock.patch.object(transfer, '_hash_file', side_effect=changed):
            with self.assertRaises(transfer.BatchTransferError):
                self.run_transfer(batch_id)
        self.assert_no_ready()
        self.assertEqual((self.root / 'source-retained').read_bytes(), b'original')
        self.assertTrue(list(self.target.glob('*.incomplete')))

    def test_disk_bytes_are_rehashed_independently_before_publication(self):
        batch_id, _ = self.make({'file': b'original'})
        write = transfer._write_bytes
        def corrupt(fd, block):
            return write(fd, b'X' + block[1:])
        with mock.patch.object(transfer, '_write_bytes', side_effect=corrupt):
            with self.assertRaises(transfer.BatchTransferError):
                self.run_transfer(batch_id)
        self.assert_no_ready()
        self.assertTrue(list(self.target.glob('*.incomplete')))

    def test_existing_matching_target_is_independently_rehashed_and_never_replaced(self):
        batch_id, _ = self.make({'file': b'original'})
        self.run_transfer(batch_id)
        ready = self.target / (batch_id + '.ready')
        original = {path: (path.stat().st_ino, path.read_bytes()) for path in ready.rglob('*') if path.is_file()}
        self.assertTrue(self.run_transfer(batch_id)['already_present'])
        for path, value in original.items():
            self.assertEqual((path.stat().st_ino, path.read_bytes()), value)
        (ready / 'files/file').write_bytes(b'corrupt!')
        with self.assertRaises(transfer.BatchTransferError):
            self.run_transfer(batch_id)
        self.assertEqual((ready / 'files/file').read_bytes(), b'corrupt!')

    def test_concurrent_publication_never_overwrites_an_empty_or_mismatched_target(self):
        batch_id, _ = self.make({'file': b'original'})
        original = transfer._publish
        def competitor(root, incomplete, ready):
            os.mkdir(ready, mode=0o700, dir_fd=root)
            return original(root, incomplete, ready)
        with mock.patch.object(transfer, '_publish', side_effect=competitor):
            with self.assertRaises(transfer.BatchTransferError):
                self.run_transfer(batch_id)
        ready = self.target / (batch_id + '.ready')
        self.assertEqual(list(ready.iterdir()), [])
        self.assertTrue(list(self.target.glob('*.incomplete')))

    def test_two_actual_concurrent_imports_publish_once_and_retain_losing_partial(self):
        batch_id, batch = self.make({'file': b'shared correct content' * 4096})
        expected = self.describe(batch_id)['manifest_sha256']
        original = (batch / 'files/file').read_bytes()
        channels = [os.pipe(), os.pipe()]
        anchors = [(self.anchor(self.web), self.anchor(self.target)) for _ in range(2)]
        barrier = threading.Barrier(2)
        results, failures = [], []
        publish = transfer._publish
        def synchronized(*args):
            barrier.wait(3)
            return publish(*args)
        def exporting(index):
            try:
                transfer.export_web_batch(self.web, batch_id, anchors[index][0], channels[index][1], expected, timeout=4)
            except BaseException as error:
                failures.append(error)
        def importing(index):
            try:
                results.append(transfer.import_web_batch(self.target, batch_id, anchors[index][1],
                                                        channels[index][0], expected, timeout=4))
            except BaseException as error:
                failures.append(error)
        threads = [threading.Thread(target=operation, args=(index,))
                   for index in range(2) for operation in (exporting, importing)]
        with mock.patch.object(transfer, '_publish', side_effect=synchronized):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(6)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(sorted(result['already_present'] for result in results), [False, True])
        self.assertEqual(len(list(self.target.glob('*.ready'))), 1)
        self.assertEqual(len(list(self.target.glob('*.incomplete'))), 1)
        self.assertEqual((batch / 'files/file').read_bytes(), original)
        ReadyBatch.load(self.target / (batch_id + '.ready'))

    def test_fsync_failures_and_post_publish_failure_retain_every_artifact(self):
        batch_id, _ = self.make({'file': b'original'})
        with mock.patch.object(transfer.os, 'fsync', side_effect=OSError('PRIVATE-sync-context')):
            with self.assertRaises(transfer.BatchTransferError) as error:
                self.run_transfer(batch_id)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assert_no_ready()
        self.assertTrue(list(self.target.glob('*.incomplete')))
        published = []
        publish, fsync = transfer._publish, os.fsync
        def mark(*arguments):
            value = publish(*arguments)
            published.append(True)
            return value
        def fail_after(fd):
            if published:
                raise OSError('PRIVATE-post-publish')
            return fsync(fd)
        with mock.patch.object(transfer, '_publish', side_effect=mark), mock.patch.object(transfer.os, 'fsync', side_effect=fail_after):
            with self.assertRaises(transfer.BatchTransferError):
                self.run_transfer(batch_id)
        ready = self.target / (batch_id + '.ready')
        self.assertTrue(ready.is_dir())
        ReadyBatch.load(ready)
        self.assertTrue(self.run_transfer(batch_id)['already_present'])

    def test_postpublication_root_replacement_fails_without_losing_published_data(self):
        batch_id, batch = self.make({'file': b'anchored data remains recoverable'})
        original = (batch / 'files/file').read_bytes()
        retained = self.root / 'retained-staging'
        publish = transfer._publish

        def replace_root(root, incomplete, ready):
            # This happens after the importer's pre-publication anchor check.
            # The FD still names the old directory, while the configured path
            # now names a fresh, unrelated private directory.
            self.target.rename(retained)
            self.target.mkdir(mode=0o700)
            return publish(root, incomplete, ready)

        with mock.patch.object(transfer, '_publish', side_effect=replace_root):
            with self.assertRaises(transfer.BatchTransferError) as error:
                self.run_transfer(batch_id)
        self.assertEqual(error.exception.code, 'batch_transfer_source_changed')
        self.assertEqual(list(self.target.iterdir()), [])
        ready = retained / (batch_id + '.ready')
        self.assertEqual((ready / 'files/file').read_bytes(), original)
        ReadyBatch.load(ready)
        self.assertEqual((batch / 'files/file').read_bytes(), original)

    def test_deadline_expired_before_publication_keeps_only_incomplete_data(self):
        # An empty manifest has no file-hashing loop to incidentally notice an
        # expired deadline. The publication boundary needs its own check.
        batch_id, _ = self.make()
        clock = time.monotonic
        check_anchor = transfer._recheck_anchor
        expired = [False]

        def anchor_returns_late(path, fd):
            result = check_anchor(path, fd)
            if Path(path) == self.target:
                expired[0] = True
            return result

        with mock.patch.object(transfer, '_recheck_anchor', side_effect=anchor_returns_late), \
                mock.patch.object(transfer.time, 'monotonic',
                                  side_effect=lambda: clock() + (1000 if expired[0] else 0)):
            with self.assertRaises(transfer.BatchTransferError) as error:
                self.run_transfer(batch_id)
        self.assertEqual(error.exception.code, 'batch_transfer_timeout')
        self.assert_no_ready()
        self.assertTrue(list(self.target.glob('*.incomplete')))

    def test_final_fsync_crossing_deadline_fails_and_final_is_reconcilable(self):
        batch_id, _ = self.make({'file': b'valid final retained after late fsync'})
        clock, fsync, publish = time.monotonic, os.fsync, transfer._publish
        published, expired = [False], [False]

        def mark_publication(*arguments):
            result = publish(*arguments)
            published[0] = True
            return result

        def returns_after_deadline(fd):
            result = fsync(fd)
            if published[0]:
                expired[0] = True
            return result

        with mock.patch.object(transfer, '_publish', side_effect=mark_publication), \
                mock.patch.object(transfer.os, 'fsync', side_effect=returns_after_deadline), \
                mock.patch.object(transfer.time, 'monotonic',
                                  side_effect=lambda: clock() + (1000 if expired[0] else 0)):
            with self.assertRaises(transfer.BatchTransferError) as error:
                self.run_transfer(batch_id)
        self.assertEqual(error.exception.code, 'batch_transfer_timeout')
        ready = self.target / (batch_id + '.ready')
        original = (ready / 'files/file').stat().st_ino
        ReadyBatch.load(ready)
        self.assertTrue(self.run_transfer(batch_id)['already_present'])
        self.assertEqual((ready / 'files/file').stat().st_ino, original)

    def test_flush_rejects_injected_deep_or_expanding_trees_and_retains_them(self):
        directory = self.target / 'flush-fixture'
        directory.mkdir(mode=0o700)
        nested = directory
        for _ in range(transfer.MAX_DEPTH + 2):
            nested = nested / 'nested'
            nested.mkdir(mode=0o700)
        fd = self.anchor(directory)
        try:
            with self.assertRaises(transfer.BatchTransferError) as error:
                transfer._flush_tree(fd, time.monotonic() + 3)
            self.assertEqual(error.exception.code, 'batch_transfer_manifest_invalid')
        finally:
            os.close(fd)
        self.assertTrue(nested.is_dir())

        broad = self.target / 'broad-flush-fixture'
        broad.mkdir(mode=0o700)
        for number in range(32):
            path = broad / str(number)
            path.write_bytes(b'retained')
            path.chmod(0o600)
        fd = self.anchor(broad)
        try:
            with mock.patch.object(transfer, 'MAX_FILES', 1), mock.patch.object(transfer, 'MAX_DEPTH', 1):
                with self.assertRaises(transfer.BatchTransferError) as error:
                    transfer._flush_tree(fd, time.monotonic() + 3)
            self.assertEqual(error.exception.code, 'batch_transfer_manifest_invalid')
        finally:
            os.close(fd)
        self.assertEqual(len(list(broad.iterdir())), 32)

    def test_flush_checks_deadline_after_each_directory_fsync(self):
        directory = self.target / 'flush-fixture'
        directory.mkdir(mode=0o700)
        fd = self.anchor(directory)
        clock, fsync = time.monotonic, os.fsync
        expired = [False]

        def returns_after_deadline(value):
            result = fsync(value)
            expired[0] = True
            return result

        try:
            with mock.patch.object(transfer.os, 'fsync', side_effect=returns_after_deadline), \
                    mock.patch.object(transfer.time, 'monotonic',
                                      side_effect=lambda: clock() + (1000 if expired[0] else 0)):
                with self.assertRaises(transfer.BatchTransferError) as error:
                    transfer._flush_tree(fd, clock() + 3)
            self.assertEqual(error.exception.code, 'batch_transfer_timeout')
        finally:
            os.close(fd)
        self.assertTrue(directory.is_dir())

    def test_fd_ownership_stdio_alias_direction_and_anchor_binding_are_enforced(self):
        batch_id, _ = self.make({'file': b'data'})
        expected = self.describe(batch_id)['manifest_sha256']
        for standard in (0, 1, 2):
            root = self.anchor(self.web)
            with self.assertRaises(transfer.BatchTransferError):
                transfer.export_web_batch(self.web, batch_id, root, standard, expected)
            self.assert_closed(root)
            os.fstat(standard)
        alias = os.dup(1)
        root = self.anchor(self.web)
        try:
            with self.assertRaises(transfer.BatchTransferError):
                transfer.export_web_batch(self.web, batch_id, root, alias, expected)
            os.fstat(alias)
            self.assert_closed(root)
        finally:
            os.close(alias)
        read, write = os.pipe()
        wrong = self.anchor(self.target)
        try:
            with self.assertRaises(transfer.BatchTransferError):
                transfer.export_web_batch(self.web, batch_id, wrong, write, expected)
            os.fstat(wrong)
            self.assert_closed(write)
        finally:
            os.close(wrong)
            os.close(read)
        read, write = os.pipe()
        root = self.anchor(self.web)
        try:
            with self.assertRaises(transfer.BatchTransferError):
                transfer.export_web_batch(self.web, batch_id, root, read, expected)
            os.fstat(read)  # Invalid direction was never accepted.
            self.assert_closed(root)
        finally:
            os.close(read)
            os.close(write)

    def test_source_root_and_batch_paths_cannot_escape_to_auth_or_symlinks(self):
        batch_id, batch = self.make({'file': b'data'})
        for value in ('../auth.json', '/etc/passwd', batch_id + '.ready', 'F' * 32):
            with self.subTest(value=value), self.assertRaises(transfer.BatchTransferError):
                transfer.describe_web_batch(self.web, value, self.anchor(self.web))
        alias = self.root / 'linked-web'
        alias.symlink_to(self.web, target_is_directory=True)
        root = self.anchor(self.web)
        try:
            with self.assertRaises(transfer.BatchTransferError):
                transfer.describe_web_batch(alias, batch_id, root)
            os.fstat(root)
        finally:
            os.close(root)
        batch.rename(self.web / 'retained')
        batch.symlink_to(self.web / 'retained', target_is_directory=True)
        with self.assertRaises(transfer.BatchTransferError):
            self.describe(batch_id)

    def test_backpressure_and_missing_eof_timeout_are_absolute_and_restore_flags(self):
        batch_id, _ = self.make({'file': b'x' * (transfer.CHUNK_BYTES * 3)})
        expected = self.describe(batch_id)['manifest_sha256']
        read, write = os.pipe()
        alias = os.dup(write)
        flags = fcntl.fcntl(alias, fcntl.F_GETFL)
        root = self.anchor(self.web)
        started = time.monotonic()
        try:
            with self.assertRaises(transfer.BatchTransferError) as error:
                transfer.export_web_batch(self.web, batch_id, root, write, expected, timeout=0.03)
            self.assertEqual(error.exception.code, 'batch_transfer_timeout')
            self.assertLess(time.monotonic() - started, 0.5)
            self.assert_closed(write)
            self.assert_closed(root)
            self.assertEqual(fcntl.fcntl(alias, fcntl.F_GETFL), flags)
        finally:
            os.close(read)
            os.close(alias)
        batch_id, batch = self.make({'empty': b''})
        manifest = json.loads((batch / 'manifest.json').read_bytes())
        expected, packet = self.packet(batch_id, manifest)
        read, write = os.pipe()
        os.write(write, packet)
        root = self.anchor(self.target)
        try:
            with self.assertRaises(transfer.BatchTransferError) as error:
                transfer.import_web_batch(self.target, batch_id, root, read, expected, timeout=0.03)
            self.assertEqual(error.exception.code, 'batch_transfer_timeout')
            self.assert_closed(read)
            self.assert_closed(root)
        finally:
            os.close(write)
        self.assert_no_ready()

    def test_deadline_boundaries_are_finite_and_allow_long_configured_transfers(self):
        for value in (0, -1, True, math.nan, math.inf, 3600.1, '300'):
            with self.subTest(value=value), self.assertRaises(transfer.BatchTransferError):
                transfer._deadline(value)
        self.assertGreater(transfer._deadline(3600), time.monotonic() + 3599)
        self.assertGreater(transfer._deadline(300), time.monotonic() + 299)

    def test_no_network_process_or_credential_access_and_errors_stay_sanitized(self):
        batch_id, batch = self.make({'file': b'private synthetic payload'})
        before = {path: (path.stat().st_ino, path.read_bytes()) for path in batch.rglob('*') if path.is_file()}
        with mock.patch('socket.socket', side_effect=AssertionError('No network')), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('No process')), \
             mock.patch('subprocess.run', side_effect=AssertionError('No process')), \
             mock.patch('os.system', side_effect=AssertionError('No shell')):
            self.run_transfer(batch_id)
        for path, value in before.items():
            self.assertEqual((path.stat().st_ino, path.read_bytes()), value)
        private_value = 'PRIVATE-' + 'untrusted-path'
        try:
            transfer.describe_web_batch(self.web, private_value, self.anchor(self.web))
        except transfer.BatchTransferError as error:
            text = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
            self.assertNotIn(private_value, text)
            self.assertNotIn('private synthetic payload', text)
        else:
            self.fail('Unsafe ID accepted')


if __name__ == '__main__':
    unittest.main()
