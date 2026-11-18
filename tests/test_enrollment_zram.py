"""Enrollment/recovery with synthetic OS loops and ordinary disposable files only."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController
from test_image_enrollment import EnrollmentFixture, FixtureBackend, identity
from test_zram_loop_verification import EvidenceIO, KernelEvidence


class EnrollmentLoopIO:
    """Combine the OS evidence with the existing fixture's own simulated loop."""

    def __init__(self, fixture, backend):
        self.fixture,self.backend=fixture,backend

    def query(self, args):
        response=self.fixture.os_io.query(args)
        if self.fixture.hw.loop is None:
            return response
        value=json.loads(response.stdout)
        value['loopdevices']+=json.loads(self.backend.query(args).stdout)['loopdevices']
        return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')

    def enrollment_loop_evidence(self, rows):
        return self.fixture.os_io.enrollment_loop_evidence(rows)


class ZramEnrollmentFixture(EnrollmentFixture):
    def __init__(self, root):
        self.probe=KernelEvidence()
        self.os_io=EvidenceIO(self.probe)
        super().__init__(root)

    def backend(self, path):
        backend=FixtureBackend(self,path)
        backend.io=EnrollmentLoopIO(self,backend)
        return backend


class EnrollmentZramTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='raspi-enrollment-zram-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        # Reuse the accepted sparse header fixture; model only allocation.
        allocation=patch('raspi_cloud_sync_pro.image_enrollment._allocated',
                         side_effect=lambda path:Path(path).stat().st_size)
        allocation.start()
        self.addCleanup(allocation.stop)

    def fixture(self, name='fixture'):
        root=self.root/name;root.mkdir(mode=0o700)
        return ZramEnrollmentFixture(root)

    def change_os_identity(self, fixture, change):
        if change=='disappeared':
            fixture.os_io.rows=[];fixture.probe.configured=[]
            fixture.probe.data['/proc/swaps']='Filename Type Size Used Priority\n'
        else:
            fixture.probe.status['inode']+=1
            previous=list(fixture.probe.file_identity);previous[1]+=1
            fixture.probe.file_identity=tuple(previous)

    def crash_commit(self, fixture, point):
        transaction,txid=fixture.prepare()
        class Interrupted(BaseException):pass
        def checkpoint(name):
            if name==point:raise Interrupted()
        transaction.checkpoint=checkpoint
        with self.assertRaises(Interrupted):transaction.commit(txid,approved=True)
        self.assertFalse(fixture.hw.bound)
        return txid

    def test_valid_prepare_commit_activation_persists_proof_and_retains_original_and_history(self):
        fixture=self.fixture()
        transaction,txid=fixture.prepare()
        plan=json.loads((transaction.directory/'intent.json').read_text())
        self.assertEqual(plan['os_loops'],transaction.verifier.os_loops)
        self.assertEqual(plan['os_loops'][0]['loop'],[7,37])
        fixture.transaction().commit(txid,approved=True)
        controller=GadgetController(fixture.backend(fixture.image),fixture.state,maintenance_enabled=True)
        with controller.session():fixture.transaction().activate(controller,txid,approved=True)
        self.assertTrue(fixture.hw.bound)
        self.assertEqual(identity(fixture.image),fixture.new_identity)
        self.assertEqual(fixture.hw.backing,str(fixture.image))
        self.assertEqual(len(fixture.find_identity(fixture.old_identity)),1)
        fixture.assert_retained(self)

    def test_candidate_identity_remains_protected_when_only_original_is_being_verified(self):
        fixture=self.fixture()
        verifier=fixture.transaction().verifier
        verifier.expect((fixture.old_identity,fixture.new_identity))
        dev,inode,size=fixture.new_identity
        fixture.probe.status.update(device=dev,inode=inode)
        fixture.probe.file_identity=(dev,inode,size,0,0,0o600,1)
        with self.assertRaises(BackupError):verifier.verify(fixture.image)
        self.assertNotIn('loop_attach',fixture.events)
        self.assertFalse(fixture.hw.bound)
        fixture.assert_retained(self)

    def test_reopened_transaction_refuses_commit_after_os_loop_disappears_or_changes(self):
        for change in ('disappeared','replaced'):
            with self.subTest(change=change):
                fixture=self.fixture(change)
                _,txid=fixture.prepare()
                self.change_os_identity(fixture,change)
                with self.assertRaises(BackupError):fixture.transaction().commit(txid,approved=True)
                self.assertEqual(identity(fixture.image),fixture.old_identity)
                self.assertFalse(fixture.hw.bound)
                fixture.assert_retained(self)

    def test_reopened_transaction_refuses_activation_after_os_loop_disappears_or_changes(self):
        for change in ('disappeared','replaced'):
            with self.subTest(change=change):
                fixture=self.fixture(change)
                transaction,txid=fixture.prepare();transaction.commit(txid,approved=True)
                self.change_os_identity(fixture,change)
                controller=GadgetController(fixture.backend(fixture.image),fixture.state,maintenance_enabled=True)
                with controller.session(),self.assertRaises(BackupError):
                    fixture.transaction().activate(controller,txid,approved=True)
                self.assertFalse((transaction.directory/'first-export.json').exists())
                self.assertFalse(fixture.hw.bound)
                self.assertNotIn('attach',fixture.events)
                fixture.assert_retained(self)

    def test_interrupted_commit_reconciles_only_with_the_original_persisted_os_proof(self):
        for point in ('original_archived','candidate_installed','record:config'):
            with self.subTest(point=point):
                fixture=self.fixture(point.replace(':','-'))
                txid=self.crash_commit(fixture,point)
                fixture.transaction().recover(txid,action='commit',approved=True)
                self.assertEqual(identity(fixture.image),fixture.new_identity)
                self.assertEqual(len(fixture.find_identity(fixture.old_identity)),1)
                self.assertFalse(fixture.hw.bound)
                fixture.assert_retained(self)

    def test_interrupted_recovery_refuses_a_missing_or_replaced_os_loop(self):
        for action in ('commit','rollback'):
            for change in ('disappeared','replaced'):
                with self.subTest(action=action,change=change):
                    fixture=self.fixture(action+'-'+change)
                    txid=self.crash_commit(fixture,'candidate_installed')
                    self.change_os_identity(fixture,change)
                    with self.assertRaises(BackupError):
                        fixture.transaction().recover(txid,action=action,approved=True)
                    self.assertEqual(len(fixture.find_identity(fixture.old_identity)),1)
                    self.assertEqual(len(fixture.find_identity(fixture.new_identity)),1)
                    self.assertFalse(fixture.hw.bound)
                    fixture.assert_retained(self)

    def test_post_export_host_recovery_rejects_changed_proof_without_replaying_usb(self):
        for change in ('disappeared','replaced'):
            with self.subTest(change=change):
                fixture=self.fixture(change)
                transaction,txid=fixture.prepare();transaction.commit(txid,approved=True)
                controller=GadgetController(fixture.backend(fixture.image),fixture.state,maintenance_enabled=True)
                with controller.session():transaction.activate(controller,txid,approved=True)
                events=list(fixture.events)
                journal=(fixture.state/'controller.json').read_bytes()
                self.change_os_identity(fixture,change)
                with self.assertRaises(BackupError):fixture.transaction().recover(txid,action='host',approved=True)
                self.assertTrue(fixture.hw.bound)
                self.assertEqual(fixture.events,events)
                self.assertEqual((fixture.state/'controller.json').read_bytes(),journal)
                self.assertEqual(len(fixture.find_identity(fixture.old_identity)),1)
                fixture.assert_retained(self)


if __name__=='__main__':unittest.main()
