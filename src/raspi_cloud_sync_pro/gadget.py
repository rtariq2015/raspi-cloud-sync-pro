"""Conservative removable mass-storage orchestration over an injected Linux backend.

The state journal records intent, never permission to replay a mount or attach.
No force-eject, timeout-based host release, automatic repair, or deletion exists.
"""

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import BackupError
from .staging import stage_snapshot
from .storage import StorageOwnership


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

    def __init__(self, backend, state_directory: Path):
        self.backend = backend
        self.journal = ControllerJournal(state_directory)
        self.ownership = StorageOwnership()
        self.armed = False
        self.started = False
        self.in_session = False
        self.loop = None

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

    def start(self):
        """New controller sessions never reuse an old eject observation."""
        with self._operation():
            observation = self._observe()
            previous = self.journal.data
            if previous and (previous.get("image") != str(self.backend.image)
                             or previous.get("image_identity") != list(getattr(self.backend, "image_identity", ()) or ())
                             or previous["phase"] != "host"):
                raise BackupError("controller_recovery_required")
            if previous:
                if not observation.bound or observation.backing != str(self.backend.image) \
                        or observation.loops or observation.mounts:
                    raise BackupError("controller_recovery_required")
                self._power(observation)
                self.ownership.confirm_host_access(locally_unmounted=True, loop_detached=True)
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

    def poll(self, staging_directory: Path):
        """Observe an actual loaded→empty removable LUN in this uninterrupted session.

        Requires exclusive administration of this configfs gadget. Cable loss,
        startup with an empty LUN, quiet time and a journal are never eject evidence.
        Target-kernel behavior must be validated before production use.
        """
        with self._operation():
            if not self.started:
                raise BackupError("controller_not_started")
            observation = self._observe()
            self._power(observation)
            if observation.loops or observation.mounts or not observation.bound:
                raise BackupError("unexpected_device_ownership")
            if not observation.configured:
                self.armed = False
                return "waiting_for_host"
            if observation.backing == str(self.backend.image):
                self.armed = True
                return "waiting_for_host_eject"
            if observation.backing or not self.armed:
                raise BackupError("host_release_unproven")
            self.ownership.confirm_host_release(clean_eject_observed=True)
            self._save("eject_observed")
            self.armed = False
            self.backend.detach_gadget()  # Never clear a loaded LUN or use forced_eject.
            self._detached(self._observe())
            self._check_image()
            self.ownership.confirm_pi_access(gadget_closed=True, io_drained=True, filesystem_clean=True)
            self._save("mount_intent")
            self.backend.mount_read_only(self.loop)
            self.backend.verify_mount_read_only(self.loop)
            self.ownership.begin_staging(loop_read_only=True, mount_read_only=True)
            self._save("staging")
            batch = stage_snapshot(self.backend.mountpoint, staging_directory, self.ownership)
            self._save("unmount_intent", batch=str(batch.root))
            self.backend.unmount()
            observation = self._observe()
            if observation.bound or observation.backing or observation.mounts \
                    or observation.loops != (self.loop,):
                raise BackupError("unmount_unverified")
            self._detach_loop()
            self.ownership.finish_staging(unmounted=True, loop_detached=True)
            self._save("returnable", batch=str(batch.root))
            self._attach()
            return batch

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
