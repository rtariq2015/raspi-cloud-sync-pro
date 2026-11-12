"""One bounded attempt at a time, with independent upload and verification states."""

import time
import sqlite3
from pathlib import Path
from typing import Protocol

from .errors import BackupError
from .history import History
from .network import Connectivity, upload_allowed
from .staging import ReadyBatch, StagedFile


class Provider(Protocol):
    destination_id: str

    def upload(self, batch: ReadyBatch, item: StagedFile) -> None:
        ...

    def verify(self, batch: ReadyBatch, item: StagedFile) -> None:
        """Return only after checking remote content against item.sha256."""
        ...


class BackupEngine:
    def __init__(self, history: History, *, max_attempts=3, base_delay=30, max_delay=3600):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20 \
                or type(base_delay) is not int or type(max_delay) is not int \
                or not 1 <= base_delay <= max_delay:
            raise ValueError("invalid retry policy")
        self.history = history
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    def run_once(self, provider: Provider, *, now=None, connectivity=Connectivity.UNKNOWN,
                 metered=False, allow_metered=False):
        """Process one eligible file. No sleeps, implicit network setup, or deletion."""
        fixed_now = now
        clock = lambda: time.time() if fixed_now is None else fixed_now
        now = clock()
        if not upload_allowed(connectivity, metered=metered, allow_metered=allow_metered):
            return "paused"
        with self.history.worker():
            try:
                self.history.recover_interrupted(now, self.max_attempts)
                job = self.history.claim(provider.destination_id, now, self.max_attempts)
            except sqlite3.Error as exc:
                raise BackupError("history_write_failed") from exc
            if job is None:
                return None
            try:
                batch = ReadyBatch.load(Path(job["batch_root"]), verify_contents=False)
                item = next((item for item in batch.files if item.relative == job["relative"]), None)
                if item is None or (item.size, item.sha256) != (job["size"], job["sha256"]):
                    raise BackupError("staged_manifest_changed")
                batch.check_file(item)
                provider.upload(batch, item)
                self.history.update(job["id"], "uploaded", clock())
                self.history.update(job["id"], "verifying", clock())
                provider.verify(batch, item)
                batch.check_file(item)
                self.history.update(job["id"], "verified", clock())
                return "verified"
            except sqlite3.Error as exc:
                # Leave the last persisted state for recovery; do not claim success.
                raise BackupError("history_write_failed") from exc
            except BackupError as exc:
                error = exc
            except OSError:
                error = BackupError("local_io_error")
            except Exception:
                # Unexpected adapter failures cannot become successful backups or leak secrets.
                error = BackupError("unexpected_provider_error")
            retry = error.retryable and job["attempts"] < self.max_attempts
            state = "retry" if retry else "failed"
            delay = min(self.max_delay, self.base_delay * 2 ** (job["attempts"] - 1))
            failed_at = clock()
            try:
                self.history.update(job["id"], state, failed_at, error_code=error.code,
                                    next_attempt=failed_at + delay if retry else 0)
            except sqlite3.Error as exc:
                raise BackupError("history_write_failed") from exc
            return state
