"""Synthetic kernel evidence only: never open an actual device or change swap."""

from copy import deepcopy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from raspi_cloud_sync_pro.errors import BackupError


class KernelEvidence:
    """Independent proc/sysfs/block-status views of one simulated OS device."""

    def __init__(self):
        self.loop, self.zram = '/dev/loop37', '/dev/zram5'
        self.configured=[self.loop]
        self.backing = '/synthetic/os-memory-backing'
        self.rdev = {self.loop:(7,37), self.zram:(252,5)}
        self.bindings = {'loop37':(7,37), 'zram5':(252,5)}
        self.status = {'device':os.makedev(8,1),'inode':7000,'number':37,
                       'offset':0,'sizelimit':0,'flags':0}
        self.file_identity = (os.makedev(8,1),7000,64*1024**2,0,0,0o600,1)
        self.data = {
            '/proc/swaps':'Filename\tType\tSize\tUsed\tPriority\n/dev/zram5 partition 32764 64 100\n',
            '/proc/self/mountinfo':'25 1 8:1 / / rw - ext4 /dev/root rw\n',
            '/sys/class/block/zram5/backing_dev':self.loop,
            '/sys/class/block/zram5/disksize':str(32*1024**2),
            '/sys/class/block/zram5/dev':'252:5',
            '/sys/class/block/loop37/dev':'7:37',
            '/sys/class/block/loop37/loop/backing_file':self.backing,
        }
        self.directories = {
            '/sys/class/block':[Path('/sys/class/block/loop37'),Path('/sys/class/block/zram5')],
            '/sys/class/block/loop37':[],
            '/sys/class/block/loop37/holders':[],
            '/sys/class/block/zram5':[],
            '/sys/class/block/zram5/holders':[],
        }

    @property
    def rows(self):
        return [{'name':self.loop,'back-file':self.backing,'ro':False}]

    def read(self, path):
        return self.data[str(path)]

    def children(self, path):
        return list(self.directories[str(path)])

    def block_identity(self, path):
        return self.rdev[str(path)]

    def configured_loops(self):
        return list(self.configured)

    def virtual_binding(self, name, identity):
        if self.bindings.get(name) != identity:
            raise BackupError('synthetic_kernel_binding_mismatch')

    def loop_status(self, path):
        if str(path) != self.loop:
            raise BackupError('synthetic_unknown_loop')
        return deepcopy(self.status)

    def backing_identity(self, path):
        if str(path) != self.backing:
            raise BackupError('synthetic_wrong_backing_path')
        return self.file_identity


class EvidenceIO:
    def __init__(self, probe):
        self.probe = probe
        self.rows = probe.rows
        self.query_count = 0
        self.evidence_count = 0
        self.on_query = None
        self.on_evidence = None

    def query(self, arguments):
        if arguments != ['losetup','--list','--json','--output','NAME,BACK-FILE,RO']:
            raise AssertionError('unexpected command, not a read-only loop inventory')
        self.query_count += 1
        if self.on_query:
            self.on_query(self)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'loopdevices':deepcopy(self.rows)}))

    def enrollment_loop_evidence(self, rows):
        from raspi_cloud_sync_pro.enrollment_loops import collect_loop_evidence
        self.evidence_count += 1
        if self.on_evidence:
            self.on_evidence(self)
        return collect_loop_evidence(rows,probe=self.probe)


