"""Opt-in space policy tests: temporary files and mocked filesystem observations."""

from dataclasses import FrozenInstanceError
import errno
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from raspi_cloud_sync_pro import capture_space, staging
from raspi_cloud_sync_pro.capture_space import (
    CAPACITY_POLICY, CONTROL_RESERVE, INODE_RESERVE, LEGACY_POLICY, MAX_INTEGER,
    OS_RESERVE, CaptureSpacePolicy, approved_capture_policy,
)
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.mass_storage_image import EXPANDED_SIZE, LEGACY_SIZE
from raspi_cloud_sync_pro.staging import ReadyBatch, StagedFile, stage_changed_snapshot
from raspi_cloud_sync_pro.storage import Ownership
from test_storage import staging_owner

GIB = 1024 ** 3
UNIT = 4096


def filesystem(available=48 * GIB, *, unit=UNIT, **changes):
    """An allocation-granularity observation; available already excludes usage."""
    value = dict(f_frsize=unit, f_bsize=unit, f_blocks=64 * GIB // unit,
                 f_bfree=available // unit, f_bavail=available // unit,
                 f_files=100000, f_favail=90000)
    value.update(changes)
    return SimpleNamespace(**value)


def item(relative='report.txt', data=b'synthetic report'):
    return StagedFile(relative, len(data), hashlib.sha256(data).hexdigest())


class CaptureSpaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-space-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.destination = self.root/'source', self.root/'staging'
        self.source.mkdir(mode=0o700)
        self.destination.mkdir(mode=0o700)
        self.policy = CaptureSpacePolicy()

    def statvfs(self, value=None, *, side_effect=None):
        return mock.patch.object(capture_space.os, 'fstatvfs',
                                 return_value=filesystem() if value is None else value,
                                 side_effect=side_effect)

    def put(self, name='report.txt', data=b'synthetic report'):
        path = self.source/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def capture(self, owner=None, previous=None):
        return stage_changed_snapshot(self.source, self.destination,
                                      staging_owner() if owner is None else owner,
                                      previous, space_policy=self.policy)

    def assert_retained_failure(self, code, operation):
        original = {str(p.relative_to(self.source)):p.read_bytes()
                    for p in self.source.rglob('*') if p.is_file()}
        owner = staging_owner()
        with self.assertRaisesRegex(BackupError, code) as caught:
            operation(owner)
        self.assertEqual(owner.state, Ownership.ERROR)
        self.assertNotIn('PRIVATE-SOURCE-NAME', str(caught.exception))
        self.assertEqual({str(p.relative_to(self.source)):p.read_bytes()
                          for p in self.source.rglob('*') if p.is_file()}, original)
        return caught.exception

    def test_policy_is_fixed_to_explicit_8gib_and_legacy_selection_remains_unchanged(self):
        self.assertIsNone(approved_capture_policy(LEGACY_SIZE, LEGACY_POLICY))
        self.assertEqual(approved_capture_policy(EXPANDED_SIZE, CAPACITY_POLICY), self.policy)
        for size, name in ((LEGACY_SIZE, CAPACITY_POLICY), (EXPANDED_SIZE, LEGACY_POLICY),
                           (4*GIB, CAPACITY_POLICY), (EXPANDED_SIZE, 'unknown'),
                           (LEGACY_SIZE, True), (EXPANDED_SIZE, []), (EXPANDED_SIZE, None)):
            with self.subTest(size=size, name=name), self.assertRaises(BackupError):
                approved_capture_policy(size, name)
        for size in (False, True, 0, -1, LEGACY_SIZE, 4*GIB, 16*GIB,
                     EXPANDED_SIZE-1, float(EXPANDED_SIZE), str(EXPANDED_SIZE)):
            with self.subTest(size=size), self.assertRaises(BackupError):
                CaptureSpacePolicy(size)
        with self.assertRaises(FrozenInstanceError):self.policy.image_size_bytes = LEGACY_SIZE

    def test_capture_budget_reserves_recovery_os_and_allocation_overhead_not_migration(self):
        files = (item('folder/a', b'a'), item('folder/b', b'b' * (UNIT+1)), item('empty', b''))
        with self.statvfs(), self.policy.admit(self.destination, files) as guard:
            rounded_payload = UNIT + 2*UNIT
            rounded_manifest = ((guard.manifest_bytes+UNIT-1)//UNIT)*UNIT
            slack = (((rounded_payload+99)//100 + UNIT-1)//UNIT)*UNIT
            expected_overhead = CONTROL_RESERVE + rounded_manifest + 2*UNIT*(3+1+3) + slack
            self.assertEqual(guard.remaining, rounded_payload)
            self.assertEqual(guard.overhead, expected_overhead)
            self.assertEqual(guard.check(), rounded_payload+expected_overhead+EXPANDED_SIZE+OS_RESERVE)
            self.assertLess(guard.check(), 3*EXPANDED_SIZE+5*GIB)

    def test_exact_available_boundary_and_one_block_below(self):
        files = (item(),)
        with self.statvfs(), self.policy.admit(self.destination, files) as guard:
            threshold = guard.check()
        for available, accepted in ((threshold-1, False), (threshold, True), (threshold+UNIT, True)):
            with self.subTest(available=available), self.statvfs(filesystem(available)):
                if accepted:
                    with self.policy.admit(self.destination, files) as guard:
                        self.assertEqual(guard.check(), threshold)
                else:
                    with self.assertRaisesRegex(BackupError, 'capture_space_low'):
                        with self.policy.admit(self.destination, files):pass
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_bavail_not_reserved_bfree_controls_admission(self):
        low = filesystem(0, f_bfree=64*GIB//UNIT)
        with self.statvfs(low), self.assertRaisesRegex(BackupError, 'capture_space_low'):
            with self.policy.admit(self.destination, (item(),)):pass

    def test_inode_boundary_includes_recovery_and_directory_allowance(self):
        files = (item('folder/report'),)
        required_inodes = INODE_RESERVE+1+1+3
        for count, accepted in ((required_inodes-1, False), (required_inodes, True)):
            with self.subTest(count=count), self.statvfs(filesystem(f_favail=count)):
                if accepted:
                    with self.policy.admit(self.destination, files):pass
                else:
                    with self.assertRaisesRegex(BackupError, 'capture_space_low'):
                        with self.policy.admit(self.destination, files):pass

    def test_malformed_or_unknown_capacity_is_refused(self):
        invalid = ({'f_frsize':0}, {'f_frsize':513}, {'f_bsize':True}, {'f_blocks':0},
                   {'f_blocks':MAX_INTEGER}, {'f_bfree':-1}, {'f_bavail':'100'},
                   {'f_bavail':64*GIB//UNIT+1}, {'f_bavail':1, 'f_bfree':0},
                   {'f_files':0}, {'f_favail':100001}, {'f_favail':1.0})
        for changed in invalid:
            with self.subTest(changed=changed), self.statvfs(filesystem(**changed)), \
                    self.assertRaisesRegex(BackupError, 'capture_space_unknown'):
                with self.policy.admit(self.destination, (item(),)):pass
        with self.statvfs(SimpleNamespace()), self.assertRaisesRegex(BackupError, 'capture_space_unknown'):
            with self.policy.admit(self.destination, (item(),)):pass
        with self.statvfs(side_effect=OSError(errno.EIO, 'PRIVATE-SOURCE-NAME')), \
                self.assertRaisesRegex(BackupError, 'capture_space_unknown'):
            with self.policy.admit(self.destination, (item(),)):pass

    def test_changed_observation_geometry_or_destination_identity_is_refused(self):
        for kind in ('unit', 'total', 'directory'):
            with self.subTest(kind=kind), self.statvfs() as observation:
                with self.policy.admit(self.destination, (item(),)) as guard:
                    if kind == 'unit':observation.return_value = filesystem(unit=8192)
                    elif kind == 'total':observation.return_value = filesystem(f_blocks=128*GIB//UNIT)
                    else:
                        retained = self.root/'retained-destination'
                        self.destination.rename(retained);self.destination.mkdir(mode=0o700)
                    with self.assertRaisesRegex(BackupError, 'capture_space_unknown'):guard.check()

    def test_invalid_plans_and_large_integer_sizes_refused_before_observation(self):
        invalid = ((object(),), (item(), item()),
                   (StagedFile('oversize', EXPANDED_SIZE+1, '0'*64),),
                   (StagedFile('overflow', MAX_INTEGER, '0'*64),))
        for files in invalid:
            with self.subTest(files=files), self.statvfs() as observation, \
                    self.assertRaisesRegex(BackupError, 'invalid_capture_space_plan'):
                self.policy.admit(self.destination, files)
            observation.assert_not_called()

    def test_progress_credits_only_whole_flushed_blocks_until_file_completion(self):
        data = b'a' * (UNIT+17)
        files = (item('report', data), item('empty', b''))
        with self.statvfs(), self.policy.admit(self.destination, files) as guard:
            self.assertEqual(guard.remaining, 2*UNIT)
            guard.begin_file('report', len(data))
            guard.written('report', UNIT-1);self.assertEqual(guard.remaining, 2*UNIT)
            guard.written('report', 1);self.assertEqual(guard.remaining, UNIT)
            guard.written('report', 17);self.assertEqual(guard.remaining, UNIT)
            guard.finish_file('report', len(data), files[0].sha256);self.assertEqual(guard.remaining, 0)
            with self.assertRaisesRegex(BackupError, 'invalid_capture_space_plan'):guard.publication_check()
            guard.begin_file('empty', 0);guard.finish_file('empty', 0, files[1].sha256)
            guard.publication_check()

    def test_invalid_progress_or_changed_file_is_refused(self):
        for operation in ('unbegun', 'wrong-size', 'duplicate', 'bad-count', 'overflow', 'hash'):
            with self.subTest(operation=operation), self.statvfs(), \
                    self.policy.admit(self.destination, (item('report', b'x'),)) as guard:
                with self.assertRaises(BackupError):
                    if operation == 'unbegun':guard.written('report', 1)
                    elif operation == 'wrong-size':guard.begin_file('report', 2)
                    else:
                        guard.begin_file('report', 1)
                        if operation == 'duplicate':guard.begin_file('report', 1)
                        elif operation == 'bad-count':guard.written('report', True)
                        elif operation == 'overflow':guard.written('report', 2)
                        else:
                            guard.written('report', 1);guard.finish_file('report', 1, '0'*64)

    def test_progress_sizes_reject_numeric_aliases_and_other_nonintegers(self):
        expected = item('report', b'x')
        for bad in (True, 1.0, '1', None):
            with self.subTest(size=bad), self.statvfs():
                with self.policy.admit(self.destination, (expected,)) as guard:
                    with self.assertRaisesRegex(BackupError, 'source_changed_during_staging'):
                        guard.begin_file('report', bad)
                with self.policy.admit(self.destination, (expected,)) as guard:
                    guard.begin_file('report', 1);guard.written('report', 1)
                    with self.assertRaisesRegex(BackupError, 'source_changed_during_staging'):
                        guard.finish_file('report', bad, expected.sha256)

    def test_only_changed_files_are_planned_and_retained_evidence_is_not_charged_twice(self):
        self.put('unchanged', b'unchanged data' * 1000)
        changed = self.put('changed', b'old')
        first, previous = stage_changed_snapshot(self.source, self.destination, staging_owner())
        orphan = self.destination/'retained.incomplete';orphan.mkdir();(orphan/'evidence').write_bytes(b'keep')
        changed.write_bytes(b'new')
        planned = []
        original = CaptureSpacePolicy.admit
        def record_plan(policy, destination, files):
            planned.append(tuple(files));return original(policy, destination, files)
        with self.statvfs(), mock.patch.object(CaptureSpacePolicy, 'admit', autospec=True, side_effect=record_plan):
            second, current = self.capture(previous=previous)
        self.assertEqual([i.relative for i in planned[0]], ['changed'])
        self.assertEqual(second.files, planned[0]);self.assertEqual(len(current), 2)
        self.assertEqual((first.root/'files/changed').read_bytes(), b'old')
        self.assertEqual((orphan/'evidence').read_bytes(), b'keep')
        with self.statvfs(), self.policy.admit(self.destination, planned[0]) as guard:required = guard.check()
        with self.statvfs(filesystem(required)), self.policy.admit(self.destination, planned[0]) as guard:
            self.assertEqual(guard.check(), required)

    def test_low_space_before_capture_keeps_existing_data_and_publishes_nothing(self):
        self.put('PRIVATE-SOURCE-NAME.txt', b'original')
        retained = self.destination/'retained.incomplete';retained.mkdir();(retained/'evidence').write_bytes(b'keep')
        with self.statvfs(filesystem(0)):
            self.assert_retained_failure('capture_space_low', lambda owner:self.capture(owner))
        self.assertEqual(list(self.destination.iterdir()), [retained])
        self.assertEqual((retained/'evidence').read_bytes(), b'keep')

    def test_unchanged_capture_still_checks_operating_reserve_without_new_batch(self):
        self.put();first, previous = stage_changed_snapshot(self.source, self.destination, staging_owner())
        with self.statvfs():
            batch, current = self.capture(previous=previous)
        self.assertIsNone(batch);self.assertEqual(current, previous)
        with self.statvfs(filesystem(0)):
            self.assert_retained_failure('capture_space_low', lambda owner:self.capture(owner, previous))
        self.assertEqual(list(self.destination.glob('*.ready')), [first.root])

    def test_space_consumed_between_chunks_retains_incomplete_payload(self):
        self.put(data=b'x' * (staging.CHUNK_SIZE+17))
        original = capture_space.CaptureSpaceAdmission.written
        low = False
        def consume_after_flush(guard, relative, count):
            nonlocal low
            low = True
            return original(guard, relative, count)
        with self.statvfs(side_effect=lambda fd:filesystem(0 if low else 48*GIB)), \
                mock.patch.object(capture_space.CaptureSpaceAdmission, 'written', autospec=True,
                                  side_effect=consume_after_flush):
            self.assert_retained_failure('capture_space_low', lambda owner:self.capture(owner))
        partial = list(self.destination.glob('*.incomplete'))
        self.assertEqual(len(partial), 1);self.assertFalse(list(self.destination.glob('*.ready')))
        self.assertEqual((partial[0]/'files/report.txt').stat().st_size, staging.CHUNK_SIZE)

    def test_enospc_during_write_fsync_or_manifest_preserves_original(self):
        for number, failure in enumerate(('write', 'fsync', 'manifest')):
            destination = self.root/('failure-'+str(number));destination.mkdir(mode=0o700)
            self.destination = destination
            self.put(data=b'retain original payload')
            open_path = Path.open
            class FailingOutput:
                def __init__(self, output):self.output = output
                def __enter__(self):self.output.__enter__();return self
                def __exit__(self, *args):return self.output.__exit__(*args)
                def write(self, block):raise OSError(errno.ENOSPC, 'PRIVATE-SOURCE-NAME')
                def __getattr__(self, name):return getattr(self.output, name)
            def fail_open(path, mode='r', *args, **kwargs):
                if failure == 'manifest' and path.name == 'manifest.json' and mode == 'x':
                    raise OSError(errno.ENOSPC, 'PRIVATE-SOURCE-NAME')
                output = open_path(path, mode, *args, **kwargs)
                return FailingOutput(output) if failure == 'write' and mode == 'xb' else output
            with self.subTest(failure=failure), self.statvfs(), \
                    mock.patch.object(Path, 'open', autospec=True, side_effect=fail_open):
                if failure == 'fsync':
                    with mock.patch.object(staging.os, 'fsync', side_effect=OSError(errno.ENOSPC, 'PRIVATE-SOURCE-NAME')):
                        self.assert_retained_failure('capture_space_low', lambda owner:self.capture(owner))
                else:
                    self.assert_retained_failure('capture_space_low', lambda owner:self.capture(owner))
            self.assertTrue(list(destination.glob('*.incomplete')))
            self.assertFalse(list(destination.glob('*.ready')))

    def test_short_write_aborts_before_crediting_payload_space(self):
        self.put(data=b'retain original payload')
        open_path = Path.open
        class ShortOutput:
            def __init__(self, output):self.output = output
            def __enter__(self):self.output.__enter__();return self
            def __exit__(self, *args):return self.output.__exit__(*args)
            def write(self, block):return self.output.write(block[:-1])
            def __getattr__(self, name):return getattr(self.output, name)
        def short_open(path, mode='r', *args, **kwargs):
            output = open_path(path, mode, *args, **kwargs)
            return ShortOutput(output) if mode == 'xb' else output
        with self.statvfs(), mock.patch.object(Path, 'open', autospec=True, side_effect=short_open), \
                mock.patch.object(capture_space.CaptureSpaceAdmission, 'written') as credited:
            self.assert_retained_failure('staging_io_error', lambda owner:self.capture(owner))
        credited.assert_not_called()
        partial = list(self.destination.glob('*.incomplete'))
        self.assertEqual(len(partial), 1);self.assertFalse(list(self.destination.glob('*.ready')))
        self.assertEqual((partial[0]/'files/report.txt').read_bytes(), b'retain original payloa')

    def test_space_check_before_publication_blocks_ready_and_late_failure_retains_private_ready(self):
        self.put()
        original = capture_space.CaptureSpaceAdmission.publication_check
        for trigger in (1, 2, 3):
            self.destination = self.root/('publish-'+str(trigger));self.destination.mkdir(mode=0o700)
            calls, low = 0, False
            def publication(guard):
                nonlocal calls, low
                calls += 1
                if calls == trigger:low = True
                return original(guard)
            with self.subTest(check=trigger), self.statvfs(side_effect=lambda fd:filesystem(0 if low else 48*GIB)), \
                    mock.patch.object(capture_space.CaptureSpaceAdmission, 'publication_check', autospec=True,
                                      side_effect=publication):
                self.assert_retained_failure('capture_space_low', lambda owner:self.capture(owner))
            self.assertEqual(bool(list(self.destination.glob('*.ready'))), trigger != 1)
            retained = list(self.destination.glob('*.ready')) + list(self.destination.glob('*.incomplete'))
            self.assertEqual(len(retained), 1)
            self.assertEqual((retained[0]/'files/report.txt').read_bytes(), b'synthetic report')

    def test_interruption_retains_incomplete_and_sets_ownership_error(self):
        self.put(data=b'x' * (staging.CHUNK_SIZE+17));owner = staging_owner()
        with self.statvfs(), mock.patch.object(capture_space.CaptureSpaceAdmission, 'written', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.capture(owner)
        self.assertEqual(owner.state, Ownership.ERROR)
        self.assertTrue(list(self.destination.glob('*.incomplete')))
        self.assertFalse(list(self.destination.glob('*.ready')))
        self.assertEqual((self.source/'report.txt').stat().st_size, staging.CHUNK_SIZE+17)

    def test_legacy_capture_does_not_use_new_statvfs_policy(self):
        self.put()
        with mock.patch.object(capture_space.os, 'fstatvfs', side_effect=AssertionError('legacy policy changed')):
            batch, _ = stage_changed_snapshot(self.source, self.destination, staging_owner())
        self.assertEqual((batch.root/'files/report.txt').read_bytes(), b'synthetic report')
        self.put(data=b'new legacy payload')
        with mock.patch.object(staging.os, 'fsync', side_effect=OSError(errno.ENOSPC, 'synthetic legacy full disk')):
            self.assert_retained_failure('staging_io_error', lambda owner:
                stage_changed_snapshot(self.source, self.destination, owner))
        self.assertTrue(batch.root.exists())

    def test_history_transaction_failure_retains_batch_and_prior_rows(self):
        self.put(data=b'version one')
        first, _ = stage_changed_snapshot(self.source, self.destination, staging_owner())
        with History(self.root/'history.sqlite3') as history:
            history.enqueue(first, 'fixture/backup', 100)
            jobs, events = history.jobs(), [dict(r) for r in history.connection.execute('SELECT * FROM events')]
            self.put(data=b'version two')
            with self.statvfs():second, _ = self.capture()
            with mock.patch.object(history, '_event', side_effect=sqlite3.OperationalError('synthetic disk full')):
                with self.assertRaises(sqlite3.OperationalError):history.enqueue(second, 'fixture/backup', 101)
            self.assertEqual(history.jobs(), jobs)
            self.assertEqual([dict(r) for r in history.connection.execute('SELECT * FROM events')], events)
            ReadyBatch.load(second.root)
            self.assertEqual((first.root/'files/report.txt').read_bytes(), b'version one')
            self.assertEqual((second.root/'files/report.txt').read_bytes(), b'version two')
            self.assertEqual(history.enqueue(second, 'fixture/backup', 102), 1)
            self.assertEqual(history.jobs()[:len(jobs)], jobs)
            self.assertEqual([dict(r) for r in history.connection.execute(
                'SELECT * FROM events WHERE job_id=? ORDER BY id', (jobs[0]['id'],))], events)
            self.assertEqual(history.enqueue(second, 'fixture/backup', 103), 0)
            self.assertEqual(len(history.jobs()), len(jobs)+1)


if __name__ == '__main__':
    unittest.main()
