"""Source-only enrollment acceptance: sparse header files and simulated USB/loops.

No formatter, mount command, device, cloud operation or live service is invoked.
The simulated mounted trees are ordinary TemporaryDirectory descendants.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController, GadgetObservation
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.maintenance import MaintenanceHold
from raspi_cloud_sync_pro.mass_storage_image import EXPANDED_SIZE, LEGACY_SIZE, PARTITION_OFFSET
from test_mass_storage_capacity import approval, headers


def identity(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino, info.st_size


def private_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True))
    path.chmod(0o600)


class SimulatedExposureCrash(BaseException):
    pass


class FixtureBackend:
    """A shared gadget, with inode-bound ordinary directories as mounted volumes."""

    def __init__(self, fixture, image):
        self.fixture, self.image = fixture, Path(image)
        self.image_identity = None
        self.settings = SimpleNamespace(state_directory=fixture.state)
        self.io = SimpleNamespace(query=self.query,enrollment_loop_evidence=self.empty_loop_evidence)

    def empty_loop_evidence(self, rows):
        if rows:
            raise BackupError('synthetic_unclassified_loop')
        return []  # This boundary models an independently empty kernel loop inventory.

    def _pin_image(self):
        actual = identity(self.image)
        if self.image_identity is not None and self.image_identity != actual:
            raise BackupError('image_identity_changed')
        self.image_identity = actual

    @property
    def mountpoint(self):
        return self.fixture.trees[identity(self.image)[1]]

    def query(self, args):
        hw = self.fixture.hw
        rows = [] if hw.loop is None else [{'name': hw.loop, 'back-file': str(hw.loop_image), 'ro': True}]
        return SimpleNamespace(returncode=0, stdout=json.dumps({'loopdevices': rows}), stderr='')

    def event(self, name):
        self.fixture.events.append(name)
        if self.fixture.failure == name:
            raise BackupError('simulated_' + name)

    def observe(self):
        self._pin_image()
        hw = self.fixture.hw
        return GadgetObservation(hw.bound, hw.backing, hw.configured,
                                 (hw.loop,) if hw.loop else (),
                                 (str(hw.mount_image),) if hw.mounted else (),
                                 hw.power_ok, hw.profile_ok)

    def attach_gadget(self):
        self.event('attach')
        hw = self.fixture.hw
        if hw.loop or hw.mounted:
            raise AssertionError('host and Pi filesystem overlap')
        hw.bound, hw.backing, hw.configured = True, str(self.image), True
        if getattr(self.fixture, 'fail_after_attach', False):
            raise SimulatedExposureCrash()

    def detach_gadget(self):
        self.event('detach')
        hw = self.fixture.hw
        if hw.backing:
            raise AssertionError('detached before host release')
        hw.bound, hw.configured = False, False

    def attach_loop_read_only(self):
        self.event('loop_attach')
        hw = self.fixture.hw
        if hw.bound or hw.backing or hw.loop:
            raise AssertionError('unsafe or competing image access')
        hw.loop, hw.loop_image = '/dev/loop42', self.image
        return hw.loop

    def verify_loop_read_only(self, loop):
        self.event('verify_loop')
        hw = self.fixture.hw
        if loop != hw.loop or hw.loop_image != self.image:
            raise BackupError('loop_not_read_only')

    def check_filesystem(self, loop):
        self.event('fsck')
        if not self.fixture.clean.get(identity(self.image)[1], True):
            raise BackupError('dirty_filesystem')

    def mount_read_only(self, loop):
        self.event('mount')
        hw = self.fixture.hw
        if hw.bound or hw.backing or loop != hw.loop:
            raise AssertionError('unsafe mount')
        hw.mounted, hw.mount_image = True, self.image

    def verify_mount_read_only(self, loop):
        self.event('verify_mount')
        hw = self.fixture.hw
        if not hw.mounted or hw.mount_image != self.image or loop != hw.loop:
            raise BackupError('mount_not_read_only')

    def unmount(self):
        self.event('unmount')
        self.fixture.hw.mounted = False
        self.fixture.hw.mount_image = None

    def detach_loop(self, loop):
        self.event('loop_detach')
        hw = self.fixture.hw
        if hw.mounted or hw.loop != loop:
            raise AssertionError('unsafe loop detach')
        hw.loop, hw.loop_image = None, None


class EnrollmentFixture:
    def __init__(self, root):
        self.root = root
        for name in ('images', 'state', 'private', 'config', 'old-tree', 'new-tree'):
            (root / name).mkdir(mode=0o700)
        self.image, self.candidate = root/'images/disk.img', root/'images/candidate.img'
        self.state, self.private = root/'state', root/'private'
        self.config, self.approval = root/'config/appliance.json', root/'config/APPROVED'
        self.trees, self.clean, self.events, self.failure = {}, {}, [], None
        self.hw = SimpleNamespace(bound=False, backing='', configured=False, loop=None,
                                  loop_image=None, mounted=False, mount_image=None,
                                  power_ok=True, profile_ok=True)
        for image, size, tree in ((self.image, LEGACY_SIZE, root/'old-tree'),
                                  (self.candidate, EXPANDED_SIZE, root/'new-tree')):
            mbr, boot = headers(size)
            with image.open('xb') as stream:
                stream.truncate(size)
                stream.write(mbr)
                stream.seek(PARTITION_OFFSET)
                stream.write(boot)
            image.chmod(0o600)
            self.trees[identity(image)[1]] = tree
            (tree/'nested').mkdir(mode=0o700)
            (tree/'empty-directory').mkdir(mode=0o700)
            (tree/'report.txt').write_bytes(b'Original synthetic QA report\n')
            (tree/'nested/unicode-\u03b1.txt').write_bytes(b'Second synthetic file\n')
            for file in tree.rglob('*.txt'):
                file.chmod(0o600)
        self.old_identity, self.new_identity = identity(self.image), identity(self.candidate)
        private_json(self.config, {'schema_version': 1, 'enabled': True, 'udc': 'test.udc',
                                  'serial': 'TEST-ENROLLMENT', 'approval_file': str(self.approval)})
        private_json(self.approval, approval(self.old_identity, 1))
        backend = self.backend(self.image)
        controller = GadgetController(backend, self.state, maintenance_enabled=True)
        with controller.session():
            controller.start()
            assert controller.poll(self.private) == 'waiting_for_host_eject'
            self.hold = MaintenanceHold(self.state, backend, enabled=True).submit()
            self.hw.backing = ''
            batch = controller.poll(self.private)
            self.retained = self.root/'retained.ready'
            batch.root.rename(self.retained)
            assert controller.complete_maintenance(self.private)
        with History(self.root/'history.sqlite3') as history:
            assert history.enqueue(replace(batch, root=self.retained), 'synthetic-backup', 1) == 2
        self.history_bytes = (self.root/'history.sqlite3').read_bytes()
        self.retained_manifest = (self.retained/'manifest.json').read_bytes()
        self.original_records = {path: path.read_bytes() for path in
                                 [self.config, self.approval, self.state/'controller.json',
                                  self.state/'capture-manifest.json', self.state/'maintenance.json',
                                  self.state/'maintenance-session.json']}
        self.events.clear()

    def backend(self, path):
        return FixtureBackend(self, path)

    def transaction(self):
        from raspi_cloud_sync_pro.image_enrollment import ImageEnrollment
        return ImageEnrollment(self.state, self.image, self.candidate, self.config,
                               self.approval, self.private, self.backend)

    def prepare(self, transaction=None):
        transaction = transaction or self.transaction()
        return transaction, transaction.prepare(self.hold, approval(self.new_identity), approved=True)

    def find_identity(self, expected):
        return [p for p in (self.root/'images').iterdir()
                if p.is_file() and identity(p) == expected]

    def assert_retained(self, case):
        case.assertEqual((self.root/'history.sqlite3').read_bytes(), self.history_bytes)
        case.assertEqual((self.retained/'manifest.json').read_bytes(), self.retained_manifest)
        case.assertTrue((self.retained/'files/report.txt').exists())


class ImageEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-enrollment-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Unit images are sparse header fixtures. Only this kernel allocation
        # measurement is modeled; actual geometry, tree hashes and records are
        # checked by production code. The unmocked negative test below proves
        # sparse images cannot receive real enrollment approval.
        self.allocation = mock.patch('raspi_cloud_sync_pro.image_enrollment._allocated',
                                     side_effect=lambda path: Path(path).stat().st_size,
                                     create=True)
        self.allocation.start()
        self.addCleanup(self.allocation.stop)

    def fixture(self, name='fixture'):
        root = self.root/name
        root.mkdir(mode=0o700)
        return EnrollmentFixture(root)

    def test_valid_commit_preserves_both_image_identities_and_backup_data(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        self.assertEqual(identity(f.image), f.old_identity)
        self.assertFalse(f.hw.bound)
        transaction.commit(txid, approved=True)
        self.assertEqual(identity(f.image), f.new_identity)
        self.assertEqual(len(f.find_identity(f.old_identity)), 1)
        self.assertFalse(f.hw.bound)
        self.assertNotIn('attach', f.events)
        config = json.loads(f.config.read_text())
        self.assertEqual(config['schema_version'], 2)
        self.assertEqual(config['capture_space_policy'], 'capacity-reserve-v1')
        manifest = json.loads((f.state/'capture-manifest.json').read_text())
        self.assertEqual(manifest['image_identity'], list(f.new_identity))
        self.assertEqual(json.loads((f.state/'controller.json').read_text())['phase'], 'enrolled_detached')
        f.assert_retained(self)

    def test_explicit_approval_and_exact_hold_are_required_before_prepare(self):
        for change in ('approval', 'hold'):
            with self.subTest(change=change):
                f = self.fixture(change)
                tx = f.transaction()
                with self.assertRaises(BackupError):
                    tx.prepare('0'*32 if change == 'hold' else f.hold,
                               approval(f.new_identity), approved=change != 'approval')
                self.assertEqual(identity(f.image), f.old_identity)
                f.assert_retained(self)

    def test_candidate_approval_capacity_and_identity_must_match(self):
        for change in ('stale_inode', 'wrong_layout', 'legacy_approval', 'wrong_capacity'):
            with self.subTest(change=change):
                f = self.fixture(change)
                candidate_approval = approval(f.new_identity)
                if change == 'stale_inode':
                    candidate_approval['image_identity'][1] += 1
                elif change == 'wrong_layout':
                    candidate_approval['image_layout']['start_sector'] = 4096
                elif change == 'legacy_approval':
                    candidate_approval = approval(f.new_identity, 1)
                else:
                    with f.candidate.open('r+b') as stream:
                        stream.truncate(4*LEGACY_SIZE)
                with self.assertRaises(BackupError):
                    f.transaction().prepare(f.hold, candidate_approval, approved=True)
                self.assertEqual(identity(f.image), f.old_identity)
                f.assert_retained(self)

    def test_candidate_content_and_empty_directory_mismatches_are_refused(self):
        for change in ('content', 'missing_directory', 'extra_file'):
            with self.subTest(change=change):
                f = self.fixture(change)
                tree = f.trees[f.new_identity[1]]
                if change == 'content':
                    (tree/'report.txt').write_bytes(b'different synthetic bytes')
                elif change == 'missing_directory':
                    (tree/'empty-directory').rmdir()
                else:
                    (tree/'extra.txt').write_bytes(b'unexpected')
                with self.assertRaises(BackupError):
                    f.prepare()
                self.assertEqual(identity(f.image), f.old_identity)
                self.assertFalse(f.hw.bound)
                f.assert_retained(self)

    def test_wrong_geometry_dirty_filesystem_and_symlink_are_refused(self):
        for change in ('geometry', 'dirty', 'symlink'):
            with self.subTest(change=change):
                f = self.fixture(change)
                if change == 'geometry':
                    with f.candidate.open('r+b') as stream:
                        stream.seek(PARTITION_OFFSET+108)
                        stream.write(b'\x0c')
                elif change == 'dirty':
                    f.clean[f.new_identity[1]] = False
                else:
                    (f.trees[f.new_identity[1]]/'alias').symlink_to('report.txt')
                with self.assertRaises(BackupError):
                    f.prepare()
                self.assertNotIn('attach', f.events)
                f.assert_retained(self)

    def test_active_host_loop_mount_or_unfinished_private_capture_refuses(self):
        for change in ('host', 'loop', 'mount', 'capture', 'power', 'profile'):
            with self.subTest(change=change):
                f = self.fixture(change)
                if change == 'host':
                    f.hw.bound, f.hw.backing, f.hw.configured = True, str(f.image), True
                elif change == 'loop':
                    f.hw.loop, f.hw.loop_image = '/dev/loop99', f.candidate
                elif change == 'mount':
                    f.hw.mounted, f.hw.mount_image = True, f.candidate
                elif change == 'capture':
                    (f.private/'incomplete').mkdir()
                elif change == 'power':
                    f.hw.power_ok = False
                else:
                    f.hw.profile_ok = False
                with self.assertRaises(BackupError):
                    f.prepare()
                self.assertNotIn('attach', f.events)
                self.assertEqual(identity(f.image), f.old_identity)
                f.assert_retained(self)

    def test_pre_export_rollback_restores_original_identity_and_records(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        transaction.commit(txid, approved=True)
        transaction.recover(txid, action='rollback', approved=True)
        self.assertEqual(identity(f.image), f.old_identity)
        self.assertEqual(len(f.find_identity(f.new_identity)), 1)
        for path, expected in f.original_records.items():
            self.assertEqual(path.read_bytes(), expected)
        self.assertFalse(f.hw.bound)
        self.assertNotIn('attach', f.events)
        f.assert_retained(self)

    def test_prepared_transaction_blocks_normal_and_cold_start(self):
        f = self.fixture()
        f.prepare()
        for cold in (False, True):
            controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=False)
            with controller.session(), self.assertRaises(BackupError):
                controller.start(cold_start=cold, staging_directory=f.private)
        self.assertFalse(f.hw.bound)
        self.assertNotIn('attach', f.events)

    def test_candidate_change_after_prepare_never_commits(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        with f.candidate.open('r+b') as stream:
            stream.seek(PARTITION_OFFSET+108)
            stream.write(b'\x0c')
        with self.assertRaises(BackupError):
            transaction.commit(txid, approved=True)
        self.assertFalse(f.hw.bound)
        f.assert_retained(self)

    def test_stale_transaction_or_missing_commit_approval_refuses(self):
        for change in ('transaction', 'approval'):
            with self.subTest(change=change):
                f = self.fixture(change)
                transaction, txid = f.prepare()
                with self.assertRaises(BackupError):
                    transaction.commit('0'*32 if change == 'transaction' else txid,
                                       approved=change != 'approval')
                self.assertEqual(identity(f.image), f.old_identity)
                f.assert_retained(self)

    def test_committed_unexported_candidate_does_not_auto_start(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        transaction.commit(txid, approved=True)
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        with controller.session(), self.assertRaises(BackupError):
            controller.start(cold_start=True, staging_directory=f.private)
        self.assertNotIn('attach', f.events)
        self.assertFalse(f.hw.bound)
        f.assert_retained(self)

    def test_process_death_at_commit_boundaries_blocks_start_then_reconciles(self):
        checkpoints = ['original_archived', 'candidate_installed'] + [
            'record:' + key for key in ('config', 'approval', 'controller', 'capture', 'maintenance', 'session')
        ] + ['committed']
        for index, point in enumerate(checkpoints):
            with self.subTest(point=point):
                f = self.fixture('crash-' + str(index))
                transaction, txid = f.prepare()
                pid = os.fork()
                if pid == 0:
                    transaction.checkpoint = lambda name: os._exit(87) if name == point else None
                    try:
                        transaction.commit(txid, approved=True)
                    except BaseException:
                        os._exit(88)
                    os._exit(89)
                _, status = os.waitpid(pid, 0)
                self.assertTrue(os.WIFEXITED(status))
                self.assertEqual(os.WEXITSTATUS(status), 87)
                # No automatic attach occurs simply because a process died.
                self.assertFalse(f.hw.bound)
                if f.image.exists():
                    controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
                    with controller.session(), self.assertRaises(BackupError):
                        controller.start(cold_start=True, staging_directory=f.private)
                reopened = f.transaction()
                reopened.recover(txid, action='commit', approved=True)
                self.assertEqual(identity(f.image), f.new_identity)
                self.assertEqual(len(f.find_identity(f.old_identity)), 1)
                self.assertFalse(f.hw.bound)
                f.assert_retained(self)

    def test_process_death_after_original_rename_can_roll_back_without_canonical(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        pid = os.fork()
        if pid == 0:
            transaction.checkpoint = lambda name: os._exit(87) if name == 'original_archived' else None
            try:
                transaction.commit(txid, approved=True)
            except BaseException:
                os._exit(88)
            os._exit(89)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.WEXITSTATUS(status), 87)
        self.assertFalse(f.image.exists())
        f.transaction().recover(txid, action='rollback', approved=True)
        self.assertEqual(identity(f.image), f.old_identity)
        self.assertEqual(len(f.find_identity(f.new_identity)), 1)
        for path, expected in f.original_records.items():
            self.assertEqual(path.read_bytes(), expected)
        f.assert_retained(self)

    def test_partial_status_write_is_preserved_and_requires_explicit_recovery(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        partial = f.state/'image-enrollment/status.json.enrollment.pending'
        partial.write_bytes(b'{"interrupted":')
        partial.chmod(0o600)
        with self.assertRaises(BackupError):
            transaction.commit(txid, approved=True)
        transaction.recover(txid, action='commit', approved=True)
        self.assertEqual(identity(f.image), f.new_identity)
        self.assertTrue(any(p.read_bytes() == b'{"interrupted":'
                            for p in (f.state/'image-enrollment').iterdir() if p.is_file()))
        self.assertFalse(f.hw.bound)
        f.assert_retained(self)

    def test_durable_export_intent_prevents_blind_rollback_even_before_attach(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        transaction.commit(txid, approved=True)
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        class SimulatedCrash(BaseException):
            pass
        def crash(name):
            if name == 'export_intent':
                raise SimulatedCrash()
        transaction.checkpoint = crash
        with controller.session(), self.assertRaises(SimulatedCrash):
            transaction.activate(controller, txid, approved=True)
        self.assertTrue((f.state/'image-enrollment/first-export.json').exists())
        self.assertFalse(f.hw.bound)
        with self.assertRaises(BackupError):
            f.transaction().recover(txid, action='rollback', approved=True)
        self.assertEqual(identity(f.image), f.new_identity)
        self.assertEqual(len(f.find_identity(f.old_identity)), 1)
        f.assert_retained(self)

    def test_export_then_rollback_refused_and_normal_controller_adopts_host(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        transaction.commit(txid, approved=True)
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        with controller.session():
            transaction.activate(controller, txid, approved=True)
        self.assertTrue(f.hw.bound)
        self.assertEqual(f.hw.backing, str(f.image))
        self.assertIn('attach', f.events)
        with self.assertRaises(BackupError):
            f.transaction().recover(txid, action='rollback', approved=True)
        backend = f.backend(f.image)
        backend._pin_image()  # The actual appliance pins before controller startup.
        restarted = GadgetController(backend, f.state, maintenance_enabled=True)
        with restarted.session():
            restarted.start(adopt_host=True)
            self.assertEqual(restarted.poll(f.private), 'waiting_for_host_eject')
        f.assert_retained(self)

    def test_activation_approval_is_required_and_failed_attach_keeps_export_evidence(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        transaction.commit(txid, approved=True)
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        with controller.session(), self.assertRaises(BackupError):
            transaction.activate(controller, txid, approved=False)
        self.assertFalse((f.state/'image-enrollment/first-export.json').exists())
        f.failure = 'attach'
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        with controller.session(), self.assertRaises(BackupError):
            transaction.activate(controller, txid, approved=True)
        self.assertTrue((f.state/'image-enrollment/first-export.json').exists())
        with self.assertRaises(BackupError):
            f.transaction().recover(txid, action='rollback', approved=True)
        f.assert_retained(self)

    def test_original_identity_replacement_after_prepare_refuses_without_deleting_evidence(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        f.image.rename(f.root/'images/retained-unexpected-original.img')
        with f.image.open('xb') as stream:
            stream.truncate(LEGACY_SIZE)
        f.image.chmod(0o600)
        with self.assertRaises(BackupError):
            transaction.commit(txid, approved=True)
        self.assertEqual(len(f.find_identity(f.old_identity)), 1)
        self.assertEqual(identity(f.candidate), f.new_identity)
        self.assertFalse(f.hw.bound)
        f.assert_retained(self)

    def test_failed_attach_after_possible_exposure_requires_verified_host_reconciliation(self):
        f = self.fixture()
        transaction, txid = f.prepare()
        transaction.commit(txid, approved=True)
        f.fail_after_attach = True
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        with controller.session(), self.assertRaises(SimulatedExposureCrash):
            transaction.activate(controller, txid, approved=True)
        self.assertTrue(f.hw.bound)
        self.assertEqual(f.hw.backing, str(f.image))
        attach_count = f.events.count('attach')
        with self.assertRaises(BackupError):
            f.transaction().recover(txid, action='rollback', approved=True)
        f.fail_after_attach = False
        f.transaction().recover(txid, action='host', approved=True)
        self.assertEqual(f.events.count('attach'), attach_count)
        self.assertEqual(json.loads((f.state/'controller.json').read_text())['phase'], 'host')
        backend = f.backend(f.image)
        backend._pin_image()  # The actual appliance pins before controller startup.
        restarted = GadgetController(backend, f.state, maintenance_enabled=True)
        with restarted.session():
            restarted.start(adopt_host=True)
        f.assert_retained(self)

    def test_low_available_space_inode_shortage_or_readonly_state_refuses_prepare(self):
        from raspi_cloud_sync_pro import image_enrollment as enrollment
        valid = {'f_flag': 0, 'f_bavail': 21*LEGACY_SIZE,
                 'f_frsize': 1, 'f_favail': 1024}
        for index, change in enumerate(({'f_bavail': 21*LEGACY_SIZE-1},
                                       {'f_favail': 1023}, {'f_flag': os.ST_RDONLY})):
            with self.subTest(change=change):
                f = self.fixture('space-' + str(index))
                stats = SimpleNamespace(**{**valid, **change})
                with mock.patch.object(enrollment.os, 'statvfs', return_value=stats):
                    with self.assertRaises(BackupError):
                        f.prepare()
                self.assertEqual(identity(f.image), f.old_identity)
                self.assertEqual(identity(f.candidate), f.new_identity)
                self.assertNotIn('attach', f.events)
                f.assert_retained(self)

    def test_unsafe_original_or_candidate_permissions_refuse_prepare(self):
        for index, target in enumerate(('original', 'candidate')):
            with self.subTest(target=target):
                f = self.fixture('mode-' + str(index))
                (f.image if target == 'original' else f.candidate).chmod(0o644)
                with self.assertRaises(BackupError):
                    f.prepare()
                self.assertFalse(f.hw.bound)
                f.assert_retained(self)

    def test_torn_record_publication_keeps_evidence_and_requires_reconciliation(self):
        from raspi_cloud_sync_pro import image_enrollment as enrollment
        f = self.fixture()
        transaction, txid = f.prepare()
        original_atomic = enrollment._atomic
        interrupted = b'{"incomplete-publication":'
        def fail_config(path, raw, *, recovery=False):
            if path == f.config:
                pending = path.with_name(path.name + '.enrollment.pending')
                with pending.open('xb') as output:
                    output.write(interrupted)
                    output.flush()
                    os.fsync(output.fileno())
                pending.chmod(0o600)
                raise OSError(28, 'simulated full filesystem during record publication')
            return original_atomic(path, raw, recovery=recovery)
        with mock.patch.object(enrollment, '_atomic', side_effect=fail_config):
            with self.assertRaises((BackupError, OSError)):
                transaction.commit(txid, approved=True)
        self.assertFalse(f.hw.bound)
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        with controller.session(), self.assertRaises(BackupError):
            controller.start(cold_start=True, staging_directory=f.private)
        f.transaction().recover(txid, action='commit', approved=True)
        self.assertEqual(identity(f.image), f.new_identity)
        self.assertTrue(any(path.read_bytes() == interrupted for path in f.config.parent.iterdir()
                            if path.is_file()))
        f.assert_retained(self)

    def test_unallocated_sparse_candidate_is_refused_without_modeling_allocation(self):
        f = self.fixture()
        self.allocation.stop()
        self.assertLess(f.candidate.stat().st_blocks*512, EXPANDED_SIZE)
        with self.assertRaises(BackupError):
            f.prepare()
        self.assertEqual(identity(f.image), f.old_identity)
        self.assertEqual(identity(f.candidate), f.new_identity)
        self.assertFalse(f.hw.bound)
        f.assert_retained(self)

    def test_native_pending_records_block_actions_and_preserve_all_evidence(self):
        pending_names = ('controller.pending', 'capture-manifest.pending',
                         'maintenance.json.pending', 'maintenance-session.json.pending')
        for phase in ('prepare', 'commit', 'activate'):
            for name in pending_names:
                with self.subTest(phase=phase, pending=name):
                    f = self.fixture(phase + '-' + name.replace('.', '-'))
                    transaction = f.transaction()
                    txid = None
                    if phase != 'prepare':
                        transaction, txid = f.prepare(transaction)
                    if phase == 'activate':
                        transaction.commit(txid, approved=True)
                    pending = f.state/name
                    evidence = b'{"interrupted-native-record":'
                    pending.write_bytes(evidence)
                    pending.chmod(0o600)
                    with self.assertRaises(BackupError):
                        if phase == 'prepare':
                            transaction.prepare(f.hold, approval(f.new_identity), approved=True)
                        elif phase == 'commit':
                            transaction.commit(txid, approved=True)
                        else:
                            controller = GadgetController(f.backend(f.image), f.state,
                                                          maintenance_enabled=True)
                            with controller.session():
                                transaction.activate(controller, txid, approved=True)
                    self.assertEqual(pending.read_bytes(), evidence)
                    self.assertFalse((f.state/'image-enrollment/first-export.json').exists())
                    self.assertFalse(f.hw.bound)
                    self.assertNotIn('attach', f.events)
                    self.assertEqual(len(f.find_identity(f.old_identity)), 1)
                    self.assertEqual(len(f.find_identity(f.new_identity)), 1)
                    f.assert_retained(self)

    def test_space_loss_after_commit_blocks_activation_before_export_intent(self):
        from raspi_cloud_sync_pro import image_enrollment as enrollment
        f = self.fixture()
        transaction, txid = f.prepare()
        transaction.commit(txid, approved=True)
        controller = GadgetController(f.backend(f.image), f.state, maintenance_enabled=True)
        stats = SimpleNamespace(f_flag=0, f_bavail=21*LEGACY_SIZE-1,
                                f_frsize=1, f_favail=1024)
        with mock.patch.object(enrollment.os, 'statvfs', return_value=stats):
            with controller.session(), self.assertRaises(BackupError):
                transaction.activate(controller, txid, approved=True)
        self.assertFalse((f.state/'image-enrollment/first-export.json').exists())
        self.assertFalse(f.hw.bound)
        self.assertNotIn('attach', f.events)
        self.assertEqual(identity(f.image), f.new_identity)
        self.assertEqual(len(f.find_identity(f.old_identity)), 1)
        f.assert_retained(self)

    def test_prepare_process_death_retains_draft_and_fresh_prepare_revalidates(self):
        from raspi_cloud_sync_pro.image_enrollment import guard_enrollment
        checkpoints = ('prepare:archive:old:config', 'prepare:intent', 'prepare:status')
        for index, point in enumerate(checkpoints):
            with self.subTest(point=point):
                f = self.fixture('draft-crash-' + str(index))
                transaction = f.transaction()
                pid = os.fork()
                if pid == 0:
                    transaction.checkpoint = lambda name: os._exit(87) if name == point else None
                    try:
                        transaction.prepare(f.hold, approval(f.new_identity), approved=True)
                    except BaseException:
                        os._exit(88)
                    os._exit(89)
                _, status = os.waitpid(pid, 0)
                self.assertTrue(os.WIFEXITED(status))
                self.assertEqual(os.WEXITSTATUS(status), 87)
                self.assertEqual(identity(f.image), f.old_identity)
                self.assertEqual(identity(f.candidate), f.new_identity)
                for path, expected in f.original_records.items():
                    self.assertEqual(path.read_bytes(), expected)
                self.assertFalse((f.state/'image-enrollment').exists())
                drafts = list(f.state.glob('image-enrollment.draft-*'))
                self.assertEqual(len(drafts), 1)
                with self.assertRaises(BackupError):
                    guard_enrollment(f.state, f.image, f.old_identity, export=True)
                self.assertFalse(f.hw.bound)
                f.assert_retained(self)
                # Retry verifies the still-active hold, both images and complete
                # trees anew. It never promotes or blindly replays the old draft.
                retried, txid = f.prepare()
                self.assertTrue((f.state/'image-enrollment').is_dir())
                self.assertTrue(drafts[0].is_dir())
                retried.commit(txid, approved=True)
                self.assertEqual(identity(f.image), f.new_identity)
                self.assertTrue(drafts[0].is_dir())
                f.assert_retained(self)
