"""Read-only, credential-free projection of the existing backup worker.

The optional reporter runs as the backup user; the web user reads only its
bounded report. Neither component invokes rclone or opens its configuration.
"""

import argparse
import json
import math
import os
import sqlite3
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from .config import _unique_object
from .errors import BackupError
from .staging import open_regular


REPORT_NAME = "backup-status.json"
MAX_REPORT = 131072
STATES = ("queued", "retry", "uploading", "uploaded", "verifying", "verified", "failed")
PROVIDERS = {"google_drive_rclone": "Google Drive", "koofr_webdav": "Koofr WebDAV",
             "dropbox_rclone": "Dropbox"}
ERRORS = {
    "rclone_command_failed": "The cloud operation failed. Check authorization or connectivity privately.",
    "rclone_timeout": "The cloud operation timed out; a retry may be queued.",
    "rclone_or_config_missing": "The backup executable or private configuration is unavailable.",
    "rclone_execution_error": "The backup executable could not complete the operation.",
    "insecure_rclone_config": "The private backup configuration needs administrator review.",
    "provider_disabled": "This backup destination is disabled.",
    "verification_mismatch": "The downloaded backup did not match the local checksum.",
    "staged_manifest_changed": "The retained file no longer matches its capture record.",
    "local_io_error": "A local storage operation failed. Retained files need review.",
    "unexpected_provider_error": "The backup operation failed unexpectedly.",
    "interrupted": "An earlier backup attempt was interrupted.",
    "backup_error": "The backup service recorded an error. Private administrator review is required.",
}


def _number(value, *, integer=False):
    return type(value) is int and 0 <= value <= 2 ** 63 - 1 if integer else \
        type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2 ** 53


def _directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe_status_directory")
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid(), path.lstat().st_uid)
                or (info.st_mode & 0o022 and not sticky)):
            raise ValueError("unsafe_status_directory")
    return path


def empty_status(state="not_connected"):
    return {"state": state, "observed_at": None, "age_seconds": None,
            "service": {"state": "unknown"},
            "history": {"state": "unavailable", "total_jobs": None, "counts": None},
            "google_drive": {"state": "unknown", "verified_jobs": None,
                             "last_verified_at": None, "current_access": "not_tested"},
            "destinations": [], "jobs": [], "worker": None, "byte_progress_available": False}


def service_observation():
    """Fixed read-only systemd query, exclusively for the separate reporter."""
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", "raspi-cloud-mass-storage-backup.service",
             "--property=ActiveState,SubState"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, text=True)
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        active, sub = fields.get("ActiveState"), fields.get("SubState")
        if (active, sub) == ("active", "running"):
            return {"state": "running"}
        if active == "failed":
            return {"state": "failed"}
        if active == "inactive":
            return {"state": "stopped"}
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return {"state": "unknown"}


def _provider(destination):
    return PROVIDERS.get(destination.split("/", 1)[0], "Other storage") if isinstance(destination, str) else "Other storage"


