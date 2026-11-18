"""Opt-in Stage 3D2 development-host rehearsal; no live paths or USB operations.

Run only after explicit approval, as root in a private mount namespace. Creates
its own directory and four fully allocated disposable 8 GiB files. Never accepts
an image/device/directory argument. Retains evidence; never repairs or deletes.
"""
import argparse
from contextlib import contextmanager
import datetime
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'src'))
from raspi_cloud_sync_pro.capture_space import CaptureSpacePolicy
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.mass_storage_image import (
    EXPANDED_SIZE, PARTITION_OFFSET, image_layout, validate_image_approval, validate_image_headers,
)
from raspi_cloud_sync_pro.staging import stage_changed_snapshot
from raspi_cloud_sync_pro.storage import StorageOwnership

spec = importlib.util.spec_from_file_location('stage3d2_mbr',
    REPO / 'tools/acceptance/mass_storage/prepare_image.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
MIB = 1024 ** 2
GIB = 1024 ** 3
CHUNK = MIB
TOOLS = {'losetup':'/usr/sbin/losetup', 'mkfs':'/usr/sbin/mkfs.exfat',
         'fsck':'/usr/sbin/fsck.exfat', 'dump':'/usr/sbin/dump.exfat',
         'mount':'/usr/bin/mount', 'umount':'/usr/bin/umount'}
LOOP_FIELDS = 'NAME,BACK-FILE,BACK-INO,BACK-MAJ:MIN,OFFSET,SIZELIMIT,RO,LOG-SEC'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(CHUNK), b''):
            digest.update(data)
    return digest.hexdigest()


def tree(path):
    """Manifest includes empty directories; refuses links and special files."""
    require(path.is_dir() and not path.is_symlink(), 'unsafe_tree_root')
    files, directories = {}, []
    for current, dirs, names in os.walk(path):
        for name in sorted(dirs + names):
            child = Path(current) / name
            info = child.lstat()
            relative = child.relative_to(path).as_posix()
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe_tree_entry')
                files[relative] = {'size':info.st_size, 'sha256':sha(child)}
    return {'directories':sorted(directories), 'files':files}


