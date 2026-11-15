"""Offline maintenance tests: generated sparse images and simulated USB only."""

from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.maintenance import MaintenanceHold
from raspi_cloud_sync_pro.mass_storage_image import (
    EXPANDED_SIZE, LEGACY_SIZE, image_layout, validate_image_approval,
)
from test_gadget_controller import SimulatedGadget


class MaintenanceHoldTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-maintenance-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, *, enabled=True, size=LEGACY_SIZE, name='fixture'):
        root = self.root/name; root.mkdir(mode=0o700)
        backend = SimulatedGadget(root)
        with backend.image.open('r+b') as output:
            output.truncate(size)  # Sparse container only, not a filesystem.
        info = backend.image.stat()
        backend.image_identity = info.st_dev, info.st_ino, info.st_size
        controller = GadgetController(backend, root/'state', maintenance_enabled=enabled)
        staging = root/'private'; staging.mkdir(mode=0o700)
        return root, backend, controller, staging

    def arm(self, controller, staging):
        controller.start()
        self.assertEqual(controller.poll(staging), 'waiting_for_host_eject')

    @staticmethod
    def submit(root, backend, *, enabled=True):
        return MaintenanceHold(root/'state', backend, enabled=enabled).submit()

    @staticmethod
    def record(root):
        return json.loads((root/'state/maintenance.json').read_text())

    def capture(self, root, backend, controller, staging):
        request = self.submit(root, backend)
        backend.events.clear(); backend.eject()
        batch = controller.poll(staging)
        self.assertNotIn('attach', backend.events)
        self.assertFalse(backend.bound); self.assertEqual(backend.backing, '')
        self.assertFalse(backend.mounted); self.assertIsNone(backend.loop)
        self.assertEqual(self.record(root)['phase'], 'captured')
        self.assertEqual((batch.root/'files/original.txt').read_bytes(), b'retain original')
        return request, batch

    def activate(self, root, backend, controller, staging):
        request, batch = self.capture(root, backend, controller, staging)
        retained = root/'retained.ready'; batch.root.rename(retained)
        self.assertTrue(controller.complete_maintenance(staging))
        self.assertEqual(self.record(root)['phase'], 'active')
        return request, replace(batch, root=retained)

    def assert_restart_blocked(self, root, backend, staging, *, enabled=False):
        before = (root/'state/maintenance.json').read_bytes() if (root/'state/maintenance.json').exists() else None
        restarted = GadgetController(backend, root/'state', maintenance_enabled=enabled)
        with restarted.session(), mock.patch.object(backend, 'observe') as observe, self.assertRaises(BackupError):
            restarted.start(cold_start=True, staging_directory=staging)
        observe.assert_not_called()
        if before is not None:
            self.assertEqual((root/'state/maintenance.json').read_bytes(), before)

    def test_disabled_default_keeps_normal_capture_and_automatic_return(self):
        root, backend, controller, staging = self.fixture(enabled=False)
        with controller.session():
            self.arm(controller, staging)
            with self.assertRaises(BackupError):self.submit(root, backend, enabled=False)
            backend.events.clear(); backend.eject(); batch = controller.poll(staging)
            self.assertIn('attach', backend.events)
            self.assertTrue(backend.bound)
            self.assertEqual((batch.root/'files/original.txt').read_bytes(), b'retain original')
        self.assertFalse((root/'state/maintenance.json').exists())
        self.assertFalse((root/'state/maintenance-session.json').exists())

    def test_valid_request_waits_for_host_without_any_usb_operation(self):
        root, backend, controller, staging = self.fixture()
        with controller.session():
            self.arm(controller, staging); request = self.submit(root, backend)
            backend.events.clear()
            for _ in range(3):
                self.assertEqual(controller.poll(staging), 'maintenance_pending')
                self.assertFalse(controller.complete_maintenance(staging))
            self.assertEqual(backend.events, [])
            self.assertEqual(backend.backing, str(backend.image))
            self.assertEqual(self.record(root)['request_id'], request)
            self.assertEqual(self.record(root)['phase'], 'pending')

    def test_capture_handoff_precedes_active_hold_and_explicit_release(self):
        for size in (LEGACY_SIZE, EXPANDED_SIZE):
            with self.subTest(size=size):
                root, backend, controller, staging = self.fixture(size=size, name=str(size))
                identity = backend.image_identity
                contract = {'schema_version':2, 'mass_storage_appliance_approved':True,
                            'image_identity':list(identity), 'image_size_bytes':size,
                            'image_layout':image_layout(size)}
                self.assertEqual(validate_image_approval(contract, identity), size)
                with controller.session():
                    self.arm(controller, staging)
                    request, batch = self.activate(root, backend, controller, staging)
                    baseline = (root/'state/capture-manifest.json').read_bytes()
                    self.assertEqual(controller.journal.data['phase'], 'maintenance_active')
                    self.assertFalse(controller.started); self.assertFalse(controller.armed)
                with History(root/'history.sqlite3') as history:
                    self.assertEqual(history.enqueue(batch, 'fixture/backup', 100), 1)
                    jobs = history.jobs()
                    events = [dict(row) for row in history.connection.execute('SELECT * FROM events')]
                    released = GadgetController(backend, root/'state', maintenance_enabled=True)
                    backend.events.clear()
                    with released.session():
                        self.assertEqual(released.release_maintenance(request, staging, approved=True), 'released_host_owned')
                    self.assertEqual(history.jobs(), jobs)
                    self.assertEqual([dict(row) for row in history.connection.execute('SELECT * FROM events')], events)
                self.assertLess(backend.events.index('fsck'), backend.events.index('attach'))
                self.assertEqual(self.record(root)['phase'], 'released')
                self.assertEqual((root/'state/capture-manifest.json').read_bytes(), baseline)
                self.assertEqual(backend.image_identity, identity)
                self.assertEqual((batch.root/'files/original.txt').read_bytes(), b'retain original')
                self.assertEqual((backend.mountpoint/'original.txt').read_bytes(), b'retain original')

    def test_private_ready_batch_must_be_handed_off_before_active(self):
        root, backend, controller, staging = self.fixture()
        with controller.session():
            self.arm(controller, staging); _, batch = self.capture(root, backend, controller, staging)
            with self.assertRaises(BackupError):controller.complete_maintenance(staging)
            self.assertTrue(batch.root.exists()); self.assertEqual(self.record(root)['phase'], 'captured')
            self.assertFalse(backend.bound); self.assertNotIn('attach', backend.events)
        self.assert_restart_blocked(root, backend, staging)

    def test_request_during_capture_is_latched_before_automatic_return(self):
        root, backend, controller, staging = self.fixture()
        with controller.session():
            self.arm(controller, staging)
            original_unmount = backend.unmount
            def request_during_unmount():
                self.submit(root, backend); original_unmount()
            backend.events.clear(); backend.eject()
            with mock.patch.object(backend, 'unmount', side_effect=request_during_unmount):
                batch = controller.poll(staging)
            self.assertEqual(self.record(root)['phase'], 'captured')
            self.assertNotIn('attach', backend.events); self.assertTrue(batch.root.exists())

    def test_duplicate_or_conflicting_request_retains_first_request(self):
        root, backend, controller, staging = self.fixture()
        with controller.session():
            self.arm(controller, staging); self.submit(root, backend)
            before = (root/'state/maintenance.json').read_bytes()
            for _ in range(2):
                with self.assertRaises(BackupError):self.submit(root, backend)
            self.assertEqual((root/'state/maintenance.json').read_bytes(), before)
            backend.events.clear()
            self.assertEqual(controller.poll(staging), 'maintenance_pending')
            self.assertEqual(backend.events, [])

    def test_stopped_controller_challenge_cannot_authorize_new_request(self):
        root, backend, controller, staging = self.fixture()
        with controller.session():self.arm(controller, staging)
        with self.assertRaises(BackupError):self.submit(root, backend)
        self.assertFalse((root/'state/maintenance.json').exists())

    def test_malformed_or_wrong_image_challenge_blocks_start_before_observation(self):
        for number,change in enumerate(({'session_id':'bad'}, {'extra':True},
                                       {'image_identity':[1,999,LEGACY_SIZE]})):
            root,backend,controller,staging=self.fixture(name=str(number))
            with controller.session():self.arm(controller,staging)
            path=root/'state/maintenance-session.json'
            path.write_text(json.dumps({**json.loads(path.read_text()),**change}));before=path.read_bytes()
            for enabled in (False,True):
                restarted=GadgetController(backend,root/'state',maintenance_enabled=enabled)
                with restarted.session(),mock.patch.object(backend,'observe') as observe,self.assertRaises(BackupError):
                    restarted.start(adopt_host=True)
                observe.assert_not_called();self.assertEqual(path.read_bytes(),before)

    def test_captured_metadata_cannot_claim_active_without_returnable_ownership(self):
        root,backend,controller,staging=self.fixture()
        with controller.session():
            self.arm(controller,staging);_,batch=self.capture(root,backend,controller,staging)
            batch.root.rename(root/'retained.ready')
            before=(root/'state/maintenance.json').read_bytes()
            controller.ownership.fail()
            with mock.patch.object(backend,'observe') as observe,self.assertRaises(BackupError):
                controller.complete_maintenance(staging)
            observe.assert_not_called();self.assertEqual((root/'state/maintenance.json').read_bytes(),before)
            self.assertEqual(controller.journal.data['phase'],'maintenance_captured')

    def test_concurrent_admission_lock_refuses_without_mutation(self):
        root, backend, controller, staging = self.fixture()
        with controller.session():
            self.arm(controller, staging)
            owner = MaintenanceHold(root/'state', backend, enabled=True)
            with owner.locked(), self.assertRaises(BackupError):self.submit(root, backend)
            self.assertFalse((root/'state/maintenance.json').exists())

    def test_stale_session_or_wrong_identity_blocks_before_host_observation(self):
        for change in ({'session_id':'0'*32}, {'image_identity':[1,999,LEGACY_SIZE]}):
            root, backend, controller, staging = self.fixture(name=str(len(list(self.root.iterdir()))))
            with controller.session():
                self.arm(controller, staging); self.submit(root, backend)
                value = {**self.record(root), **change}
                path = root/'state/maintenance.json'; path.write_text(json.dumps(value)); before = path.read_bytes()
                with mock.patch.object(backend, 'observe') as observe, self.assertRaises(BackupError):controller.poll(staging)
                observe.assert_not_called(); self.assertEqual(path.read_bytes(), before)

    def test_malformed_requests_never_authorize_capture(self):
        changes = ({'extra':True}, {'schema_version':True}, {'phase':'unknown'},
                   {'request_id':'bad'}, {'capture_sha256':'0'*64})
        for number, change in enumerate(changes):
            with self.subTest(change=change):
                root, backend, controller, staging = self.fixture(name=str(number))
                with controller.session():
                    self.arm(controller, staging); self.submit(root, backend)
                    path = root/'state/maintenance.json'; path.write_text(json.dumps({**self.record(root),**change}))
                    with mock.patch.object(backend, 'observe') as observe, self.assertRaises(BackupError):controller.poll(staging)
                    observe.assert_not_called()

    def test_untrusted_permissions_and_links_are_refused(self):
        for number, kind in enumerate(('mode','hardlink','symlink','oversize')):
            root, backend, controller, staging = self.fixture(name=str(number))
            with controller.session():
                self.arm(controller, staging); self.submit(root, backend)
                path = root/'state/maintenance.json'
                if kind == 'mode':path.chmod(0o644)
                elif kind == 'hardlink':os.link(path,root/'retained-hardlink')
                elif kind == 'symlink':
                    retained = root/'retained-record';path.rename(retained);path.symlink_to(retained)
                else:path.write_bytes(b' ' * 4097)
                with mock.patch.object(backend, 'observe') as observe, self.assertRaises((BackupError,OSError)):
                    controller.poll(staging)
                observe.assert_not_called()

    def test_disconnect_cannot_substitute_for_host_eject(self):
        root, backend, controller, staging = self.fixture()
        with controller.session():
            self.arm(controller, staging);self.submit(root,backend);backend.events.clear()
            backend.configured=False
            self.assertEqual(controller.poll(staging),'waiting_for_host')
            backend.eject();backend.configured=True
            with self.assertRaises(BackupError):controller.poll(staging)
            self.assertEqual(backend.events,[]);self.assertEqual(self.record(root)['phase'],'pending')

    def test_pending_and_active_restart_cannot_bypass_hold_when_admission_disabled(self):
        for phase in ('pending','active'):
            root, backend, controller, staging = self.fixture(name=phase)
            with controller.session():
                self.arm(controller,staging)
                if phase == 'pending':self.submit(root,backend)
                else:self.activate(root,backend,controller,staging)
            self.assert_restart_blocked(root,backend,staging,enabled=False)
            self.assert_restart_blocked(root,backend,staging,enabled=True)

    def test_capture_faults_never_reattach_and_preserve_originals(self):
        for name in ('detach','loop_attach','fsck','mount','verify_mount','unmount','loop_detach'):
            root,backend,controller,staging=self.fixture(name=name)
            with controller.session():
                self.arm(controller,staging);self.submit(root,backend)
                backend.failure=name;backend.events.clear();backend.eject()
                with self.assertRaises(BackupError):controller.poll(staging)
                self.assertNotIn('attach',backend.events)
                self.assertEqual((backend.mountpoint/'original.txt').read_bytes(),b'retain original')
            self.assert_restart_blocked(root,backend,staging)

    def test_failures_after_unmount_or_loop_detach_remain_interrupted(self):
        for method in ('unmount','detach_loop'):
            root,backend,controller,staging=self.fixture(name=method)
            with controller.session():
                self.arm(controller,staging);self.submit(root,backend)
                operation=getattr(backend,method)
                def post_failure(*args):
                    operation(*args);raise OSError(errno.EIO,'synthetic post-operation fault')
                backend.events.clear();backend.eject()
                with mock.patch.object(backend,method,side_effect=post_failure),self.assertRaises(BackupError):
                    controller.poll(staging)
                self.assertNotIn('attach',backend.events)
                self.assertTrue(list(staging.glob('*.ready')))
            self.assert_restart_blocked(root,backend,staging)

    def test_low_space_and_capture_manifest_failure_preserve_failed_evidence(self):
        for kind in ('staging-space','manifest'):
            root,backend,controller,staging=self.fixture(name=kind)
            with controller.session():
                self.arm(controller,staging);self.submit(root,backend);backend.events.clear();backend.eject()
                if kind == 'staging-space':
                    patch=mock.patch('raspi_cloud_sync_pro.staging.shutil.disk_usage',return_value=mock.Mock(free=0))
                else:
                    patch=mock.patch.object(controller.journal,'save_capture',side_effect=OSError(errno.ENOSPC,'synthetic full metadata disk'))
                with patch,self.assertRaises(BackupError):controller.poll(staging)
                self.assertNotIn('attach',backend.events)
                self.assertTrue(list(staging.iterdir()))
                self.assertEqual((backend.mountpoint/'original.txt').read_bytes(),b'retain original')
            self.assert_restart_blocked(root,backend,staging)

    def test_partial_maintenance_write_blocks_current_and_restarted_controller(self):
        root,backend,controller,staging=self.fixture()
        with controller.session():
            self.arm(controller,staging)
            with mock.patch('raspi_cloud_sync_pro.maintenance.os.fsync',side_effect=OSError(errno.ENOSPC,'synthetic full state disk')):
                with self.assertRaises(OSError):self.submit(root,backend)
            self.assertTrue((root/'state/maintenance.json.pending').exists())
            with mock.patch.object(backend,'observe') as observe,self.assertRaises(BackupError):controller.poll(staging)
            observe.assert_not_called()
        self.assert_restart_blocked(root,backend,staging)

    def test_release_requires_exact_id_explicit_approval_and_active_hold(self):
        root,backend,controller,staging=self.fixture()
        with controller.session():
            self.arm(controller,staging);request,_=self.activate(root,backend,controller,staging)
        release=GadgetController(backend,root/'state',maintenance_enabled=True)
        with release.session():
            for request_id,approved in ((request,False),(request,1),('0'*32,True)):
                with self.subTest(approved=approved,request=request_id),mock.patch.object(backend,'observe') as observe,self.assertRaises(BackupError):
                    release.release_maintenance(request_id,staging,approved=approved)
                observe.assert_not_called()
        self.assertEqual(self.record(root)['phase'],'active')

    def test_release_refuses_changed_inventory_or_image(self):
        for kind in ('inventory','identity'):
            root,backend,controller,staging=self.fixture(name=kind)
            with controller.session():
                self.arm(controller,staging);request,batch=self.activate(root,backend,controller,staging)
            if kind=='inventory':
                path=root/'state/capture-manifest.json';value=json.loads(path.read_text());value['files']=[];path.write_text(json.dumps(value))
            else:backend.image_identity=(backend.image_identity[0],backend.image_identity[1]+1,LEGACY_SIZE)
            released=GadgetController(backend,root/'state',maintenance_enabled=True)
            with released.session(),mock.patch.object(backend,'observe') as observe,self.assertRaises(BackupError):
                released.release_maintenance(request,staging,approved=True)
            observe.assert_not_called();self.assertTrue(batch.root.exists())

    def test_unsafe_release_never_forces_host_or_local_resources(self):
        for attribute,value in (('bound',True),('backing','unexpected'),('loop','/dev/loop99'),('mounted',True),('profile_ok',False),('power_ok',False)):
            root,backend,controller,staging=self.fixture(name=attribute)
            with controller.session():
                self.arm(controller,staging);request,_=self.activate(root,backend,controller,staging)
            setattr(backend,attribute,value);backend.events.clear()
            released=GadgetController(backend,root/'state',maintenance_enabled=True)
            with released.session(),self.assertRaises(BackupError):released.release_maintenance(request,staging,approved=True)
            self.assertEqual(backend.events,[])

    def test_failed_release_fsck_cannot_be_replayed_by_restart(self):
        root,backend,controller,staging=self.fixture()
        with controller.session():
            self.arm(controller,staging);request,_=self.activate(root,backend,controller,staging)
        backend.clean=False;backend.events.clear()
        release=GadgetController(backend,root/'state',maintenance_enabled=True)
        with release.session(),self.assertRaises(BackupError):release.release_maintenance(request,staging,approved=True)
        self.assertNotIn('attach',backend.events);self.assertEqual(self.record(root)['phase'],'releasing')
        self.assert_restart_blocked(root,backend,staging)

    def test_release_completion_write_failure_preserves_loaded_host_without_forced_detach(self):
        root,backend,controller,staging=self.fixture()
        with controller.session():
            self.arm(controller,staging);request,_=self.activate(root,backend,controller,staging)
        release=GadgetController(backend,root/'state',maintenance_enabled=True)
        original_transition=release.maintenance.transition
        def fail_completion(value,phase,**kwargs):
            if phase=='released':raise OSError(errno.ENOSPC,'synthetic release completion failure')
            return original_transition(value,phase,**kwargs)
        backend.events.clear()
        with release.session(),mock.patch.object(release.maintenance,'transition',side_effect=fail_completion),self.assertRaises(BackupError):
            release.release_maintenance(request,staging,approved=True)
        self.assertTrue(backend.bound);self.assertEqual(backend.backing,str(backend.image))
        self.assertNotIn('detach',backend.events);self.assertEqual(self.record(root)['phase'],'releasing')
        self.assert_restart_blocked(root,backend,staging)

    def test_successfully_released_controller_returns_to_normal_but_request_is_one_shot(self):
        root,backend,controller,staging=self.fixture()
        with controller.session():
            self.arm(controller,staging);request,batch=self.activate(root,backend,controller,staging)
        release=GadgetController(backend,root/'state',maintenance_enabled=True)
        with release.session():release.release_maintenance(request,staging,approved=True)
        retained=(root/'state/maintenance.json').read_bytes()
        normal=GadgetController(backend,root/'state',maintenance_enabled=True)
        with normal.session():
            normal.start();self.assertEqual(normal.poll(staging),'waiting_for_host_eject')
            with self.assertRaises(BackupError):self.submit(root,backend)
            backend.events.clear();backend.eject()
            self.assertEqual(normal.poll(staging),'unchanged_snapshot')
            self.assertIn('attach',backend.events);self.assertTrue(backend.bound)
        self.assertTrue(batch.root.exists());self.assertEqual((root/'state/maintenance.json').read_bytes(),retained)

    @unittest.skipUnless(hasattr(os,'fork'),'process-crash fixture requires POSIX fork')
    def test_process_death_records_block_automatic_restart_at_each_transition(self):
        for phase in ('pending','capturing','captured','active','releasing'):
            root,backend,controller,staging=self.fixture(name=phase)
            child=os.fork()
            if child==0:
                try:
                    with controller.session():
                        self.arm(controller,staging);request=self.submit(root,backend)
                        if phase=='pending':os._exit(77)
                        if phase=='capturing':backend.detach_gadget=lambda:os._exit(77)
                        backend.eject();batch=controller.poll(staging)
                        if phase=='captured':os._exit(77)
                        batch.root.rename(root/'retained.ready');controller.complete_maintenance(staging)
                        if phase=='active':os._exit(77)
                        controller._attach=lambda:os._exit(77)
                        controller.release_maintenance(request,staging,approved=True)
                    os._exit(90)
                except BaseException:
                    os._exit(91)
            waited,status=os.waitpid(child,0)
            self.assertEqual(waited,child);self.assertTrue(os.WIFEXITED(status));self.assertEqual(os.WEXITSTATUS(status),77)
            self.assertEqual(self.record(root)['phase'],phase)
            for loaded in (False,True):
                backend.bound=loaded;backend.backing=str(backend.image) if loaded else '';backend.configured=loaded
                self.assert_restart_blocked(root,backend,staging,enabled=False)
            self.assertEqual((backend.mountpoint/'original.txt').read_bytes(),b'retain original')


if __name__=='__main__':
    unittest.main()