def _history(path, now):
    """One bounded, read-only SQLite snapshot; never read filenames or tokens."""
    path = Path(path)
    _directory(path.parent)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o022:
        raise ValueError("unsafe_status_history")
    identity = (before.st_dev, before.st_ino)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
    try:
        deadline = time.monotonic() + 1
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("unsupported_status_history")
        counts = dict.fromkeys(STATES, 0)
        for state, count in connection.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state"):
            if state not in counts or not _number(count, integer=True):
                raise ValueError("invalid_status_history")
            counts[state] = count
        # Group by provider, never publish rclone remote names, paths or folder IDs.
        providers = connection.execute("SELECT destination FROM jobs GROUP BY destination LIMIT 65").fetchall()
        if len(providers) > 64:
            raise ValueError("status_destination_limit")
        verified, last = connection.execute(
            "SELECT COUNT(*),MAX(updated_at) FROM jobs WHERE state='verified' "
            "AND substr(destination,1,20)='google_drive_rclone/'").fetchone()
        # The exact prefix is 20 characters including its slash.
        jobs = []
        for row in connection.execute(
                "SELECT id,destination,state,attempts,size,updated_at,next_attempt,error_code "
                "FROM jobs ORDER BY updated_at DESC,id DESC LIMIT 100"):
            ident, destination, state, attempts, size, updated, retry, error = row
            if (state not in STATES or not _number(ident, integer=True) or ident == 0
                    or not _number(attempts, integer=True) or not _number(size, integer=True)
                    or not _number(updated) or updated > now + 30 or not _number(retry)):
                raise ValueError("invalid_status_job")
            code = error if isinstance(error, str) and error in ERRORS else "backup_error" if error is not None else None
            jobs.append({"id": ident, "label": "Backup #" + str(ident), "destination": _provider(destination),
                         "state": state, "attempts": attempts, "size": size, "updated_at": updated,
                         "next_attempt": retry, "error_code": code,
                         "error_message": ERRORS.get(code)})
        after = path.lstat()
        if identity != (after.st_dev, after.st_ino) or stat.S_ISLNK(after.st_mode):
            raise ValueError("status_history_changed")
        if not _number(verified, integer=True) or (last is not None and (not _number(last) or last > now + 30)):
            raise ValueError("invalid_status_history")
        labels = sorted({_provider(row[0]) for row in providers})
        return {"history": {"state": "available", "total_jobs": sum(counts.values()), "counts": counts},
                "google_drive": {"state": "historically_verified" if verified else "recorded" if "Google Drive" in labels else "unknown",
                                 "verified_jobs": verified, "last_verified_at": last, "current_access": "not_tested"},
                "destinations": [{"label": label} for label in labels], "jobs": jobs}
    finally:
        connection.close()


def observe_status(history=None, worker_state=None, *, now=None, service_reader=None):
    """Collect metadata only. Dashboard fallback deliberately does not query systemd."""
    from .dashboard import worker_snapshot

    now = time.time() if now is None else now
    report = empty_status("current" if history is not None or worker_state is not None else "not_connected")
    if report["state"] == "not_connected":
        return report
    report.update(observed_at=now, age_seconds=0, worker=worker_snapshot(worker_state, now=now))
    if service_reader is not None:
        try:
            observed = service_reader()
            if observed in ({"state": value} for value in ("running", "stopped", "failed", "unknown")):
                report["service"] = observed
        except Exception:
            pass
    if history is not None:
        try:
            report.update(_history(history, now))
        except (OSError, ValueError, TypeError, OverflowError, sqlite3.Error):
            pass
    return report


