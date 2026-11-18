"""Offline Stage3 simulations, NOT an installer or a filesystem resize tool.

Only TemporaryDirectory fixtures are accepted. Images are opaque regular files
with a generated MBR; they contain no real exFAT filesystem. Nothing mounts,
formats, opens a device, contacts a provider or accepts a live path argument.
"""
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.staging import stage_changed_snapshot, stage_snapshot
from raspi_cloud_sync_pro.storage import Ownership, StorageOwnership

MIB = 1024**2
GIB = 1024**3
REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    'capacity_mbr_fixture', REPO/'tools/acceptance/mass_storage/prepare_image.py')
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


def staging_owner():
    """Model evidence for generated directories only; never hardware permission."""
    owner = StorageOwnership()
    owner.confirm_host_access(locally_unmounted=True, loop_detached=True)
    owner.confirm_host_release(clean_eject_observed=True)
    owner.confirm_pi_access(gadget_closed=True, io_drained=True, filesystem_clean=True)
    owner.begin_staging(loop_read_only=True, mount_read_only=True)
    return owner


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as incoming:
        for chunk in iter(lambda: incoming.read(65536), b''):
            result.update(chunk)
    return result.hexdigest()


class CapacitySimulation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-capacity-simulation-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.original = self.root/'original.img'
        with self.original.open('xb') as output:
            output.write(layout.mbr(4*MIB))
            output.write(b'SYNTHETIC OPAQUE IMAGE; NOT AN EXFAT FILESYSTEM\n')
            output.truncate(4*MIB)
            output.flush()
            os.fsync(output.fileno())
        self.original.chmod(0o600)
        self.original_hash = digest(self.original)
        self.original_identity = self.identity(self.original)

    def fenced(self, path):
        self.assertEqual(path.parent.resolve(), self.root)
        self.assertFalse(path.is_symlink())
        self.assertTrue(self.root.name.startswith('raspi-capacity-simulation-'))

    def identity(self, path):
        self.fenced(path)
        info = path.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(info.st_nlink, 1)
        return info.st_dev, info.st_ino, info.st_size

    def copy_image(self, target, *, available=64*MIB, reserve=8*MIB, interrupt=False):
        """Private fixture operation: a completed clone is not export approval."""
        self.fenced(target)
        receipt = target.with_suffix('.container-verified.json')
        self.fenced(receipt)
        if target.exists() or receipt.exists():
            raise FileExistsError('fixture candidate or receipt already exists')
        before = self.identity(self.original)
        if available < before[2]+reserve:
            raise OSError(errno.ENOSPC, 'fixture reserve refuses candidate creation')
        partial = target.with_suffix('.incomplete')
        self.fenced(partial)
        with self.original.open('rb') as incoming, partial.open('xb') as output:
            while True:
                chunk = incoming.read(65536)
                if not chunk:
                    break
                output.write(chunk)
                if interrupt:
                    output.flush()
                    os.fsync(output.fileno())
                    raise InterruptedError('simulated copy interruption')
            output.flush()
            os.fsync(output.fileno())
        self.assertEqual(self.identity(self.original), before)
        self.assertEqual(digest(self.original), self.original_hash)
        self.assertEqual(digest(partial), self.original_hash)
        if receipt.exists() or receipt.is_symlink():
            raise FileExistsError('fixture receipt appeared during copy')
        # Atomic no-clobber publication. A competing candidate retains the partial.
        os.link(partial, target)
        with receipt.open('x') as output:
            json.dump({'kind':'opaque-copy-only', 'sha256':digest(target), 'size':target.stat().st_size}, output)
        partial.unlink()
        return target

    def rollback_fixture(self, expected_candidate_hash):
        """Model guard: preserve an unexpectedly changed candidate for review."""
        rollback = self.root/'original-retained.img'
        self.assertEqual(self.identity(rollback),self.original_identity)
        if self.original.exists():
            if digest(self.original) != expected_candidate_hash:
                raise RuntimeError('retain_and_reconcile_candidate_before_rollback')
            retained = self.root/'candidate-retained.img'
            self.assertFalse(retained.exists())
            os.replace(self.original,retained)
        os.replace(rollback,self.original)

    def crash_cutover(self, phase):
        candidate = self.copy_image(self.root/'candidate.img')
        rollback = self.root/'original-retained.img'
        code = """import os,pathlib,sys
r=pathlib.Path(sys.argv[1]).resolve()
assert r.name.startswith('raspi-capacity-simulation-')
assert (r/'original.img').is_file() and (r/'candidate.img').is_file()
os.replace(r/'original.img',r/'original-retained.img')
if sys.argv[2]=='after-original-rename':os._exit(73)
os.replace(r/'candidate.img',r/'original.img')
os._exit(73)
"""
        result = subprocess.run([sys.executable,'-I','-B','-c',code,str(self.root),phase],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 73)
        self.assertEqual(self.identity(rollback), self.original_identity)
        if self.original.exists():
            self.assertNotEqual(self.identity(self.original), self.original_identity)
            self.assertEqual(digest(self.original), self.original_hash)
        else:
            self.assertTrue(candidate.exists())
        self.rollback_fixture(self.original_hash)
        self.assertEqual(self.identity(self.original),self.original_identity)
        self.assertEqual(digest(self.original),self.original_hash)

    def test_4_8_16_gib_budget_includes_capture_partial_and_fixed_reserves(self):
        available = 47*GIB
        required = {size:(3*size+5)*GIB for size in (4,8,16)}
        self.assertEqual(required, {4:17*GIB,8:29*GIB,16:53*GIB})
        self.assertTrue(available >= required[4])
        self.assertTrue(available >= required[8])
        self.assertFalse(available >= required[16])

    def test_mbr_layouts_are_reused_without_creating_large_images(self):
        for size in (4,8,16):
            sector = layout.mbr(size*GIB)
            self.assertEqual(len(sector),512)
            self.assertEqual(sector[510:],b'\x55\xaa')
            self.assertEqual(sector[450],7)
            self.assertEqual(struct.unpack_from('<II',sector,454),(2048,(size*GIB-MIB)//512))

    def test_exact_copy_preserves_bytes_but_invalidates_backing_inode_identity(self):
        candidate = self.copy_image(self.root/'candidate.img')
        self.assertEqual(digest(candidate),self.original_hash)
        self.assertNotEqual(self.identity(candidate),self.original_identity)
        self.assertEqual(candidate.read_bytes()[:512],self.original.read_bytes()[:512])
        self.assertTrue(self.original.exists())

    def test_container_extension_does_not_grow_the_partition(self):
        candidate = self.copy_image(self.root/'candidate.img')
        old_sector = candidate.read_bytes()[:512]
        with candidate.open('r+b') as output:
            output.truncate(8*MIB)
        self.assertEqual(candidate.stat().st_size,8*MIB)
        self.assertEqual(candidate.read_bytes()[:512],old_sector)
        self.assertEqual(struct.unpack_from('<I',old_sector,458)[0],(4*MIB-MIB)//512)
        self.assertNotEqual(digest(candidate),json.loads(candidate.with_suffix('.container-verified.json').read_text())['sha256'])
        self.assertEqual(digest(self.original),self.original_hash)

    def test_reserve_refuses_before_creating_candidate(self):
        candidate = self.root/'candidate.img'
        with self.assertRaises(OSError):
            self.copy_image(candidate,available=12*MIB-1,reserve=8*MIB)
        self.assertFalse(candidate.exists())
        self.assertFalse(candidate.with_suffix('.incomplete').exists())
        self.assertEqual(self.identity(self.original),self.original_identity)

    def test_existing_candidate_or_receipt_is_never_overwritten(self):
        for name, suffix in (('candidate', '.img'), ('receipt', '.container-verified.json')):
            with self.subTest(existing=name):
                target = self.root/(name+'.img')
                existing = target.with_suffix(suffix)
                existing.write_bytes(b'PREEXISTING FIXTURE ARTIFACT')
                identity = self.identity(existing)
                with self.assertRaises(FileExistsError):
                    self.copy_image(target)
                self.assertEqual(self.identity(existing),identity)
                self.assertEqual(existing.read_bytes(),b'PREEXISTING FIXTURE ARTIFACT')
                self.assertFalse(target.with_suffix('.incomplete').exists())
        self.assertEqual(self.identity(self.original),self.original_identity)

    def test_candidate_appearing_at_publication_retains_partial_and_existing_bytes(self):
        target = self.root/'candidate.img'
        real_link = os.link

        def competing_candidate(partial, destination):
            destination.write_bytes(b'COMPETING FIXTURE CANDIDATE')
            real_link(partial,destination)

        with mock.patch('os.link',side_effect=competing_candidate):
            with self.assertRaises(FileExistsError):
                self.copy_image(target)
        self.assertEqual(target.read_bytes(),b'COMPETING FIXTURE CANDIDATE')
        self.assertEqual(digest(target.with_suffix('.incomplete')),self.original_hash)
        self.assertFalse(target.with_suffix('.container-verified.json').exists())
        self.assertEqual(self.identity(self.original),self.original_identity)

    def test_interrupted_copy_artifacts_reconcile_in_fresh_process(self):
        candidate = self.root/'candidate.img'
        with self.assertRaises(InterruptedError):
            self.copy_image(candidate,interrupt=True)
        partial = candidate.with_suffix('.incomplete')
        self.assertTrue(partial.exists())
        self.assertLess(partial.stat().st_size,self.original.stat().st_size)
        self.assertFalse(candidate.exists())
        self.assertFalse(candidate.with_suffix('.container-verified.json').exists())
        code = """import pathlib,sys
r=pathlib.Path(sys.argv[1]).resolve();assert r.name.startswith('raspi-capacity-simulation-')
assert (r/'candidate.incomplete').exists()
assert not (r/'candidate.img').exists()
assert not (r/'candidate.container-verified.json').exists()
print('PARTIAL_REQUIRES_REVIEW_NO_EXPORT')
"""
        child = subprocess.run([sys.executable,'-I','-B','-c',code,str(self.root)],capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0)
        self.assertEqual(child.stdout.strip(),'PARTIAL_REQUIRES_REVIEW_NO_EXPORT')
        self.assertEqual(digest(self.original),self.original_hash)

    def test_crash_before_candidate_publish_restores_original_inode(self):
        self.crash_cutover('after-original-rename')

    def test_crash_after_candidate_publish_reconciles_actual_files_and_restores_inode(self):
        self.crash_cutover('after-candidate-publish')

    def test_new_candidate_writes_block_blind_rollback_and_retain_both_images(self):
        candidate = self.copy_image(self.root/'candidate.img')
        rollback = self.root/'original-retained.img'
        os.replace(self.original,rollback)
        os.replace(candidate,self.original)
        with self.original.open('r+b') as output:
            output.seek(MIB)
            output.write(b'SYNTHETIC NEW WRITE AFTER CUTOVER')
            output.flush()
            os.fsync(output.fileno())
        candidate_changed = digest(self.original) != self.original_hash
        self.assertTrue(candidate_changed)
        self.assertTrue(rollback.exists())
        self.assertEqual(self.identity(rollback),self.original_identity)
        self.assertEqual(digest(rollback),self.original_hash)
        with self.assertRaisesRegex(RuntimeError,'retain_and_reconcile'):
            self.rollback_fixture(self.original_hash)
        # The guard retains both images rather than discarding the new data.
        self.assertTrue(rollback.exists())
        self.assertNotEqual(self.identity(self.original),self.original_identity)

    def test_existing_staging_and_history_preserve_versions_and_suppress_duplicate_jobs(self):
        source = self.root/'old-files';source.mkdir()
        (source/'synthetic-report.txt').write_bytes(b'SYNTHETIC VERSION ONE')
        stage = self.root/'staging'
        first, inventory = stage_changed_snapshot(source,stage,staging_owner())
        with History(self.root/'history.sqlite3') as history:
            self.assertEqual(history.enqueue(first,'google_drive_rclone/fixture:Backup',100),1)
            before_jobs = history.jobs()
            before_events = [dict(r) for r in history.connection.execute('SELECT * FROM events ORDER BY id')]
            candidate_files = self.root/'candidate-files';shutil.copytree(source,candidate_files)
            same, _ = stage_changed_snapshot(candidate_files,stage,staging_owner(),inventory)
            self.assertIsNone(same)
            restaged = stage_snapshot(candidate_files,stage,staging_owner())
            self.assertEqual(history.enqueue(restaged,'google_drive_rclone/fixture:Backup',101),0)
            self.assertEqual(history.jobs(),before_jobs)
            self.assertEqual([dict(r) for r in history.connection.execute('SELECT * FROM events ORDER BY id')],before_events)
            (candidate_files/'synthetic-report.txt').write_bytes(b'SYNTHETIC VERSION TWO')
            changed, _ = stage_changed_snapshot(candidate_files,stage,staging_owner(),inventory)
            self.assertEqual(history.enqueue(changed,'google_drive_rclone/fixture:Backup',102),1)
            self.assertEqual(history.jobs()[:1],before_jobs)
            self.assertEqual([dict(r) for r in history.connection.execute(
                'SELECT * FROM events WHERE job_id=? ORDER BY id',(before_jobs[0]['id'],))],before_events)
            self.assertEqual((first.root/'files/synthetic-report.txt').read_bytes(),b'SYNTHETIC VERSION ONE')
            self.assertEqual((changed.root/'files/synthetic-report.txt').read_bytes(),b'SYNTHETIC VERSION TWO')

    def test_low_space_keeps_original_and_incomplete_batch_without_ready_publication(self):
        source = self.root/'synthetic-files';source.mkdir();(source/'report.txt').write_bytes(b'SYNTHETIC REPORT')
        target = self.root/'staging';owner = staging_owner()
        with mock.patch('raspi_cloud_sync_pro.staging.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaisesRegex(BackupError,'staging_space_low'):
                stage_snapshot(source,target,owner,reserve_bytes=4*GIB)
        self.assertEqual(owner.state,Ownership.ERROR)
        self.assertTrue(list(target.glob('*.incomplete')))
        self.assertFalse(list(target.glob('*.ready')))
        self.assertEqual((source/'report.txt').read_bytes(),b'SYNTHETIC REPORT')

    def test_enospc_during_fsync_preserves_original_and_unpublished_payload(self):
        source = self.root/'synthetic-files';source.mkdir();(source/'report.txt').write_bytes(b'SYNTHETIC REPORT')
        target = self.root/'staging';owner = staging_owner()
        with mock.patch('raspi_cloud_sync_pro.staging.os.fsync',side_effect=OSError(errno.ENOSPC,'simulated ENOSPC')):
            with self.assertRaisesRegex(BackupError,'staging_io_error'):
                stage_snapshot(source,target,owner)
        self.assertEqual(owner.state,Ownership.ERROR)
        self.assertFalse(list(target.glob('*.ready')))
        partial = list(target.glob('*.incomplete'))
        self.assertEqual(len(partial),1)
        self.assertEqual((partial[0]/'files/report.txt').read_bytes(),b'SYNTHETIC REPORT')
        self.assertEqual((source/'report.txt').read_bytes(),b'SYNTHETIC REPORT')

    def test_image_fixture_refuses_symlink_or_outside_temporary_root(self):
        link = self.root/'link.img';link.symlink_to(self.original)
        with self.assertRaises(AssertionError):
            self.identity(link)
        with self.assertRaises(AssertionError):
            self.copy_image(self.root/'nested/candidate.img')


if __name__ == '__main__':
    unittest.main()
