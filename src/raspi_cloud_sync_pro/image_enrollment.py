"""One explicitly approved 1-to-8 GiB enrollment; no formatting or cloud work.

An intent and immutable record snapshots precede every publication. Multiple
renames are NOT treated as atomic. All controller/export paths enforce this
latch. Recovery compares actual identities, records and read-only filesystem
contents; a saved phase is never permission to replay a device operation.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .errors import BackupError
from .mass_storage_image import LEGACY_SIZE, EXPANDED_SIZE, PARTITION_OFFSET, validate_image_approval, validate_image_headers
from .maintenance import MaintenanceHold
from .staging import _capture_inventory, relative_path

_PERMIT = ContextVar('image_enrollment_export', default=None)
_NAMES = {'config':'config.json', 'approval':'approval.json', 'controller':'controller.json',
          'capture':'capture-manifest.json', 'maintenance':'maintenance.json', 'session':'maintenance-session.json'}
_LIMIT = 16 * 1024 * 1024
_RESERVE = 2 * EXPANDED_SIZE + 5 * LEGACY_SIZE  # Candidate already allocated: remaining migration envelope.


def _require(value):
    if not value:
        raise BackupError('image_enrollment_review_required')


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value);value[key] = item
    return value


def _encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode('ascii')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _directory(path):
    path = Path(path)
    _require(path.is_absolute() and path.resolve(strict=True) == path)
    s = path.lstat()
    _require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid() and stat.S_IMODE(s.st_mode) == 0o700)


def _read(path):
    path = Path(path);_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                 and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size <= _LIMIT)
        raw = os.read(fd, _LIMIT + 1)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        _require(len(raw) == before.st_size and signature(before) == signature(os.fstat(fd)) == signature(path.lstat()))
        return raw
    finally:
        os.close(fd)


def _json(path):
    try:
        return json.loads(_read(path), object_pairs_hook=_unique)
    except (ValueError, TypeError) as error:
        raise BackupError('image_enrollment_review_required') from error


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _new(path, raw):
    _directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw);output.flush();os.fsync(output.fileno())
    _sync(path.parent)


def _rename(source, destination):
    """Linux no-clobber rename: preserve inodes, never overwrite an image."""
    library = ctypes.CDLL(None, use_errno=True)
    function = library.renameat2
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        raise OSError(ctypes.get_errno(), 'Enrollment rename refused')
    _sync(source.parent)
    if destination.parent != source.parent:
        _sync(destination.parent)


def _preserve_partial(path):
    if os.path.lexists(path):
        _read(path)  # Never move links or an untrusted file, even on explicit recovery.
        _rename(path, path.with_name(path.name + '.interrupted-' + uuid.uuid4().hex))


def _atomic(path, raw, *, recovery=False):
    temporary = path.with_name(path.name + '.enrollment.pending')
    if recovery:
        _preserve_partial(temporary)
    _new(temporary, raw)
    os.replace(temporary, path);_sync(path.parent)


def _image(path):
    path = Path(path);_directory(path.parent)
    s = path.lstat()
    _require(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid() and s.st_nlink == 1
             and stat.S_IMODE(s.st_mode) == 0o600 and s.st_size in (LEGACY_SIZE, EXPANDED_SIZE))
    return [s.st_dev, s.st_ino, s.st_size], s.st_mtime_ns


def _allocated(path):
    """Kernel allocation evidence, independent of the image's apparent length."""
    return Path(path).lstat().st_blocks * 512


