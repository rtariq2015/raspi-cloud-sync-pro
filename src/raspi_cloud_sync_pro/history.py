"""Durable backup jobs and append-only attempt events, serialized by a worker lock."""

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import BackupError
from .staging import ReadyBatch


class History:
    def __init__(self, path: Path):
        self.path = Path(path).absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise BackupError("unsafe_history_path")
        self.connection = sqlite3.connect(str(self.path), timeout=5)
        os.chmod(self.path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA synchronous=FULL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.connection.close()
            raise BackupError("unsupported_history_schema")
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    destination TEXT NOT NULL,
                    batch_root TEXT NOT NULL,
                    relative TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    error_code TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(destination, relative, sha256)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES jobs(id),
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    error_code TEXT,
                    recorded_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS due_jobs ON jobs(destination, state, next_attempt);
                PRAGMA user_version=1;
            """)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def worker(self):
        """One engine worker per database; a killed process releases the OS lock."""
        fd = os.open(str(self.path) + ".worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BackupError("backup_worker_busy", retryable=True) from exc
            yield
        finally:
            os.close(fd)

    def enqueue(self, batch: ReadyBatch, destination: str, now: float) -> int:
        # Do not trust an in-memory manifest supplied by a caller.
        validated = ReadyBatch.load(batch.root)
        added = 0
        with self.connection:
            for item in validated.files:
                result = self.connection.execute("""
                    INSERT OR IGNORE INTO jobs
                    (destination,batch_root,relative,size,sha256,state,created_at,updated_at)
                    VALUES (?,?,?,?,?,'queued',?,?)
                """, (destination, str(validated.root), item.relative, item.size, item.sha256, now, now))
                if result.rowcount:
                    added += 1
                    self._event(result.lastrowid, "queued", 0, None, now)
        return added

    def _event(self, job_id, state, attempt, error_code, now):
        self.connection.execute(
            "INSERT INTO events(job_id,state,attempt,error_code,recorded_at) VALUES (?,?,?,?,?)",
            (job_id, state, attempt, error_code, now),
        )

    def update(self, job_id, state, now, *, error_code=None, next_attempt=0):
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET state=?,updated_at=?,error_code=?,next_attempt=? WHERE id=?",
                (state, now, error_code, next_attempt, job_id),
            )
            attempts = self.connection.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            self._event(job_id, state, attempts, error_code, now)

    def recover_interrupted(self, now, max_attempts):
        """Call only while holding worker(); never treat a crash as verification."""
        rows = self.connection.execute(
            "SELECT id,attempts,state FROM jobs WHERE state IN ('uploading','uploaded','verifying') "
            "OR (state IN ('queued','retry') AND attempts>=?)", (max_attempts,),
        ).fetchall()
        for row in rows:
            state = "retry" if row["attempts"] < max_attempts else "failed"
            error = "retry_exhausted" if row["state"] in ("queued", "retry") else "interrupted"
            self.update(row["id"], state, now, error_code=error, next_attempt=now if state == "retry" else 0)

    def claim(self, destination, now, max_attempts):
        # Explicit write transaction makes selection/claim atomic across connections.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute("""
                SELECT * FROM jobs WHERE destination=? AND state IN ('queued','retry')
                AND next_attempt<=? AND attempts<? ORDER BY id LIMIT 1
            """, (destination, now, max_attempts)).fetchone()
            if row is None:
                return None
            self.connection.execute(
                "UPDATE jobs SET state='uploading',attempts=attempts+1,updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            self._event(row["id"], "uploading", row["attempts"] + 1, None, now)
            return dict(self.connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def jobs(self):
        return [dict(row) for row in self.connection.execute("SELECT * FROM jobs ORDER BY id")]

    def events(self, job_id):
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,),
        )]


def read_history(path: Path, limit: int = 100):
    """Read existing history without creating a database or modifying its jobs."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise BackupError("invalid_history_limit")
    path = Path(path).absolute()
    if path.is_symlink():
        raise BackupError("unsafe_history_path")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise BackupError("unsupported_history_schema")
            return [dict(row) for row in connection.execute("""
                SELECT id,destination,relative,size,sha256,state,attempts,next_attempt,
                       error_code,created_at,updated_at FROM jobs ORDER BY id DESC LIMIT ?
            """, (limit,))]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise BackupError("history_read_error") from exc