class ZramLoopVerificationTests(unittest.TestCase):
    def setUp(self):
        self.probe = KernelEvidence()
        self.identities = ((os.makedev(8,1),1001,1024**3),
                           (os.makedev(8,1),1002,8*1024**3))

    def collect(self, rows=None):
        from raspi_cloud_sync_pro.enrollment_loops import collect_loop_evidence
        return collect_loop_evidence(self.probe.rows if rows is None else rows,probe=self.probe)

    def inventory(self, io=None):
        from raspi_cloud_sync_pro.enrollment_loops import verified_loop_inventory
        return verified_loop_inventory(io or EvidenceIO(self.probe),self.identities)

    def test_verified_unrelated_kernel_zram_backing_is_accepted_without_name_exception(self):
        result = self.inventory()
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['loop'],[7,37])
        self.assertEqual(result[0]['zram'],[252,5])
        self.assertEqual(result[0]['backing'],list(self.probe.file_identity))
        self.assertNotIn(self.probe.backing,json.dumps(result))

    def test_empty_inventory_preserves_existing_no_loop_behavior(self):
        self.probe.configured=[]
        self.probe.data['/proc/swaps']='Filename Type Size Used Priority\n'
        io = EvidenceIO(self.probe);io.rows=[]
        self.assertEqual(self.inventory(io),[])

    def test_empty_inventory_without_kernel_completeness_evidence_is_rejected(self):
        io=SimpleNamespace(query=lambda args:SimpleNamespace(
            returncode=0,stdout=json.dumps({'loopdevices':[]})))
        with self.assertRaises(BackupError):self.inventory(io)

    def test_empty_or_truncated_inventory_cannot_hide_a_configured_kernel_loop(self):
        for rows,configured in (([],['/dev/loop37']),
                                (self.probe.rows,['/dev/loop37','/dev/loop38'])):
            with self.subTest(rows=rows):
                self.probe.configured=configured
                io=EvidenceIO(self.probe);io.rows=rows
                with self.assertRaises(BackupError):self.inventory(io)

    def test_original_candidate_and_size_aliases_are_rejected_by_backing_inode(self):
        for identity in self.identities:
            with self.subTest(image_inode=identity[1]):
                probe = KernelEvidence()
                probe.status.update(device=identity[0],inode=identity[1])
                probe.file_identity = (identity[0],identity[1],identity[2]+4096,0,0,0o600,1)
                with self.assertRaises(BackupError):
                    self.inventory(EvidenceIO(probe))

    def test_hardlink_alias_is_rejected(self):
        self.probe.file_identity = (*self.probe.file_identity[:-1],2)
        with self.assertRaises(BackupError):self.inventory()

    def test_symlink_or_unresolvable_alias_cannot_supply_backing_evidence(self):
        def refuse(path):
            raise OSError('synthetic no-follow backing identity refusal')
        self.probe.backing_identity=refuse
        with self.assertRaises(BackupError):self.inventory()

    def test_unknown_loop_is_not_accepted_by_a_swap_looking_filename(self):
        self.probe.data['/proc/swaps']='Filename Type Size Used Priority\n'
        with self.assertRaises(BackupError):self.inventory()

    def test_reported_backing_relationship_must_match_kernel_device_identity(self):
        for change in ('different_loop','wrong_loop_device','wrong_zram_device','wrong_virtual_binding'):
            with self.subTest(change=change):
                self.probe=KernelEvidence()
                if change=='different_loop':self.probe.data['/sys/class/block/zram5/backing_dev']='/dev/loop38'
                elif change=='wrong_loop_device':self.probe.rdev[self.probe.loop]=(8,37)
                elif change=='wrong_zram_device':self.probe.rdev[self.probe.zram]=(7,37)
                else:self.probe.bindings['zram5']=(252,6)
                with self.assertRaises(BackupError):self.inventory()

    def test_ioctl_and_backing_file_identities_must_agree(self):
        for index in (0,1):
            with self.subTest(identity_field=index):
                self.probe=KernelEvidence();values=list(self.probe.file_identity);values[index]+=1
                self.probe.file_identity=tuple(values)
                with self.assertRaises(BackupError):self.inventory()

    def test_unknown_ioctl_flags_number_or_size_are_rejected(self):
        for key,value in (('number',38),('flags',0x8000),('sizelimit',128*1024**2)):
            with self.subTest(field=key):
                self.probe=KernelEvidence();self.probe.status[key]=value
                with self.assertRaises(BackupError):self.inventory()

    def test_missing_proc_sysfs_or_ioctl_evidence_refuses(self):
        for key in ('/proc/swaps','/proc/self/mountinfo','/sys/class/block/zram5/backing_dev',
                    '/sys/class/block/zram5/disksize','/sys/class/block/loop37/loop/backing_file'):
            with self.subTest(missing=key):
                self.probe=KernelEvidence();del self.probe.data[key]
                with self.assertRaises(BackupError):self.inventory()
        self.probe=KernelEvidence()
        def absent(path):raise OSError('synthetic missing ioctl evidence')
        self.probe.loop_status=absent
        with self.assertRaises(BackupError):self.inventory()

    def test_unknown_mount_partition_and_holder_are_rejected(self):
        for change in ('loop_mount','zram_mount','partition','holder'):
            with self.subTest(change=change):
                self.probe=KernelEvidence()
                if change.endswith('_mount'):
                    dev='7:37' if change=='loop_mount' else '252:5'
                    self.probe.data['/proc/self/mountinfo']+=f'26 25 {dev} / /synthetic rw - ext4 /dev/other rw\n'
                elif change=='partition':
                    self.probe.directories['/sys/class/block/loop37']=[Path('/sys/class/block/loop37/loop37p1')]
                else:
                    self.probe.directories['/sys/class/block/loop37/holders']=[Path('/sys/class/block/loop37/holders/dm-0')]
                with self.assertRaises(BackupError):self.inventory()

    def test_malformed_inventory_or_readonly_flags_are_rejected(self):
        for rows in ([{'name':'/dev/loop37','back-file':self.probe.backing,'ro':'false'}],
                     [{'name':'/dev/loop37','back-file':self.probe.backing,'ro':True}],
                     [{'name':'/dev/loop37','back-file':None,'ro':False}],
                     self.probe.rows+self.probe.rows):
            with self.subTest(rows=rows),self.assertRaises(BackupError):self.collect(rows)

    def test_backing_ownership_permissions_linkage_and_file_size_are_checked(self):
        for index,value in ((2,0),(3,1000),(5,0o666),(6,2)):
            with self.subTest(field=index):
                self.probe=KernelEvidence();identity=list(self.probe.file_identity);identity[index]=value
                self.probe.file_identity=tuple(identity)
                with self.assertRaises(BackupError):self.inventory()

    def test_disappearing_loop_between_inventory_samples_refuses(self):
        io=EvidenceIO(self.probe)
        io.on_query=lambda s:setattr(s,'rows',[]) if s.query_count==2 else None
        with self.assertRaises(BackupError):self.inventory(io)

    def test_changed_loop_or_backing_identity_between_samples_refuses(self):
        for change in ('loop_device','backing_inode','zram_device'):
            with self.subTest(change=change):
                self.probe=KernelEvidence();io=EvidenceIO(self.probe)
                def mutate(s):
                    if s.evidence_count!=2:return
                    if change=='loop_device':s.probe.rdev[s.probe.loop]=(7,38)
                    elif change=='backing_inode':
                        s.probe.status['inode']+=1;value=list(s.probe.file_identity);value[1]+=1;s.probe.file_identity=tuple(value)
                    else:s.probe.rdev[s.probe.zram]=(252,6)
                io.on_evidence=mutate
                with self.assertRaises(BackupError):self.inventory(io)

    def test_multiple_active_zram_claims_for_one_loop_are_ambiguous(self):
        self.probe.data['/proc/swaps']+='/dev/zram6 partition 32764 32 100\n'
        self.probe.data['/sys/class/block/zram6/backing_dev']=self.probe.loop
        self.probe.data['/sys/class/block/zram6/disksize']=str(32*1024**2)
        self.probe.rdev['/dev/zram6']=(252,6);self.probe.bindings['zram6']=(252,6)
        with self.assertRaises(BackupError):self.inventory()


