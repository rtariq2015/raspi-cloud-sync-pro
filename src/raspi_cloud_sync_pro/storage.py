"""Fail-closed ownership model, not a gadget driver or proof of actual host release.

Only a future hardware adapter may supply the evidence accepted here. There are
deliberately no modprobe, mount, unmount, formatting, or device commands.
"""

from contextlib import contextmanager
from enum import Enum
from threading import RLock

from .errors import BackupError


class Ownership(str, Enum):
    UNKNOWN = "unknown"
    HOST = "host_owned"
    RELEASED = "host_released"
    PI = "pi_owned"
    STAGING = "staging"
    RETURNABLE = "returnable_to_host"
    ERROR = "error"


class StorageOwnership:
    def __init__(self):
        self._state = Ownership.UNKNOWN
        self._lock = RLock()

    @property
    def state(self):
        with self._lock:
            return self._state

    def _transition(self, expected, target, *evidence):
        with self._lock:
            if self._state not in expected or not all(item is True for item in evidence):
                self._state = Ownership.ERROR
                raise BackupError("storage_ownership_unproven")
            self._state = target

    def confirm_host_access(self, *, locally_unmounted: bool, loop_detached: bool):
        self._transition(
            (Ownership.UNKNOWN, Ownership.RETURNABLE), Ownership.HOST,
            locally_unmounted, loop_detached,
        )

    def confirm_host_release(self, *, clean_eject_observed: bool):
        self._transition((Ownership.HOST,), Ownership.RELEASED, clean_eject_observed)

    def confirm_pi_access(self, *, gadget_closed: bool, io_drained: bool, filesystem_clean: bool):
        self._transition(
            (Ownership.RELEASED,), Ownership.PI, gadget_closed, io_drained, filesystem_clean,
        )

    def begin_staging(self, *, loop_read_only: bool, mount_read_only: bool):
        self._transition((Ownership.PI,), Ownership.STAGING, loop_read_only, mount_read_only)

    def finish_staging(self, *, unmounted: bool, loop_detached: bool):
        self._transition((Ownership.STAGING,), Ownership.RETURNABLE, unmounted, loop_detached)

    def fail(self):
        with self._lock:
            self._state = Ownership.ERROR

    @contextmanager
    def read_access(self):
        """Serialize local transitions for the entire copy; no hardware lock is implied."""
        with self._lock:
            if self._state != Ownership.STAGING:
                raise BackupError("storage_read_not_permitted")
            try:
                yield
            except BaseException:
                self._state = Ownership.ERROR
                raise
