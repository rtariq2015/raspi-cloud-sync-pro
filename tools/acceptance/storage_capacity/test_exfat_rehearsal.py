"""Hardware-free guard tests; never run the privileged exFAT rehearsal."""
from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    'stage3d2_rehearsal_under_test', Path(__file__).with_name('exfat_rehearsal.py'))
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)


def stat_fields(value, **changes):
    fields = {key:getattr(value,key) for key in dir(value) if key.startswith('st_')}
    return SimpleNamespace(**{**fields, **changes})


class RehearsalGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='raspi-stage3d2-unit-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)

    @contextmanager
    def root_metadata(self, lab):
        """Mock ownership only; fixtures are ordinary unprivileged tiny files."""
        real = Path.lstat
        def observed(path, *args, **kwargs):
            value = real(path,*args,**kwargs)
            return stat_fields(value,st_uid=0) if path == lab.root else value
        with mock.patch.object(Path,'lstat',autospec=True,side_effect=observed):
            yield

    def lab(self):
        lab = rehearsal.Lab()
        lab.root = self.root
        info = self.root.stat()
        lab.root_identity = (info.st_dev,info.st_ino)
        return lab

    def test_preflight_refuses_unprivileged_before_creating_scratch(self):
        with mock.patch.object(rehearsal.os,'geteuid',return_value=1000), \
                mock.patch.object(rehearsal.tempfile,'mkdtemp') as created, \
                self.assertRaisesRegex(RuntimeError,'requires_approved_development_root'):
            rehearsal.Lab().start()
        created.assert_not_called()

    def test_preflight_refuses_other_architecture_before_scratch(self):
        with mock.patch.object(rehearsal.os,'geteuid',return_value=0), \
                mock.patch.object(rehearsal.platform,'machine',return_value='aarch64'), \
                mock.patch.object(rehearsal.tempfile,'mkdtemp') as created, \
                self.assertRaisesRegex(RuntimeError,'requires_approved_development_root'):
            rehearsal.Lab().start()
        created.assert_not_called()

    def test_preflight_refuses_non_zorin_before_scratch(self):
        with mock.patch.object(rehearsal.os,'geteuid',return_value=0), \
                mock.patch.object(rehearsal.platform,'machine',return_value='x86_64'), \
                mock.patch.object(Path,'read_text',return_value='ID=ubuntu\nVERSION_CODENAME=noble\n'), \
                mock.patch.object(rehearsal.tempfile,'mkdtemp') as created, \
                self.assertRaisesRegex(RuntimeError,'unexpected_development_os'):
            rehearsal.Lab().start()
        created.assert_not_called()

    def test_preflight_refuses_initial_mount_namespace_before_scratch(self):
        with mock.patch.object(rehearsal.os,'geteuid',return_value=0), \
                mock.patch.object(rehearsal.platform,'machine',return_value='x86_64'), \
                mock.patch.object(Path,'read_text',return_value='ID=zorin\nVERSION_CODENAME=noble\n'), \
                mock.patch.object(rehearsal.os,'stat',return_value=SimpleNamespace(st_ino=123)), \
                mock.patch.object(rehearsal.tempfile,'mkdtemp') as created, \
                self.assertRaisesRegex(RuntimeError,'private_mount_namespace_required'):
            rehearsal.Lab().start()
        created.assert_not_called()

    def test_preflight_refuses_shared_mount_propagation_before_scratch(self):
        for propagation in ('shared:42','master:42','propagate_from:42'):
            with self.subTest(propagation=propagation), \
                    mock.patch.object(rehearsal.os,'geteuid',return_value=0), \
                    mock.patch.object(rehearsal.platform,'machine',return_value='x86_64'), \
                    mock.patch.object(Path,'read_text',side_effect=[
                        'ID=zorin\nVERSION_CODENAME=noble\n',
                        '1 0 8:1 / / rw '+propagation+' - ext4 /dev/synthetic rw\n']), \
                    mock.patch.object(rehearsal.os,'stat',side_effect=[
                        SimpleNamespace(st_ino=123),SimpleNamespace(st_ino=456)]), \
                    mock.patch.object(rehearsal.tempfile,'mkdtemp') as created, \
                    self.assertRaisesRegex(RuntimeError,'mount_propagation_not_private'):
                rehearsal.Lab().start()
            created.assert_not_called()

    def test_path_guard_accepts_only_descendants_and_refuses_symlink(self):
        lab = self.lab()
        outside = self.root/'outside';outside.mkdir()
        (self.root/'link').symlink_to(outside,target_is_directory=True)
        with self.root_metadata(lab):
            self.assertEqual(lab.path('new.img'),self.root/'new.img')
            for name in ('..','../escape.img','/etc/passwd','link/file.img','.'):
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError,'outside_scratch_or_symlink'):
                    lab.path(name)

    def test_path_guard_rejects_changed_directory_identity_or_permissions(self):
        lab = self.lab()
        with self.root_metadata(lab):
            lab.root_identity = (0,0)
            with self.assertRaisesRegex(RuntimeError,'scratch_identity_changed'):lab.path('new.img')
        lab = self.lab();self.root.chmod(0o750)
        with self.root_metadata(lab), self.assertRaisesRegex(RuntimeError,'scratch_identity_changed'):
            lab.path('new.img')

    def test_image_guard_rejects_wrong_identity_links_or_file_type(self):
        path = self.root/'small.img';path.write_bytes(b'tiny unit fixture');path.chmod(0o600)
        info = path.lstat()
        image = {'path':path,'identity':(info.st_dev,info.st_ino,info.st_size)}
        lab = self.lab();lab.path = mock.Mock(return_value=path)
        valid = stat_fields(info,st_uid=0)
        with mock.patch.object(Path,'lstat',return_value=valid):
            self.assertEqual(lab.identity(image).st_ino,info.st_ino)
        for changes in ({'st_dev':info.st_dev+1},{'st_ino':info.st_ino+1},{'st_size':info.st_size+1},
                        {'st_nlink':2},{'st_uid':1000},{'st_mode':stat.S_IFBLK|0o600},
                        {'st_mode':stat.S_IFREG|0o644}):
            with self.subTest(changes=changes), mock.patch.object(Path,'lstat',return_value=stat_fields(valid,**changes)), \
                    self.assertRaisesRegex(RuntimeError,'image_identity_changed'):
                lab.identity(image)

    def loop_fixture(self):
        lab = self.lab()
        image = {'path':self.root/'synthetic.img','identity':(os.makedev(8,1),23,rehearsal.EXPANDED_SIZE),
                 'loop':'/dev/loop999999','mount':None,'ro':False}
        info = SimpleNamespace(st_dev=image['identity'][0],st_ino=23,st_size=rehearsal.EXPANDED_SIZE)
        lab.identity = mock.Mock(return_value=info)
        row = {'name':image['loop'],'back-file':str(image['path']),'back-ino':23,'back-maj:min':'8:1',
               'offset':rehearsal.PARTITION_OFFSET,'sizelimit':rehearsal.EXPANDED_SIZE-rehearsal.PARTITION_OFFSET,
               'ro':False,'log-sec':512}
        lab.loops = mock.Mock(return_value=[row]);lab.mounts = mock.Mock(return_value=[])
        lab.external_mounts = mock.Mock(return_value=[])
        return lab,image,row

    def test_loop_guard_accepts_exact_mapping_without_device_access(self):
        lab,image,row = self.loop_fixture()
        lab.verify(image)
        lab.mounts.assert_called_once_with(image)

    def test_loop_guard_refuses_wrong_backing_geometry_or_readonly_mode(self):
        for changes in ({'name':'/dev/loop999998'},{'back-file':'/outside.img'},
                        {'back-ino':24},{'back-maj:min':'8:2'},{'offset':0},
                        {'sizelimit':rehearsal.EXPANDED_SIZE},{'log-sec':4096},{'ro':True}):
            lab,image,row = self.loop_fixture();lab.loops.return_value=[{**row,**changes}]
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):lab.verify(image)

    def test_loop_guard_refuses_duplicate_mapping_and_non_loop_names(self):
        lab,image,row = self.loop_fixture();lab.loops.return_value=[row,dict(row)]
        with self.assertRaisesRegex(RuntimeError,'ambiguous_loop_mapping'):lab.verify(image)
        image['loop']='/dev/synthetic-physical-device'
        with self.assertRaisesRegex(RuntimeError,'invalid_loop_name'):lab.verify(image)

    def test_loop_guard_refuses_mount_outside_private_namespace(self):
        lab,image,row = self.loop_fixture()
        lab.external_mounts.return_value=[['synthetic-external-mount']]
        with self.assertRaisesRegex(RuntimeError,'test_loop_mounted_outside_private_namespace'):
            lab.verify(image)
        lab.mounts.assert_not_called()

    def test_loop_guard_refuses_unexpected_or_unsafe_mount(self):
        for target,options,fstype in ((str(self.root/'wrong'),'rw,nosuid,nodev,noexec','exfat'),
                                    (str(self.root/'mount'),'rw,nosuid,nodev','exfat'),
                                    (str(self.root/'mount'),'ro,nosuid,nodev,noexec','exfat'),
                                    (str(self.root/'mount'),'rw,nosuid,nodev,noexec','ext4')):
            lab,image,row=self.loop_fixture();image['mount']=self.root/'mount'
            fields=['99','1','7:99','/',target,options,'- ',fstype,image['loop'],'rw']
            fields[6]='-';lab.mounts.return_value=[fields]
            with self.subTest(target=target,options=options,fstype=fstype),self.assertRaises(RuntimeError):
                lab.verify(image,mounted=True)
        lab,image,row=self.loop_fixture();lab.mounts.return_value=[['synthetic-mounted']]
        with self.assertRaisesRegex(RuntimeError,'loop_still_mounted'):lab.verify(image)

    def test_command_allowlist_refuses_unrelated_command_without_subprocess(self):
        lab=self.lab()
        with mock.patch.object(rehearsal.subprocess,'run') as executed, \
                self.assertRaisesRegex(RuntimeError,'unsupported_tool'):
            lab.command(['/usr/bin/unrelated','argument'])
        executed.assert_not_called()

    def source_fixture(self):
        source=self.root/'source';source.mkdir()
        (source/'nested'/'QA Δ').mkdir(parents=True)
        (source/'empty-directory').mkdir()
        (source/'empty.txt').write_bytes(b'')
        (source/'nested'/'QA Δ'/'报告 α.txt').write_bytes(b'synthetic-only\n')
        (source/'payload.bin').write_bytes(b'X'*65537)
        return source

    def test_copy_preserves_unicode_empty_directories_hashes_and_source(self):
        source=self.source_fixture();before=rehearsal.tree(source);destination=self.root/'candidate'
        result=rehearsal.copy_tree(source,destination)
        self.assertEqual(result,before);self.assertEqual(rehearsal.tree(destination),before)
        self.assertEqual(rehearsal.tree(source),before)
        self.assertEqual(before['files']['payload.bin']['sha256'],hashlib.sha256(b'X'*65537).hexdigest())
        self.assertIn('empty-directory',result['directories'])

    def test_repeat_copy_and_candidate_symlink_refuse_without_overwrites(self):
        source=self.source_fixture();destination=self.root/'candidate'
        rehearsal.copy_tree(source,destination);before=rehearsal.tree(destination)
        with self.assertRaisesRegex(RuntimeError,'candidate_collision'):rehearsal.copy_tree(source,destination)
        self.assertEqual(rehearsal.tree(destination),before)
        (self.root/'linked-candidate').symlink_to(destination,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'candidate_collision'):
            rehearsal.copy_tree(source,self.root/'linked-candidate')
        self.assertEqual(rehearsal.tree(destination),before)

    def test_insufficient_space_refuses_before_candidate_creation(self):
        source=self.source_fixture();before=rehearsal.tree(source);destination=self.root/'candidate'
        with mock.patch.object(rehearsal.shutil,'disk_usage',return_value=SimpleNamespace(free=0)), \
                self.assertRaisesRegex(RuntimeError,'migration_space_low'):
            rehearsal.copy_tree(source,destination)
        self.assertFalse(destination.exists());self.assertEqual(rehearsal.tree(source),before)

    def test_tree_refuses_symlink_or_hardlinked_source_entries(self):
        source=self.source_fixture();(source/'symlink').symlink_to(source/'payload.bin')
        with self.assertRaisesRegex(RuntimeError,'unsafe_tree_entry'):rehearsal.tree(source)
        (source/'symlink').unlink();os.link(source/'payload.bin',source/'second-link')
        with self.assertRaisesRegex(RuntimeError,'unsafe_tree_entry'):rehearsal.tree(source)

    def test_interruption_retains_partial_and_original_without_exit_or_subprocess(self):
        source=self.source_fixture();before=rehearsal.tree(source);destination=self.root/'candidate.incomplete'
        class InjectedStop(BaseException):pass
        with mock.patch.object(rehearsal.os,'_exit',side_effect=InjectedStop) as stopped, \
                self.assertRaises(InjectedStop):
            rehearsal.copy_tree(source,destination,die_after_first_chunk=True)
        stopped.assert_called_once_with(73)
        self.assertTrue(destination.exists());self.assertNotEqual(rehearsal.tree(destination),before)
        self.assertEqual(rehearsal.tree(source),before)

    def test_source_metadata_change_during_copy_is_refused(self):
        source=self.source_fixture();destination=self.root/'candidate';target=source/'payload.bin'
        real=Path.stat;calls=[]
        def observed(path,*args,**kwargs):
            result=real(path,*args,**kwargs)
            if path==target:
                calls.append(path)
                if len(calls)==2:return stat_fields(result,st_mtime_ns=result.st_mtime_ns+1)
            return result
        with mock.patch.object(Path,'stat',autospec=True,side_effect=observed), \
                self.assertRaisesRegex(RuntimeError,'source_metadata_changed'):
            rehearsal.copy_tree(source,destination)
        self.assertTrue(destination.exists())


if __name__=='__main__':unittest.main()