def _intent(directory, image):
    directory = Path(directory);p = _json(directory / 'intent.json')
    fields = {'schema_version','transaction_id','image','candidate','archive','old_identity','new_identity',
              'old_mtime_ns','new_mtime_ns','hold_id','records','tree'}
    _require(type(p) is dict and set(p) == fields and type(p['schema_version']) is int and p['schema_version'] == 1
             and type(p['transaction_id']) is str and re.fullmatch('[0-9a-f]{32}', p['transaction_id'])
             and p['image'] == str(image) and type(p['hold_id']) is str and re.fullmatch('[0-9a-f]{32}', p['hold_id']))
    for key, size in [('old_identity', LEGACY_SIZE), ('new_identity', EXPANDED_SIZE)]:
        _require(type(p[key]) is list and len(p[key]) == 3 and all(type(i) is int and i >= 0 for i in p[key])
                 and p[key][2] == size)
    candidate, archive = Path(p['candidate']), Path(p['archive'])
    _require(candidate.parent == image.parent and candidate != image and candidate != archive
             and archive == image.with_name(image.name + '.rollback-' + p['transaction_id'])
             and all(type(p[k]) is int and p[k] >= 0 for k in ('old_mtime_ns','new_mtime_ns'))
             and type(p['records']) is dict and set(p['records']) == set(_NAMES))
    for name, hashes in p['records'].items():
        _require(type(hashes) is dict and set(hashes) == {'old','new'})
        for version in ('old','new'):
            _require(hashes[version] == _hash(_read(directory / version / _NAMES[name])))
    _require(type(p['tree']) is dict and set(p['tree']) == {'files','directories'})
    return p


def _status(directory, plan, *, recovery=False):
    path = directory / 'status.json'
    _require(recovery or not os.path.lexists(path.with_name(path.name + '.enrollment.pending')))
    if not os.path.lexists(path):
        return None
    value = _json(path)
    _require(type(value) is dict and set(value) == {'schema_version','transaction_id','intent_sha256','phase'}
             and type(value['schema_version']) is int and value['schema_version'] == 1
             and value['transaction_id'] == plan['transaction_id']
             and value['intent_sha256'] == _hash(_read(directory / 'intent.json'))
             and value['phase'] in ('prepared','publishing','committed','exported','rolled_back'))
    return value['phase']


def _native_clean(state):
    _require(not any(os.path.lexists(Path(state)/name) for name in
                    ('controller.pending','capture-manifest.pending','maintenance.json.pending','maintenance-session.json.pending')))


def guard_enrollment(state_directory, image, image_identity, *, export=False):
    """Fail closed before normal startup or any attach, including disabled admission."""
    directory, image = Path(state_directory) / 'image-enrollment', Path(image)
    if not os.path.lexists(directory):
        # A prepare can die before its complete intent is published. Such a
        # draft is never authority; preserve it and require explicit revalidation.
        _require(not any(Path(state_directory).glob('image-enrollment.draft-*')))
        return
    _native_clean(state_directory)
    plan = _intent(directory, image);phase = _status(directory, plan)
    identity = _image(image)[0]
    _require(not image_identity or list(image_identity) == identity)
    if phase == 'rolled_back':
        _require(not os.path.lexists(directory / 'first-export.json') and identity == plan['old_identity'])
        return
    if phase == 'exported':
        _require(identity == plan['new_identity'] and _json(directory / 'first-export.json') ==
                 {'transaction_id':plan['transaction_id'], 'new_identity':plan['new_identity']})
        return
    _require(export and _PERMIT.get() == (str(directory), plan['transaction_id'], tuple(plan['new_identity']))
             and identity == plan['new_identity'] and phase == 'committed'
             and _json(directory / 'first-export.json') ==
                 {'transaction_id':plan['transaction_id'], 'new_identity':plan['new_identity']})