def publish_status(directory, report):
    """Publish one metadata file; the dashboard group never gains source access."""
    root = _directory(directory)
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o027:
        raise ValueError("unsafe_status_output")
    raw = json.dumps({"schema_version": 1, **report}, allow_nan=False).encode()
    if len(raw) > MAX_REPORT:
        raise ValueError("status_report_too_large")
    fd, name = tempfile.mkstemp(prefix=".backup-status-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / REPORT_NAME)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _validate(report, now):
    """Strict allowlist: even a compromised report cannot relay arbitrary strings."""
    expected = set(empty_status()) | {"schema_version"}
    if (not isinstance(report, dict) or set(report) != expected or type(report["schema_version"]) is not int
            or report["schema_version"] != 1 or report["state"] != "current"
            or not _number(report["observed_at"]) or report["observed_at"] > now + 30
            or report["age_seconds"] != 0 or type(report["age_seconds"]) is not int
            or report["byte_progress_available"] is not False
            or report["service"] not in ({"state": x} for x in ("running", "stopped", "failed", "unknown"))):
        raise ValueError("invalid_status_report")
    h, g = report["history"], report["google_drive"]
    if (not isinstance(h, dict) or set(h) != {"state", "total_jobs", "counts"}
            or not isinstance(g, dict) or set(g) != {"state", "verified_jobs", "last_verified_at", "current_access"}
            or g["current_access"] != "not_tested"):
        raise ValueError("invalid_status_report")
    if h["state"] == "unavailable":
        if h != empty_status()["history"] or g != empty_status()["google_drive"] or report["jobs"] or report["destinations"]:
            raise ValueError("invalid_status_report")
    elif (h["state"] != "available" or not isinstance(h["counts"], dict) or set(h["counts"]) != set(STATES)
          or not all(_number(x, integer=True) for x in h["counts"].values())
          or not _number(h["total_jobs"], integer=True) or sum(h["counts"].values()) != h["total_jobs"]
          or g["state"] not in ("unknown", "recorded", "historically_verified")
          or not _number(g["verified_jobs"], integer=True) or g["verified_jobs"] > h["counts"]["verified"]
          or (g["last_verified_at"] is not None and (not _number(g["last_verified_at"]) or g["last_verified_at"] > now + 30))
          or (g["state"] == "historically_verified") != (g["verified_jobs"] > 0)
          or (g["last_verified_at"] is not None) != (g["verified_jobs"] > 0)):
        raise ValueError("invalid_status_report")
    labels = set(PROVIDERS.values()) | {"Other storage"}
    if (not isinstance(report["destinations"], list) or len(report["destinations"]) > 4
            or any(not isinstance(x, dict) or set(x) != {"label"} or x["label"] not in labels for x in report["destinations"])
            or not isinstance(report["jobs"], list) or len(report["jobs"]) > 100):
        raise ValueError("invalid_status_report")
    fields = {"id", "label", "destination", "state", "attempts", "size", "updated_at", "next_attempt", "error_code", "error_message"}
    for job in report["jobs"]:
        if (not isinstance(job, dict) or set(job) != fields or not _number(job["id"], integer=True) or job["id"] == 0
                or job["label"] != "Backup #" + str(job["id"]) or job["destination"] not in labels
                or job["state"] not in STATES or not all(_number(job[k], integer=True) for k in ("attempts", "size"))
                or not all(_number(job[k]) for k in ("updated_at", "next_attempt")) or job["updated_at"] > now + 30
                or (job["error_code"] is not None and job["error_code"] not in ERRORS)
                or job["error_message"] != ERRORS.get(job["error_code"])):
            raise ValueError("invalid_status_report")
    # Worker report is itself a fixed projection; no original destinations appear.
    w = report["worker"]
    if w is not None:
        from .dashboard import worker_snapshot
        if not isinstance(w, dict) or set(w) != set(worker_snapshot(None)):
            raise ValueError("invalid_status_report")
        if (w["state"] not in ("not_connected", "unavailable", "invalid", "stale", "error", "observed")
                or w["cloud_accounts_verified"] is not False
                or any(w[k] is not None and not _number(w[k], integer=True) for k in ("age_seconds", "pending_jobs", "failed_jobs", "destination_count"))
                or (w["updated_at"] is not None and (not _number(w["updated_at"]) or w["updated_at"] > now + 30))
                or any(w[k] is not None and type(w[k]) is not bool for k in ("network_policy_allows_upload", "reconciliation_pending"))
                or not isinstance(w["outcomes"], list) or len(w["outcomes"]) > 8
                or any(x not in (None, "verified", "retry", "paused", "failed") for x in w["outcomes"])):
            raise ValueError("invalid_status_report")
    age = max(0, int(now - report["observed_at"]))
    if w is not None and w["updated_at"] is not None:
        w = {**w, "age_seconds": max(0, int(now - w["updated_at"]))}
        if w["state"] in ("observed", "stale"):
            w["state"] = "stale" if w["age_seconds"] > 300 else "observed"
    return {k: v for k, v in {**report, "state": "stale" if age > 60 else "current", "age_seconds": age,
                              "worker": w, "service": {"state": "unknown"} if age > 60 else report["service"]}.items()
            if k != "schema_version"}


def read_status(directory, *, now=None):
    if directory is None:
        return empty_status()
    now = time.time() if now is None else now
    try:
        root = _directory(directory)
        with open_regular(root, REPORT_NAME) as stream:
            info = os.fstat(stream.fileno())
            if (info.st_uid != root.stat().st_uid or info.st_nlink != 1
                    or info.st_mode & 0o027 or info.st_size > MAX_REPORT):
                raise ValueError("unsafe_status_report")
            raw = stream.read(MAX_REPORT + 1)
        if len(raw) > MAX_REPORT:
            raise ValueError("status_report_too_large")
        return _validate(json.loads(raw, object_pairs_hook=_unique_object), now)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return empty_status("invalid")
    except (OSError, BackupError):
        return empty_status("unavailable")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--worker-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = observe_status(args.history, args.worker_state, service_reader=service_observation)
        publish_status(args.output, report)
        return 0
    except (OSError, ValueError, TypeError):
        # No raw exceptions, paths, subprocess output or secret-bearing data.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
