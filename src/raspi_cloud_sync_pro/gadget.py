"""Conservative removable mass-storage orchestration over an injected Linux backend.

The state journal records intent, never permission to replay a mount or attach.
No force-eject, timeout-based host release, automatic repair, or deletion exists.
"""

import fcntl
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import BackupError
from .staging import StagedFile, stage_changed_snapshot
from .storage import Ownership, StorageOwnership
from .maintenance import MaintenanceHold
from .capture_space import CaptureSpacePolicy


@dataclass(frozen=True)
class GadgetObservation:
    bound: bool
    backing: str
    configured: bool
    loops: tuple = ()
    mounts: tuple = ()
    power_ok: bool = False
    profile_ok: bool = False


class ControllerJournal:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "controller.json"
        self.data = None

    def load_capture(self, image, image_identity):
        """Read only our private local-capture baseline, never backup-owned data."""
        path = self.directory / 'capture-manifest.json'
        if os.path.lexists(self.directory / 'capture-manifest.pending'):
            raise BackupError('invalid_capture_manifest')
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4 * 1024 * 1024):
                raise ValueError()
            raw = os.read(fd, 4 * 1024 * 1024 + 1)
            if len(raw) != info.st_size:
                raise ValueError()
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError()
                    value[key] = item
                return value
            value = json.loads(raw, object_pairs_hook=unique)
            if (set(value) != {'schema_version', 'image', 'image_identity', 'files'}
                    or type(value['schema_version']) is not int or value['schema_version'] != 1
                    or value['image'] != str(image) or value['image_identity'] != list(image_identity)
                    or not isinstance(value['image_identity'], list)
                    or any(type(item) is not int or item < 0 for item in value['image_identity'])
                    or not isinstance(value['files'], list)):
                raise ValueError()
            files = []
            for item in value['files']:
                if not isinstance(item, dict) or set(item) != {'relative', 'size', 'sha256'}:
                    raise ValueError()
                files.append(StagedFile(**item))
            if len({item.relative for item in files}) != len(files) or files != sorted(files, key=lambda item: item.relative):
                raise ValueError()
            after, named = os.fstat(fd), path.lstat()
            signature = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if signature(info) != signature(after) or signature(info) != signature(named):
                raise ValueError()
            return tuple(files)
        except (OSError, ValueError, TypeError, KeyError, BackupError) as error:
            raise BackupError('invalid_capture_manifest') from error
        finally:
            os.close(fd)

    def save_capture(self, image, image_identity, files):
        """Commit only after ready publication and successful unmount/loop detach."""
        data = {'schema_version': 1, 'image': str(image), 'image_identity': list(image_identity),
                'files': [vars(item) for item in files]}
        raw = json.dumps(data, sort_keys=True, ensure_ascii=True).encode('ascii')
        if len(raw) > 4 * 1024 * 1024:
            raise BackupError('invalid_capture_manifest')
        path = self.directory / 'capture-manifest.pending'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(path, self.directory / 'capture-manifest.json')
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def locked(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise BackupError("unsafe_controller_state")
        fd = os.open(self.directory / "controller.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BackupError("gadget_controller_busy") from exc
            self.data = None
            if self.path.exists():
                try:
                    if self.path.is_symlink():
                        raise ValueError()
                    self.data = json.loads(self.path.read_text())
                    if self.data.get("schema_version") != 1 or not isinstance(self.data.get("phase"), str):
                        raise ValueError()
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    raise BackupError("invalid_controller_journal") from exc
            yield
        finally:
            os.close(fd)

    def save(self, phase, **details):
        data = {"schema_version": 1, "phase": phase, **details}
        temporary = self.directory / "controller.pending"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self.data = data


class GadgetController:
    """Hold session() for the lifetime of the controller, including idle polling."""

    def __init__(self, backend, state_directory: Path, *, maintenance_enabled=False, space_policy=None):
        if space_policy is not None and type(space_policy) is not CaptureSpacePolicy:
            raise BackupError('invalid_capture_space_policy')
        self.backend = backend
        self.journal = ControllerJournal(state_directory)
        self.ownership = StorageOwnership()
        self.armed = False
        self.started = False
        self.in_session = False
        self.loop = None
        self.capture_files = None
        self.maintenance = MaintenanceHold(state_directory, backend, enabled=maintenance_enabled)
        self._maintenance_release = None
        self.space_policy = space_policy

    @contextmanager
    def session(self):
        if self.in_session:
            raise BackupError("gadget_controller_busy")
        with self.journal.locked():
            if self.journal.data:
                self.loop = self.journal.data.get("loop")
            self.in_session = True
            try:
                yield self
            finally:
                self.armed = False
                self.started = False
                self.in_session = False

    def _require_session(self):
        if not self.in_session:
            raise BackupError("controller_session_required")

    def _observe(self):
        observation = self.backend.observe()
        if observation.profile_ok is not True:
            raise BackupError("gadget_profile_unverified")
        return observation

    def _power(self, observation):
        if observation.power_ok is not True:
            raise BackupError("power_status_unhealthy_or_unknown")

    def _detached(self, observation):
        if observation.bound or observation.backing or observation.loops or observation.mounts:
            raise BackupError("image_not_exclusive")
        self._power(observation)

    def _save(self, phase, **details):
        self.journal.save(phase, image=str(self.backend.image), loop=self.loop,
                          image_identity=list(getattr(self.backend, "image_identity", ()) or ()), **details)

    @contextmanager
    def _operation(self):
        self._require_session()
        try:
            yield
        except BaseException as error:
            self.ownership.fail()
            self.armed = False
            self.started = False
            if not self.journal.data or self.journal.data.get("image") == str(self.backend.image):
                try:
                    self._save("recovery_required")
                except OSError:
                    pass  # A full/broken state disk cannot authorize any further operation.
            if isinstance(error, OSError):
                raise BackupError("gadget_io_failed") from error
            raise

    def validate_cold_start(self, staging_directory):
        """Metadata eligibility only, under session lock; never eject permission."""
        self._require_session()
        self.validate_maintenance_start()
        previous = self.journal.data
        identity = list(getattr(self.backend, 'image_identity', ()) or ())
        if (self.started or self.armed or self.loop is not None or len(identity) != 3
                or any(type(item) is not int or item < 0 for item in identity)):
            raise BackupError('controller_recovery_required')
        if previous is not None and (set(previous) != {'schema_version', 'phase', 'image', 'image_identity', 'loop'}
                or type(previous['schema_version']) is not int or previous['schema_version'] != 1
                or previous['phase'] != 'host' or previous['image'] != str(self.backend.image)
                or not isinstance(previous['image_identity'], list)
                or any(type(item) is not int for item in previous['image_identity'])
                or previous['image_identity'] != identity or previous['loop'] is not None):
            raise BackupError('controller_recovery_required')
        if os.path.lexists(self.journal.directory / 'controller.pending'):
            raise BackupError('controller_recovery_required')
        root = Path(staging_directory) if staging_directory is not None else None
        if root is None or root.is_symlink() or not root.is_dir():
            raise BackupError('capture_handoff_recovery_required')
        with os.scandir(root) as entries:
            if next(entries, None) is not None:
                raise BackupError('capture_handoff_recovery_required')
        self.journal.load_capture(self.backend.image, identity)

    def start(self, *, adopt_host=False, cold_start=False, staging_directory=None):
        self._require_session()
        from .image_enrollment import guard_enrollment
        guard_enrollment(self.journal.directory, self.backend.image,
                         getattr(self.backend, 'image_identity', ()) or ())
        with self.maintenance.locked():
            self.maintenance.assert_normal()
            self._start(adopt_host=adopt_host, cold_start=cold_start, staging_directory=staging_directory)
            self.maintenance.open_session()

    def validate_maintenance_start(self):
        """Enforced even when new request admission is disabled."""
        self._require_session()
        from .image_enrollment import guard_enrollment
        guard_enrollment(self.journal.directory, self.backend.image,
                         getattr(self.backend, 'image_identity', ()) or ())
        with self.maintenance.locked():
            self.maintenance.assert_normal()

    def _start(self, *, adopt_host=False, cold_start=False, staging_directory=None):
        """Start unarmed; explicitly adopt loaded host media only with a new journal.

        Adoption records current host ownership without inspecting the filesystem.
        A later configured, loaded poll must still precede any accepted eject.
        """
        # Reject ambiguous cold metadata without rewriting an existing pending file.
        if cold_start is True and type(adopt_host) is bool and not adopt_host:
            self.validate_cold_start(staging_directory)
        with self._operation():
            if type(adopt_host) is not bool or type(cold_start) is not bool or adopt_host and cold_start:
                raise BackupError("invalid_host_adoption_request")
            observation = self._observe()
            previous = self.journal.data
            if previous and (previous.get("image") != str(self.backend.image)
                             or previous.get("image_identity") != list(getattr(self.backend, "image_identity", ()) or ())
                             or previous["phase"] != "host"):
                raise BackupError("controller_recovery_required")
            self.capture_files = self.journal.load_capture(self.backend.image, getattr(self.backend, 'image_identity', ()) or ())
            if cold_start:
                self._detached(observation)
                self._attach()
            elif previous:
                if not observation.bound or observation.backing != str(self.backend.image) \
                        or observation.loops or observation.mounts:
                    raise BackupError("controller_recovery_required")
                self._power(observation)
                self.ownership.confirm_host_access(locally_unmounted=True, loop_detached=True)
            elif adopt_host:
                if not observation.bound or not observation.configured \
                        or observation.backing != str(self.backend.image) \
                        or observation.loops or observation.mounts:
                    raise BackupError("host_adoption_unverified")
                self._power(observation)
                self.ownership.confirm_host_access(locally_unmounted=True, loop_detached=True)
                self._save("host")
            else:
                self._detached(observation)
                self._attach()
            self.started = True
            self.armed = False

    def _check_image(self):
        self._detached(self._observe())
        self._save("loop_attach_intent")
        self.loop = self.backend.attach_loop_read_only()
        self._save("checking_image")
        observation = self._observe()
        if observation.bound or observation.backing or observation.loops != (self.loop,) or observation.mounts:
            raise BackupError("image_not_exclusive")
        self.backend.verify_loop_read_only(self.loop)
        self.backend.check_filesystem(self.loop)  # fsck.exfat -n, exit zero only; never repair.

    def _detach_loop(self):
        self._save("loop_detach_intent")
        self.backend.detach_loop(self.loop)
        self.loop = None
        self._detached(self._observe())

    def _attach(self):
        with self.maintenance.locked():
            value = self.maintenance.record()
            if not (self._maintenance_release is not None and value is not None
                    and value['phase'] == 'releasing' and value['request_id'] == self._maintenance_release):
                self.maintenance.assert_normal()
            self._attach_checked()

    def _attach_checked(self):
        from .image_enrollment import guard_enrollment
        guard_enrollment(self.journal.directory, self.backend.image,
                         getattr(self.backend, 'image_identity', ()) or (), export=True)
        self._check_image()
        self._detach_loop()
        self._save("attach_intent")
        self.backend.attach_gadget()
        observation = self._observe()
        if not observation.bound or observation.backing != str(self.backend.image) \
                or observation.loops or observation.mounts:
            raise BackupError("gadget_attach_unverified")
        self.ownership.confirm_host_access(locally_unmounted=True, loop_detached=True)
        self._save("host")

    def _capture_digest(self):
        files = self.journal.load_capture(self.backend.image, getattr(self.backend, 'image_identity', ()) or ())
        raw = json.dumps(None if files is None else [vars(item) for item in files], sort_keys=True).encode('ascii')
        return hashlib.sha256(raw).hexdigest()

    def _empty_capture(self, directory):
        root = Path(directory)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700 or os.listdir(fd)
                    or (root.lstat().st_dev, root.lstat().st_ino) != (info.st_dev, info.st_ino)):
                raise BackupError('maintenance_capture_handoff_unfinished')
        finally:
            os.close(fd)

    def complete_maintenance(self, staging_directory):
        """Acknowledge hold only after the appliance's private batch handoff."""
        self._require_session()
        with self.maintenance.locked():
            value = self.maintenance.record()
            if value is None or value['phase'] == 'released':
                return False
            if (value['phase'] == 'pending' and self.started and self.loop is None
                    and self.maintenance.enabled and value['session_id'] == self.maintenance.session_id
                    and self.journal.data['phase'] == 'host'):
                return False  # Still waiting for a genuine Windows Eject.
            if (value['phase'] != 'captured' or value['session_id'] != self.maintenance.session_id
                    or not self.started or self.loop is not None or self.journal.data['phase'] != 'maintenance_captured'
                    or self.ownership.state != Ownership.RETURNABLE
                    or value['capture_sha256'] != self._capture_digest()):
                raise BackupError('maintenance_review_required')
            with self._operation():
                self._empty_capture(staging_directory)
                self._detached(self._observe())
                self._save('maintenance_active')
                self.maintenance.transition(value, 'active')
                self.started = False;self.armed = False
                return True

    def release_maintenance(self, request_id, staging_directory, *, approved=False):
        """Separate approved one-shot release, unchanged image only; never repair."""
        self._require_session()
        with self.maintenance.locked():
            self.maintenance.validate_challenge()
            value = self.maintenance.record()
            if (approved is not True or not self.maintenance.enabled or value is None
                    or value['phase'] != 'active' or value['request_id'] != request_id
                    or self.started or self.armed or self.loop is not None):
                raise BackupError('maintenance_release_refused')
            expected = {'schema_version':1, 'phase':'maintenance_active', 'image':str(self.backend.image),
                        'image_identity':list(getattr(self.backend, 'image_identity', ()) or ()), 'loop':None}
            if self.journal.data != expected or self._capture_digest() != value['capture_sha256']:
                raise BackupError('maintenance_release_refused')
            with self._operation():
                self._empty_capture(staging_directory)
                self._detached(self._observe())
                self.maintenance.transition(value, 'releasing')
                self._maintenance_release = request_id
                try:
                    self._attach()  # Existing read-only fsck and verified loop detach precede export.
                finally:
                    self._maintenance_release = None
                self.maintenance.transition(self.maintenance.record(), 'released')
                return 'released_host_owned'

    def poll(self, staging_directory: Path):
        """Observe an actual loaded→empty removable LUN in this uninterrupted session.

        Requires exclusive administration of this configfs gadget. Cable loss,
        startup with an empty LUN, quiet time and a journal are never eject evidence.
        Target-kernel behavior must be validated before production use.
        """
        with self._operation():
            if not self.started:
                raise BackupError("controller_not_started")
            with self.maintenance.locked():
                requested = self.maintenance.pending()
            observation = self._observe()
            self._power(observation)
            if observation.loops or observation.mounts or not observation.bound:
                raise BackupError("unexpected_device_ownership")
            if not observation.configured:
                self.armed = False
                return "waiting_for_host"
            if observation.backing == str(self.backend.image):
                self.armed = True
                return 'maintenance_pending' if requested else "waiting_for_host_eject"
            if observation.backing or not self.armed:
                raise BackupError("host_release_unproven")
            self.ownership.confirm_host_release(clean_eject_observed=True)
            self._save("eject_observed")
            self.armed = False
            with self.maintenance.locked():
                requested = self.maintenance.pending()
                if requested:
                    self.maintenance.transition(requested, 'capturing')
            self.backend.detach_gadget()  # Never clear a loaded LUN or use forced_eject.
            self._detached(self._observe())
            self._check_image()
            self.ownership.confirm_pi_access(gadget_closed=True, io_drained=True, filesystem_clean=True)
            self._save("mount_intent")
            self.backend.mount_read_only(self.loop)
            self.backend.verify_mount_read_only(self.loop)
            self.ownership.begin_staging(loop_read_only=True, mount_read_only=True)
            self._save("staging")
            capture_options = {}
            if self.space_policy is not None:
                if getattr(self.backend, 'image_identity', (None, None, None))[2] != self.space_policy.image_size_bytes:
                    raise BackupError('invalid_capture_space_policy')
                capture_options['space_policy'] = self.space_policy
            batch, current_files = stage_changed_snapshot(self.backend.mountpoint, staging_directory,
                                                          self.ownership, self.capture_files, **capture_options)
            self._save("unmount_intent", batch=str(batch.root) if batch else None)
            self.backend.unmount()
            observation = self._observe()
            if observation.bound or observation.backing or observation.mounts \
                    or observation.loops != (self.loop,):
                raise BackupError("unmount_unverified")
            self._detach_loop()
            self.ownership.finish_staging(unmounted=True, loop_detached=True)
            self._save("returnable", batch=str(batch.root) if batch else None)
            if current_files != self.capture_files:
                self.journal.save_capture(self.backend.image, getattr(self.backend, 'image_identity', ()) or (), current_files)
                self.capture_files = current_files
            with self.maintenance.locked():
                requested = self.maintenance.pending()
                if requested:
                    self._detached(self._observe())
                    self._save('maintenance_captured')
                    self.maintenance.transition(requested, 'captured', capture_sha256=self._capture_digest())
                else:
                    self._attach()
            return batch if batch else 'unchanged_snapshot'

    def recover_resources(self):
        """Explicit cleanup only; never reattach, repair, adopt or stage on recovery."""
        with self._operation():
            data = self.journal.data
            observation = self._observe()
            if not data or data.get("image") != str(self.backend.image) \
                    or data.get("image_identity") != list(getattr(self.backend, "image_identity", ()) or ()) \
                    or observation.bound or observation.backing:
                raise BackupError("recovery_ownership_unproven")
            self._power(observation)
            self.loop = data.get("loop")
            if observation.loops:
                if not self.loop or observation.loops != (self.loop,):
                    raise BackupError("recovery_ownership_unproven")
                self.backend.verify_loop_read_only(self.loop)
                if observation.mounts:
                    self.backend.verify_mount_read_only(self.loop)
                    self.backend.unmount()
                    if self._observe().mounts:
                        raise BackupError("unmount_unverified")
                self._detach_loop()
            else:
                self._detached(observation)
            self.ownership.fail()
            self._save("recovered_detached_needs_review")
            return "manual_review_required"