def _directories(root):
    """Include empty directories and stable parent metadata omitted by file capture."""
    signatures = {};budget = [10000]
    def walk(fd, relative, depth):
        _require(depth <= 32)
        before = os.fstat(fd)
        signature = lambda s: (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        signatures[relative] = signature(before)
        for name in sorted(os.listdir(fd)):
            budget[0] -= 1;_require(budget[0] >= 0)
            path = relative_path(relative+'/'+name if relative else name)
            info = os.stat(name,dir_fd=fd,follow_symlinks=False)
            _require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=fd)
                try:
                    _require(signature(info) == signature(os.fstat(child)))
                    walk(child,path,depth+1)
                finally:
                    os.close(child)
        _require(signature(before) == signature(os.fstat(fd)))
    fd = os.open(root,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(fd,'',0)
        named = Path(root).lstat();current = os.fstat(fd)
        _require((named.st_dev,named.st_ino) == (current.st_dev,current.st_ino))
    finally:
        os.close(fd)
    return sorted(name for name in signatures if name),signatures


class ReadOnlyImageVerifier:
    """Real loop/mount/header/content checks; tests inject only the Linux boundary."""
    def __init__(self, backend_factory):
        self.factory = backend_factory

    def detached(self, paths):
        for path in paths:
            backend = self.factory(Path(path));seen = backend.observe()
            _require(seen.profile_ok is True and seen.power_ok is True and not seen.bound
                     and not seen.backing and not seen.loops and not seen.mounts)
            rows = backend.io.query(['losetup','--list','--json','--output','NAME,BACK-FILE,RO'])
            _require(rows.returncode == 0 and json.loads(rows.stdout)['loopdevices'] == [])

    def verify(self, path):
        path = Path(path);identity, stamp = _image(path);self.detached([path])
        backend = self.factory(path);loop = None;mounted = False
        try:
            loop = backend.attach_loop_read_only();backend.verify_loop_read_only(loop)
            backend.check_filesystem(loop)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                _require([before.st_dev,before.st_ino,before.st_size] == identity)
                validate_image_headers(os.pread(fd,512,0), os.pread(fd,512,PARTITION_OFFSET),identity[2])
                after = os.fstat(fd)
                _require((before.st_mtime_ns,before.st_ctime_ns) == (after.st_mtime_ns,after.st_ctime_ns))
            finally:
                os.close(fd)
            backend.mount_read_only(loop);backend.verify_mount_read_only(loop);mounted = True
            directories, before_directories = _directories(backend.mountpoint)
            files, fingerprints = _capture_inventory(backend.mountpoint, contents=True)
            _, after = _capture_inventory(backend.mountpoint, contents=False)
            after_directories, directory_signatures = _directories(backend.mountpoint)
            _require(fingerprints == after and directories == after_directories
                     and before_directories == directory_signatures)
            tree = {'files':[vars(item) for item in files], 'directories':directories}
        finally:
            if loop is not None:
                seen = backend.observe()
                _require(not seen.bound and not seen.backing and seen.loops == (loop,))
                if mounted:
                    backend.verify_mount_read_only(loop);backend.unmount()
                _require(not backend.observe().mounts)
                backend.verify_loop_read_only(loop);backend.detach_loop(loop)
        self.detached([path]);_require(_image(path) == (identity,stamp))
        return tree


class ImageEnrollment:
    def __init__(self, state_directory, image, candidate, config_path, approval_path, private_staging, backend_factory):
        self.state, self.image, self.candidate = Path(state_directory), Path(image), Path(candidate)
        self.config, self.approval, self.staging = Path(config_path), Path(approval_path), Path(private_staging)
        self.directory = self.state / 'image-enrollment';self.factory = backend_factory
        self.verifier = ReadOnlyImageVerifier(backend_factory)
        _require(self.candidate.parent == self.image.parent and self.candidate != self.image)
        self.targets = {'config':self.config, 'approval':self.approval,
                        **{key:self.state/name for key,name in _NAMES.items() if key not in ('config','approval')}}

    @classmethod
    def open(cls, state_directory, image, config_path, approval_path, private_staging, backend_factory):
        plan = _intent(Path(state_directory)/'image-enrollment', Path(image))
        return cls(state_directory,image,plan['candidate'],config_path,approval_path,private_staging,backend_factory)

    def checkpoint(self, name):
        """Fault-injection seam; production does nothing."""

    @contextmanager
    def _locked(self):
        from .gadget import ControllerJournal
        for path in (self.state,self.image.parent,self.config.parent,self.approval.parent,self.staging):
            _directory(path)
        _native_clean(self.state)
        # Taking a lock must work during the verified canonical-image rename gap.
        # No backend or device operation is needed until actual state is reconciled.
        maintenance = MaintenanceHold(self.state,None,enabled=True)
        with ControllerJournal(self.state).locked(), maintenance.locked():
            _require(not os.listdir(self.staging))
            yield maintenance

    def _space(self):
        _require(self.state.lstat().st_dev == self.image.parent.lstat().st_dev)
        v = os.statvfs(self.state)
        _require(not v.f_flag & os.ST_RDONLY and v.f_bavail*v.f_frsize >= _RESERVE
                 and v.f_favail >= 1024)

    def _set_status(self, plan, phase, *, recovery=False, directory=None):
        directory = self.directory if directory is None else directory
        value = {'schema_version':1,'transaction_id':plan['transaction_id'],
                 'intent_sha256':_hash(_read(directory/'intent.json')),'phase':phase}
        _atomic(directory/'status.json',_encode(value),recovery=recovery)

    def _records(self, plan, *, allow_missing=False):
        for key,path in self.targets.items():
            if not os.path.lexists(path):
                _require(allow_missing);continue
            _require(_hash(_read(path)) in plan['records'][key].values())

    def _positions(self, plan):
        old, new = [], []
        for path in (self.image,self.candidate,Path(plan['archive'])):
            if not os.path.lexists(path):
                continue
            identity,stamp = _image(path)
            if identity == plan['old_identity'] and stamp == plan['old_mtime_ns']:
                old.append(path)
            elif identity == plan['new_identity'] and stamp == plan['new_mtime_ns']:
                _require(_allocated(path) >= EXPANDED_SIZE)
                new.append(path)
            else:
                raise BackupError('image_enrollment_identity_changed')
        _require(len(old) == len(new) == 1)
        _require((old[0],new[0]) in ((self.image,self.candidate),
                 (Path(plan['archive']),self.candidate),(Path(plan['archive']),self.image)))
        return old[0],new[0]

    def _verify_plan(self, plan):
        old,new = self._positions(plan);self.verifier.detached([old,new])
        _require(self.verifier.verify(old) == self.verifier.verify(new) == plan['tree'])
        self._positions(plan)
        return old,new

    def prepare(self, hold_id, candidate_approval, *, approved=False):
        from .gadget import ControllerJournal
        _require(approved is True)
        with self._locked() as maintenance:
            _require(not os.path.lexists(self.directory));self._space()
            old_id,old_time = _image(self.image);new_id,new_time = _image(self.candidate)
            _require(old_id[2] == LEGACY_SIZE and new_id[2] == EXPANDED_SIZE and old_id[0] == new_id[0]
                     and old_id != new_id and _allocated(self.candidate) >= EXPANDED_SIZE
                     and type(candidate_approval) is dict and candidate_approval.get('schema_version') == 2)
            validate_image_approval(candidate_approval,new_id)
            old = {key:_read(path) for key,path in self.targets.items()}
            values = {key:json.loads(raw,object_pairs_hook=_unique) for key,raw in old.items()}
            config = values['config']
            _require(set(config) == {'schema_version','enabled','udc','serial','approval_file'}
                     and type(config['schema_version']) is int and config['schema_version'] == 1 and config['enabled'] is True
                     and config['approval_file'] == str(self.approval))
            validate_image_approval(values['approval'],old_id)
            maintenance.backend = self.factory(self.image);maintenance.backend._pin_image()
            with maintenance.locked():
                hold = maintenance.record();challenge = maintenance.validate_challenge()
            _require(hold is not None and hold['phase'] == 'active' and hold['request_id'] == hold_id
                     and challenge is not None and challenge['session_id'] == hold['session_id']
                     and values['controller'] == {'schema_version':1,'phase':'maintenance_active',
                         'image':str(self.image),'image_identity':old_id,'loop':None})
            inventory = ControllerJournal(self.state).load_capture(self.image,old_id)
            _require(inventory is not None and hold['capture_sha256'] == _hash(_encode([vars(f) for f in inventory])))
            self.verifier.detached([self.image,self.candidate])
            tree = self.verifier.verify(self.image)
            _require(tree == self.verifier.verify(self.candidate) and tree['files'] == [vars(f) for f in inventory])
            _require(_image(self.image) == (old_id,old_time) and _image(self.candidate) == (new_id,new_time))
            identifier = uuid.uuid4().hex;archive = self.image.with_name(self.image.name+'.rollback-'+identifier)
            _require(not os.path.lexists(archive))
            base = {'schema_version':1,'image':str(self.image),'image_identity':new_id}
            new = {'config':_encode({**config,'schema_version':2,'capture_space_policy':'capacity-reserve-v1'}),
                   'approval':_encode(candidate_approval),'controller':_encode({**base,'phase':'enrolled_detached','loop':None}),
                   'capture':_encode({**base,'files':tree['files']}),
                   'maintenance':_encode({**hold,'image_identity':new_id,'phase':'enrolled'}),
                   'session':_encode({**challenge,'image_identity':new_id})}
            # Publish only a complete, self-describing prepare. A crash before
            # this final directory rename cannot have changed an active record.
            draft = self.state/('image-enrollment.draft-'+identifier)
            draft.mkdir(mode=0o700);_sync(self.state)
            for version,records in [('old',old),('new',new)]:
                folder = draft/version;folder.mkdir(mode=0o700);_sync(draft)
                for key,raw in records.items():
                    _new(folder/_NAMES[key],raw)
                    self.checkpoint('prepare:archive:'+version+':'+key)
            plan = {'schema_version':1,'transaction_id':identifier,'image':str(self.image),
                    'candidate':str(self.candidate),'archive':str(archive),'old_identity':old_id,'new_identity':new_id,
                    'old_mtime_ns':old_time,'new_mtime_ns':new_time,'hold_id':hold_id,'tree':tree,
                    'records':{key:{'old':_hash(old[key]),'new':_hash(new[key])} for key in _NAMES}}
            _new(draft/'intent.json',_encode(plan));self.checkpoint('prepare:intent')
            self._set_status(plan,'prepared',directory=draft);self.checkpoint('prepare:status')
            _rename(draft,self.directory)
            self.checkpoint('prepared')
            return identifier

    def _load(self, transaction_id, *, recovery=False):
        plan = _intent(self.directory,self.image)
        _require(transaction_id == plan['transaction_id'] and self.candidate == Path(plan['candidate']))
        phase = _status(self.directory,plan,recovery=recovery)
        return plan,phase

    def _publish(self, plan, *, recovery=False, rollback=False):
        self._records(plan,allow_missing=recovery)
        for key,path in self.targets.items():
            self._positions(plan)
            raw = _read(self.directory/('old' if rollback else 'new')/_NAMES[key])
            _atomic(path,raw,recovery=recovery)
            self.checkpoint(('rollback:' if rollback else '')+'record:'+key)

    def _commit(self, plan, *, recovery=False):
        _require(not os.path.lexists(self.directory/'first-export.json'))
        self._space();self._records(plan,allow_missing=recovery);old,new = self._verify_plan(plan)
        self._set_status(plan,'publishing',recovery=recovery)
        if old == self.image:
            _rename(old,Path(plan['archive']));self.checkpoint('original_archived')
        self._positions(plan);self.verifier.detached([Path(plan['archive']),new])
        if new == self.candidate:
            _rename(new,self.image);self.checkpoint('candidate_installed')
        self._positions(plan);self._publish(plan,recovery=recovery)
        self._verify_plan(plan);self._set_status(plan,'committed',recovery=recovery)
        self.checkpoint('committed')
        return 'enrolled_detached'

    def commit(self, transaction_id, *, approved=False):
        _require(approved is True)
        with self._locked():
            plan,phase = self._load(transaction_id)
            _require(phase == 'prepared')
            return self._commit(plan)

    def recover(self, transaction_id, *, action, approved=False):
        _require(approved is True and action in ('commit','rollback','host'))
        with self._locked():
            plan,phase = self._load(transaction_id,recovery=True)
            if action == 'host':
                return self._recover_host(plan)
            _require(not os.path.lexists(self.directory/'first-export.json') and phase not in ('exported','rolled_back'))
            if action == 'commit':
                return self._commit(plan,recovery=True)
            self._records(plan,allow_missing=True);old,new = self._verify_plan(plan)
            if new == self.image:
                _rename(new,self.candidate)
            if old != self.image:
                _rename(old,self.image)
            self._positions(plan);self._publish(plan,recovery=True,rollback=True)
            self._verify_plan(plan);self._set_status(plan,'rolled_back',recovery=True)
            self.checkpoint('rolled_back')
            return 'original_restored_maintenance_active'

    def activate(self, controller, transaction_id, *, approved=False):
        _require(approved is True and controller.in_session and not controller.started and not controller.armed)
        _native_clean(self.state)
        with controller.maintenance.locked():
            plan,phase = self._load(transaction_id)
            _require(phase == 'committed' and controller.journal.directory == self.state
                     and controller.backend.image == self.image and not os.path.lexists(self.directory/'first-export.json'))
            self._space();self._records(plan);self._verify_plan(plan)
            _require(all(_read(path) == _read(self.directory/'new'/_NAMES[key]) for key,path in self.targets.items()))
            controller.backend._pin_image();controller._empty_capture(self.staging)
            _require(list(controller.backend.image_identity) == plan['new_identity'])
            self._space()
            # Irreversible conservative boundary: even a failed attach may have exposed the image.
            _new(self.directory/'first-export.json',_encode({'transaction_id':transaction_id,'new_identity':plan['new_identity']}))
            self.checkpoint('export_intent')
            token = _PERMIT.set((str(self.directory),transaction_id,tuple(plan['new_identity'])))
            try:
                with controller._operation():
                    controller._attach_checked()
                    hold = controller.maintenance.record()
                    _require(hold['phase'] == 'enrolled' and hold['request_id'] == plan['hold_id'])
                    controller.maintenance.transition(hold,'released')
                    self._set_status(plan,'exported');self.checkpoint('exported')
            finally:
                _PERMIT.reset(token)
            return 'exported_host_owned_restart_requires_separate_action'

    def _recover_host(self, plan):
        from .gadget import ControllerJournal
        _require(_json(self.directory/'first-export.json') ==
                 {'transaction_id':plan['transaction_id'],'new_identity':plan['new_identity']})
        _require(_image(self.image)[0] == plan['new_identity'] and _image(Path(plan['archive']))[0] == plan['old_identity'])
        for key in ('config','approval','capture','session'):
            _require(_read(self.targets[key]) == _read(self.directory/'new'/_NAMES[key]))
        hold = _json(self.targets['maintenance']);expected = _json(self.directory/'new'/_NAMES['maintenance'])
        _require(hold == expected or hold == {**expected,'phase':'released'})
        journal = _json(self.targets['controller'])
        _require(set(journal) == {'schema_version','phase','image','image_identity','loop'}
                 and journal['image'] == str(self.image) and journal['image_identity'] == plan['new_identity']
                 and journal['loop'] is None and journal['phase'] in ('host','attach_intent','recovery_required','enrolled_detached'))
        backend = self.factory(self.image);seen = backend.observe()
        _require(seen.profile_ok is True and seen.power_ok is True and seen.bound and seen.configured
                 and seen.backing == str(self.image) and not seen.loops and not seen.mounts)
        # Observe/adopt only. Never replay attach, mount or a journal's claimed host release.
        _atomic(self.targets['controller'],_encode({**journal,'phase':'host'}),recovery=True)
        _atomic(self.targets['maintenance'],_encode({**expected,'phase':'released'}),recovery=True)
        self._set_status(plan,'exported',recovery=True)
        return 'observed_host_owned_restart_requires_separate_action'