def copy_tree(source, destination, *, die_after_first_chunk=False):
    """One fresh candidate; no overwrites, no deletion, no live cutover."""
    before = tree(source)
    require(not os.path.lexists(destination), 'candidate_collision')
    required = sum(item['size'] for item in before['files'].values()) + 16*MIB
    require(shutil.disk_usage(destination.parent).free >= required, 'migration_space_low')
    destination.mkdir(mode=0o700)
    for relative in before['directories']:
        (destination / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    for relative in sorted(before['files']):
        incoming, outgoing = source/relative, destination/relative
        original = incoming.stat()
        with incoming.open('rb') as src, outgoing.open('xb') as dst:
            for block in iter(lambda: src.read(CHUNK), b''):
                require(dst.write(block) == len(block), 'short_copy')
                if die_after_first_chunk:
                    dst.flush();os.fsync(dst.fileno());os._exit(73)
            dst.flush();os.fsync(dst.fileno())
        after = incoming.stat()
        require((original.st_dev,original.st_ino,original.st_size,original.st_mtime_ns,original.st_ctime_ns)
                == (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),
                'source_metadata_changed')
    for current, _, _ in os.walk(destination, topdown=False):
        fsync_dir(current)
    fsync_dir(destination.parent)
    require(tree(source) == before and tree(destination) == before, 'copy_integrity_failed')
    return before


class Lab:
    def __init__(self):
        self.root = None
        self.root_identity = None
        self.images = {}
        self.results = []
        self.evidence = {'commands':[], 'checks':self.results, 'images':{}, 'status':'running'}

    def start(self):
        require(os.geteuid() == 0 and platform.machine() == 'x86_64', 'requires_approved_development_root')
        release = Path('/etc/os-release').read_text()
        require('ID=zorin\n' in release and 'VERSION_CODENAME=noble\n' in release, 'unexpected_development_os')
        require(os.stat('/proc/self/ns/mnt').st_ino != os.stat('/proc/1/ns/mnt').st_ino,
                'private_mount_namespace_required')
        require(not any(field.startswith(('shared:','master:','propagate_from:'))
                        for line in Path('/proc/self/mountinfo').read_text().splitlines()
                        for field in line.split()[6:line.split().index('-')]),
                'mount_propagation_not_private')
        require(any(line.split() and line.split()[-1] == 'exfat'
                    for line in Path('/proc/filesystems').read_text().splitlines()), 'exfat_not_registered')
        require(all(Path(tool).is_file() and os.access(tool,os.X_OK) for tool in TOOLS.values()), 'missing_tools')
        tmp = Path('/tmp').lstat()
        require(stat.S_ISDIR(tmp.st_mode) and tmp.st_uid == 0 and tmp.st_mode & stat.S_ISVTX,
                'unsafe_scratch_parent')
        capacity = os.statvfs('/tmp')
        require(capacity.f_bavail*capacity.f_frsize >= 48*GIB and capacity.f_favail > 10000,
                'insufficient_scratch_capacity')
        self.root = Path(tempfile.mkdtemp(prefix='raspi-stage3d2-', dir='/tmp'))
        self.root.chmod(0o700)
        info = self.root.lstat();self.root_identity = (info.st_dev,info.st_ino)
        self.evidence.update(start_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            kernel=platform.release(), architecture=platform.machine(), scratch=str(self.root),
            available_bytes=capacity.f_bavail*capacity.f_frsize,
            mount_namespace_private=True, source_checkpoint='6c7414e48119ede2bf6c39307b05b867e771fe6d')
        self.save()
        print('Private Stage 3D2 scratch:', self.root, flush=True)

    def path(self, name):
        info = self.root.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700
                and (info.st_dev,info.st_ino) == self.root_identity, 'scratch_identity_changed')
        path = self.root/name
        require(path.is_absolute() and '..' not in path.parts and path != self.root
                and self.root in path.parents and path.resolve() == path, 'outside_scratch_or_symlink')
        return path

    def save(self):
        path = self.path('evidence.json')
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd,'w') as output:
            json.dump(self.evidence,output,indent=2,sort_keys=True);output.write('\n')
            output.flush();os.fsync(output.fileno())
        fsync_dir(self.root)

    def command(self, args, *, allowed=(0,), inventory=False):
        require(args[0] in TOOLS.values(), 'unsupported_tool')
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=240, env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
        self.evidence['commands'].append({'argv':args,'exit':result.returncode,
            'stdout':'[unrelated inventory omitted]' if inventory else result.stdout[:12000],
            'stderr':result.stderr[:12000]})
        self.save()
        require(result.returncode in allowed, 'tool_failed:'+Path(args[0]).name)
        return result

    def check(self, name, function):
        function()
        self.results.append({'name':name,'result':'PASS'})
        self.save()
        print('PASS:',name,flush=True)

    def create(self, name):
        require(name not in self.images and re.fullmatch(r'[a-z-]+',name), 'invalid_image_name')
        path = self.path(name+'.img')
        fd = os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            os.posix_fallocate(fd,0,EXPANDED_SIZE)
            require(os.pwrite(fd,prepare.mbr(EXPANDED_SIZE),0)==512,'short_mbr_write')
            os.fsync(fd);info=os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size==EXPANDED_SIZE
                    and info.st_blocks*512>=EXPANDED_SIZE,'image_not_fully_allocated')
            identity=(info.st_dev,info.st_ino,info.st_size)
        finally:
            os.close(fd)
        fsync_dir(self.root)
        self.images[name]={'path':path,'identity':identity,'loop':None,'mount':None,'ro':False}
        self.evidence['images'][name]={'identity':identity,'allocated_bytes':info.st_blocks*512}
        self.save()

    def identity(self, image):
        path=self.path(image['path'].name);info=path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==0
                and stat.S_IMODE(info.st_mode)==0o600
                and (info.st_dev,info.st_ino,info.st_size)==image['identity'],'image_identity_changed')
        return info

    def loops(self):
        # Read-only inventory; evidence never exports unrelated loop mappings.
        return json.loads(self.command([TOOLS['losetup'],'--list','--json','--output',LOOP_FIELDS],inventory=True).stdout)['loopdevices']

    def related(self, image, rows):
        info=self.identity(image)
        major_minor=f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
        return [row for row in rows if row.get('back-file')==str(image['path'])
                or (str(row.get('back-ino'))==str(info.st_ino) and row.get('back-maj:min')==major_minor)]

    def mounts(self, image):
        info=Path(image['loop']).stat()
        require(stat.S_ISBLK(info.st_mode) and os.major(info.st_rdev)==7,'not_loop_block_device')
        key=f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
        return [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()
                if line.split()[2]==key]

    def external_mounts(self, image):
        info=Path(image['loop']).stat()
        require(stat.S_ISBLK(info.st_mode) and os.major(info.st_rdev)==7,'not_loop_block_device')
        key=f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
        return [line.split() for line in Path('/proc/1/mountinfo').read_text().splitlines()
                if line.split()[2]==key]

    def verify(self, image, *, mounted=False):
        info=self.identity(image);loop=image['loop']
        require(type(loop) is str and re.fullmatch(r'/dev/loop[0-9]+',loop),'invalid_loop_name')
        rows=self.related(image,self.loops())
        require(len(rows)==1,'ambiguous_loop_mapping')
        row=rows[0]
        require(row.get('name')==loop and row.get('back-file')==str(image['path'])
                and int(row.get('back-ino',-1))==info.st_ino
                and row.get('back-maj:min')==f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
                and int(row.get('offset',-1))==PARTITION_OFFSET
                and int(row.get('sizelimit',-1))==EXPANDED_SIZE-PARTITION_OFFSET
                and int(row.get('log-sec',-1))==512
                and row.get('ro')==image['ro'],'loop_mapping_changed')
        require(not self.external_mounts(image),'test_loop_mounted_outside_private_namespace')
        mounts=self.mounts(image)
        if mounted:
            require(len(mounts)==1 and mounts[0][4]==str(image['mount']), 'unexpected_mount')
            fields=mounts[0];separator=fields.index('-')
            require(fields[separator+1]=='exfat' and fields[separator+2]==loop
                    and {'nosuid','nodev','noexec', 'ro' if image['ro'] else 'rw'}
                    <= set(fields[5].split(',')), 'unsafe_mount_options')
        else:
            require(not mounts,'loop_still_mounted')

    def attach(self, image, *, ro=False):
        require(image['loop'] is None and not self.related(image,self.loops()),'image_already_attached')
        self.identity(image);image['ro']=ro
        args=[TOOLS['losetup'],'--find','--show','--nooverlap','--offset',str(PARTITION_OFFSET),
              '--sizelimit',str(EXPANDED_SIZE-PARTITION_OFFSET),'--sector-size','512']
        if ro:args.append('--read-only')
        args.extend(['--',str(image['path'])])
        image['loop']=self.command(args).stdout.strip()
        self.verify(image)

    def detach(self, image):
        self.verify(image)
        self.command([TOOLS['losetup'],'--detach',image['loop']])
        for _ in range(50):
            if not self.related(image,self.loops()):
                image['loop']=None;return
            time.sleep(0.1)
        raise RuntimeError('loop_detach_unconfirmed')

    def fsck(self, image):
        self.verify(image)
        return self.command([TOOLS['fsck'],'-n',image['loop']])

    def headers(self, image):
        self.identity(image)
        fd=os.open(image['path'],os.O_RDONLY|os.O_NOFOLLOW)
        try:
            require((lambda i:(i.st_dev,i.st_ino,i.st_size))(os.fstat(fd))==image['identity'],'opened_identity_changed')
            mbr,boot=os.pread(fd,512,0),os.pread(fd,512,PARTITION_OFFSET)
        finally:os.close(fd)
        validate_image_headers(mbr,boot,EXPANDED_SIZE)
        return mbr,boot

    def format(self, name):
        self.create(name);image=self.images[name]
        self.attach(image)
        try:
            self.verify(image)
            self.command([TOOLS['mkfs'],'-L','CLOUDSTICK',image['loop']])
            self.fsck(image)
            self.verify(image)
            dump=self.command([TOOLS['dump'],image['loop']])
            mbr,boot=self.headers(image)
            geometry={'image_size':EXPANDED_SIZE,'layout':image_layout(EXPANDED_SIZE),
                'partition_offset':int.from_bytes(boot[64:72],'little'),
                'volume_sectors':int.from_bytes(boot[72:80],'little'),
                'fat_offset':int.from_bytes(boot[80:84],'little'),
                'fat_sectors':int.from_bytes(boot[84:88],'little'),
                'cluster_heap_offset':int.from_bytes(boot[88:92],'little'),
                'cluster_count':int.from_bytes(boot[92:96],'little'),
                'root_cluster':int.from_bytes(boot[96:100],'little'),
                'volume_serial':boot[100:104].hex(),'sector_shift':boot[108],
                'cluster_shift':boot[109],'cluster_bytes':1 << (boot[108]+boot[109]),
                'volume_flags':int.from_bytes(boot[106:108],'little'),
                'mbr_sha256':hashlib.sha256(mbr).hexdigest()}
            root_offset=(PARTITION_OFFSET + geometry['cluster_heap_offset']*512
                         + (geometry['root_cluster']-2)*geometry['cluster_bytes'])
            require(PARTITION_OFFSET <= root_offset < EXPANDED_SIZE-geometry['cluster_bytes'],
                    'invalid_root_cluster_geometry')
            with image['path'].open('rb') as stream:
                stream.seek(root_offset);entries=stream.read(geometry['cluster_bytes'])
            labels=[entries[n+2:n+2+entries[n+1]*2].decode('utf-16-le')
                    for n in range(0,len(entries),32) if entries[n]==0x83 and entries[n+1]<=11]
            require(labels==['CLOUDSTICK'],'wrong_exfat_volume_label')
            geometry['volume_label']=labels[0]
            self.evidence['images'][name]['geometry']=geometry
            require(geometry['volume_flags']==0 and bool(dump.stdout),'filesystem_metadata_invalid')
        finally:
            if image['loop'] is not None:self.detach(image)

    @contextmanager
    def mounted(self, name, *, ro=False):
        image=self.images[name]
        target=Path(tempfile.mkdtemp(prefix=name+'-mount-',dir=self.root))
        self.path(target.name)
        target_info=target.lstat();target_identity=(target_info.st_dev,target_info.st_ino)
        require(target_info.st_uid==0 and stat.S_IMODE(target_info.st_mode)==0o700
                and not list(target.iterdir()),'unsafe_mount_target')
        self.attach(image,ro=ro)
        try:
            self.fsck(image);self.verify(image)
            self.command([TOOLS['mount'],'-t','exfat','-o',
                ('ro' if ro else 'rw')+',nosuid,nodev,noexec,uid=0,gid=0,fmask=0077,dmask=0077',
                image['loop'],str(target)])
            image['mount']=target
            self.verify(image,mounted=True)
            yield target
        finally:
            if image['mount'] is not None:
                self.verify(image,mounted=True)
                self.command([TOOLS['umount'],str(target)])
                image['mount']=None
                after=target.lstat()
                require((after.st_dev,after.st_ino)==target_identity and not list(target.iterdir()),
                        'mount_target_identity_changed')
            self.verify(image);self.fsck(image);self.headers(image);self.detach(image)

    def reject(self, name, operation, *, error=RuntimeError):
        try:operation()
        except error:
            self.results.append({'name':name,'result':'PASS','expected_refusal':True});self.save();return
        raise RuntimeError('expected_refusal_missing:'+name)

    def exercise(self):
        source=self.path('synthetic-source');source.mkdir(mode=0o700)
        (source/'nested'/'QA Δ').mkdir(mode=0o700,parents=True)
        (source/'empty-directory').mkdir(mode=0o700)
        samples={'empty.txt':b'', 'nested/QA Δ/报告-α.txt':'Synthetic QA only. No clinical data.\n'.encode(),
                 'nested/with spaces.csv':b'fixture,value\nsynthetic,42\n', 'one-MiB.bin':b'A'*MIB}
        for name,data in samples.items():(source/name).write_bytes(data)
        with (source/'large-64-MiB.bin').open('xb') as output:
            for _ in range(64):output.write(b'Z'*MIB)
            output.flush();os.fsync(output.fileno())
        original=tree(source);self.evidence['synthetic_manifest']=original;self.save()
        self.check('source-8gib-real-format-fsck-geometry',lambda:self.format('source'))
        self.check('destination-8gib-real-format-fsck-geometry',lambda:self.format('destination'))
        with self.mounted('source') as source_mount:
            copy_tree(source,source_mount/'files')
        with self.mounted('source',ro=True) as source_mount, self.mounted('destination') as destination:
            actual_source=source_mount/'files';candidate=destination/'migrated'
            self.check('file-migration-hashes-and-empty-directories',lambda:copy_tree(actual_source,candidate))
            self.reject('repeat-migration-refuses-overwrite',lambda:copy_tree(actual_source,candidate))
            partial=destination/'interrupted.incomplete'
            child=os.fork()
            if child==0:
                try:copy_tree(actual_source,partial,die_after_first_chunk=True)
                except BaseException:os._exit(74)
                os._exit(75)
            _,status=os.waitpid(child,0)
            require(os.waitstatus_to_exitcode(status)==73 and partial.exists()
                    and tree(partial)!=original,'interruption_not_observed')
            self.results.append({'name':'real-copy-process-interruption-retains-partial-and-source','result':'PASS'})
            self.reject('incomplete-candidate-collision',lambda:copy_tree(actual_source,partial))
            changed=destination/'changed-candidate'
            copy_tree(actual_source,changed);(changed/'unexpected.txt').write_bytes(b'retain for review')
            self.reject('changed-candidate-blocks-blind-rollback',
                        lambda:require(tree(changed)==original,'candidate_changed'))
            require(tree(actual_source)==original and tree(candidate)==original and tree(source)==original,
                    'retained_source_or_candidate_changed')
            self.evidence['destination_manifest']=tree(candidate)
            # Real RO exFAT source -> private ext4 staging, using the unchanged 3D engine.
            owner=StorageOwnership()
            owner.confirm_host_access(locally_unmounted=True,loop_detached=True)
            owner.confirm_host_release(clean_eject_observed=True)
            owner.confirm_pi_access(gadget_closed=True,io_drained=True,filesystem_clean=True)
            owner.begin_staging(loop_read_only=True,mount_read_only=True)
            batch,current=stage_changed_snapshot(actual_source,self.path('staging'),owner,space_policy=CaptureSpacePolicy())
            require(batch is not None and {item.relative:{'size':item.size,'sha256':item.sha256} for item in current}
                    == original['files'],'capture_manifest_mismatch')
            require(tree(batch.root/'files')['files']==original['files'],'capture_hash_mismatch')
            self.results.append({'name':'stage3d-real-ro-exfat-to-ext4-capture','result':'PASS'})
            self.save()
        self.controller_boundaries()
        self.check('corruption-fixture-real-format',lambda:self.format('corruption'))
        image=self.images['corruption'];self.identity(image)
        require(image['loop'] is None and not self.related(image,self.loops()),'corruption_fixture_attached')
        with image['path'].open('r+b',buffering=0) as output:
            for sector in (0,12):
                output.seek(PARTITION_OFFSET+sector*512+3);require(output.write(b'INVALID!')==8,'short_corruption_write')
            os.fsync(output.fileno())
        def metadata_hash():
            with image['path'].open('rb') as incoming:
                incoming.seek(PARTITION_OFFSET);return hashlib.sha256(incoming.read(24*512)).hexdigest()
        before=metadata_hash();self.attach(image,ro=True)
        try:
            self.verify(image)
            result=self.command([TOOLS['fsck'],'-n',image['loop']],allowed=tuple(range(256)))
            require(result.returncode!=0 and metadata_hash()==before,'corruption_not_refused_or_repaired')
            self.evidence['corruption']={'fsck_exit':result.returncode,'metadata_sha256_before':before,
                                         'metadata_sha256_after':metadata_hash()}
            self.results.append({'name':'corrupt-real-exfat-fsck-refuses-without-repair','result':'PASS'})
        finally:self.detach(image)
        self.check('enospc-fixture-real-format',lambda:self.format('low-space'))
        with self.mounted('low-space') as target:
            self.verify(self.images['low-space'],mounted=True)
            count=0;exhausted=False
            with (target/'bounded-fill.bin').open('xb',buffering=0) as output:
                try:
                    while count <= EXPANDED_SIZE:
                        n=output.write(b'F'*MIB);require(n is not None and n>0,'fill_short_write');count+=n
                    raise RuntimeError('fill_exceeded_image_bound')
                except OSError as error:
                    require(error.errno==errno.ENOSPC,'unexpected_fill_error');exhausted=True
            require(exhausted and count<=EXPANDED_SIZE,'bounded_enospc_not_observed')
            self.evidence['real_enospc']={'written_bytes':count,'errno':errno.ENOSPC,
                                         'available_after':shutil.disk_usage(target).free}
            self.reject('full-disposable-exfat-refuses-new-migration',lambda:copy_tree(source,target/'new-copy'))
            require(not (target/'new-copy').exists() and tree(source)==original,'low_space_source_or_publication_changed')
            self.results.append({'name':'bounded-real-exfat-enospc','result':'PASS'})
            self.save()

    def controller_boundaries(self):
        image=self.images['destination'];identity=image['identity']
        approval={'schema_version':2,'mass_storage_appliance_approved':True,'image_identity':list(identity),
                  'image_size_bytes':EXPANDED_SIZE,'image_layout':image_layout(EXPANDED_SIZE)}
        require(validate_image_approval(approval,identity)==EXPANDED_SIZE,'approval_contract_failed')
        self.reject('wrong-image-inode-refused',lambda:validate_image_approval(approval,(identity[0],identity[1]+1,identity[2])),error=BackupError)
        self.reject('wrong-image-capacity-refused',lambda:validate_image_approval(approval,(identity[0],identity[1],4*GIB)),error=BackupError)
        mbr,boot=self.headers(image);bad=bytearray(mbr);bad[458:462]=(1).to_bytes(4,'little')
        self.reject('unexpected-mbr-geometry-refused',lambda:validate_image_headers(bytes(bad),boot,EXPANDED_SIZE),error=BackupError)
        bad=bytearray(boot);bad[106]|=2
        self.reject('dirty-exfat-flags-refused',lambda:validate_image_headers(mbr,bytes(bad),EXPANDED_SIZE),error=BackupError)

    def finish(self, error=None):
        # Never guess cleanup ownership. Ambiguous devices remain for review.
        cleanup=[]
        for name,image in self.images.items():
            if image['loop'] is not None:
                cleanup.append({'name':name,'loop':image['loop'],'mount':str(image['mount']),
                                'state':'retained_for_review'})
            else:
                require(not self.related(image,self.loops()),'residual_test_loop')
        self.evidence.update(status='PASS' if error is None and not cleanup else 'STOP_REVIEW_REQUIRED',
                             error=error, residual_resources=cleanup,
                             finish_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        self.save()
        if not cleanup:
            uid=int(os.environ.get('SUDO_UID','0'));gid=int(os.environ.get('SUDO_GID','0'))
            os.chown(self.root/'evidence.json',uid,gid)
            os.chown(self.root,uid,gid)
        print(json.dumps({'status':self.evidence['status'],'report':str(self.root/'evidence.json'),
                          'passed_checks':len(self.results),'residual_resources':cleanup}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-stage3d2-only',action='store_true')
    args=parser.parse_args()
    if not args.approved_stage3d2_only:parser.error('explicit Stage 3D2 approval flag required')
    lab=Lab();lab.start()
    try:lab.exercise()
    except BaseException as error:
        lab.finish(type(error).__name__+': '+str(error));raise
    else:lab.finish()


if __name__=='__main__':main()