class KernelLoopProbeTests(unittest.TestCase):
    """Mock every device boundary, including the read-only kernel ioctl."""

    def setUp(self):
        from raspi_cloud_sync_pro.enrollment_loops import KernelLoopProbe
        self.probe=KernelLoopProbe()
        self.device=SimpleNamespace(st_dev=5,st_ino=9,st_rdev=os.makedev(7,37),
                                    st_mode=stat.S_IFBLK|0o600,st_uid=0,st_gid=0)
        self.payload=struct.pack('=5Q4I64s64s32s2Q',os.makedev(8,1),7000,0,0,0,
                                 37,0,0,0,b'',b'',b'',0,0)
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        namespace='raspi_cloud_sync_pro.enrollment_loops.'
        self.open=self.stack.enter_context(patch(namespace+'os.open',return_value=123))
        self.close=self.stack.enter_context(patch(namespace+'os.close'))
        self.fstat=self.stack.enter_context(patch(namespace+'os.fstat',return_value=self.device))
        self.lstat=self.stack.enter_context(patch(namespace+'Path.lstat',return_value=self.device))
        def get_status(fd,request,data,mutate):
            self.assertEqual((fd,request,mutate),(123,0x4C05,True))
            self.assertEqual(len(data),232)
            data[:]=self.payload
            return 0
        self.ioctl=self.stack.enter_context(patch(namespace+'fcntl.ioctl',side_effect=get_status))

    def test_loop_get_status_opens_readonly_and_closes_the_exact_mocked_device(self):
        result=self.probe.loop_status('/dev/loop37')
        self.assertEqual(result,{'device':os.makedev(8,1),'inode':7000,'number':37,
                                 'offset':0,'sizelimit':0,'flags':0})
        self.assertEqual(self.open.call_args.args[0],Path('/dev/loop37'))
        flags=self.open.call_args.args[1]
        self.assertEqual(flags&os.O_ACCMODE,os.O_RDONLY)
        self.assertTrue(flags&os.O_NOFOLLOW)
        self.assertTrue(flags&os.O_NONBLOCK)
        self.assertTrue(flags&os.O_CLOEXEC)
        self.ioctl.assert_called_once()
        self.close.assert_called_once_with(123)

    def test_device_replacement_during_status_read_is_rejected_and_descriptor_closed(self):
        changed=SimpleNamespace(**vars(self.device));changed.st_ino+=1
        self.lstat.side_effect=[self.device,changed]
        with self.assertRaises(BackupError):self.probe.loop_status('/dev/loop37')
        self.close.assert_called_once_with(123)

    def test_nonblock_device_or_wrong_kernel_major_never_reaches_ioctl(self):
        for changed in (dict(st_mode=stat.S_IFREG|0o600),dict(st_rdev=os.makedev(8,37)),
                        dict(st_uid=1000)):
            with self.subTest(changed=changed):
                item=SimpleNamespace(**vars(self.device))
                vars(item).update(changed)
                self.fstat.return_value=item;self.lstat.return_value=item
                self.ioctl.reset_mock();self.close.reset_mock()
                with self.assertRaises(BackupError):self.probe.loop_status('/dev/loop37')
                self.ioctl.assert_not_called()
                self.close.assert_called_once_with(123)

    def test_mismatched_loop_number_and_encrypted_loop_status_are_rejected(self):
        for number,encryption,key_size in ((38,0,0),(37,1,0),(37,0,16)):
            with self.subTest(number=number,encryption=encryption,key_size=key_size):
                self.payload=struct.pack('=5Q4I64s64s32s2Q',os.makedev(8,1),7000,0,0,0,
                                         number,encryption,key_size,0,b'',b'',b'',0,0)
                self.close.reset_mock()
                with self.assertRaises(BackupError):self.probe.loop_status('/dev/loop37')
                self.close.assert_called_once_with(123)

    def test_invalid_device_path_is_refused_before_open(self):
        for path in ('/tmp/loop37','/dev/mapper/loop37','/dev/loop37/../loop0','/dev/sda'):
            with self.subTest(path=path),self.assertRaises(BackupError):
                self.probe.loop_status(path)
        self.open.assert_not_called()

    def test_backing_symlink_alias_is_refused_before_file_open(self):
        namespace='raspi_cloud_sync_pro.enrollment_loops.'
        with patch(namespace+'Path.resolve',return_value=Path('/synthetic/real-image')):
            with self.assertRaises(BackupError):
                self.probe.backing_identity('/synthetic/image-symlink')
        self.open.assert_not_called()

    def test_configured_sysfs_inventory_ignores_only_unconfigured_loops_and_nonloops(self):
        directory=Path('/sys/class/block')
        def children(path):
            if Path(path)==directory:
                return [directory/name for name in ('loop1','loop37','loop37p1','zram5')]
            if Path(path)==directory/'loop1':return [directory/'loop1/dev']
            if Path(path)==directory/'loop37':return [directory/'loop37/loop']
            raise AssertionError('unexpected sysfs traversal')
        with patch.object(self.probe,'children',side_effect=children),\
             patch.object(self.probe,'virtual_binding') as binding,\
             patch.object(self.probe,'block_identity',return_value=(7,37)),\
             patch.object(self.probe,'read',return_value='/synthetic/os-backing') as read:
            self.assertEqual(self.probe.configured_loops(),['/dev/loop37'])
            binding.assert_called_once_with('loop37',(7,37))
            read.assert_called_once_with(directory/'loop37/loop/backing_file')
        self.open.assert_not_called()

    def test_sysfs_enumeration_failure_is_never_interpreted_as_no_loops(self):
        directory=Path('/sys/class/block')
        with patch.object(self.probe,'children',side_effect=[[directory/'loop37'],OSError('missing evidence')]):
            with self.assertRaises(OSError):self.probe.configured_loops()
        self.open.assert_not_called()


if __name__=='__main__':
    unittest.main()
