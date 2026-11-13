"""Opt-in scheduler for already published batches; never reads live USB/share data."""

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import _unique_object
from .device_modes import DevicePolicy, approved_internet
from .engine import BackupEngine
from .errors import BackupError
from .history import History
from .network import Connectivity
from .network_diagnostics import network_status
from .providers import RcloneProvider
from .staging import ReadyBatch, _fsync_directory, open_regular


@dataclass(frozen=True)
class WorkerSettings:
    state: Path
    staging: Path
    rclone_config: Path
    destinations: tuple
    policy: DevicePolicy
    enabled: bool = False
    interval: int = 10

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(), object_pairs_hook=_unique_object)
            if set(data) != {"schema_version", "enabled", "state_directory", "staging_directory",
                             "rclone_config", "destinations", "approved_uplinks", "allow_metered", "interval"} \
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1 \
                    or type(data["enabled"]) is not bool or type(data["interval"]) is not int \
                    or not 5 <= data["interval"] <= 3600 or not isinstance(data["destinations"], list) \
                    or not 1 <= len(data["destinations"]) <= 8 or not isinstance(data["approved_uplinks"], list):
                raise ValueError()
            paths = [Path(data[name]) for name in ("state_directory", "staging_directory", "rclone_config")]
            if any(not p.is_absolute() or ".." in p.parts or p.is_symlink() for p in paths) \
                    or paths[0] == paths[1] or paths[1] in paths[0].parents \
                    or paths[1] == paths[2] or paths[1] in paths[2].parents:
                raise ValueError()
            providers = []
            for item in data["destinations"]:
                if not isinstance(item, dict) or set(item) != {"kind", "remote"}:
                    raise ValueError()
                provider = RcloneProvider(item["kind"], item["remote"], paths[2])
                if provider.destination_id in [p.destination_id for p in providers]:
                    raise ValueError()
                providers.append(provider)
            policy = DevicePolicy(approved_uplinks=tuple(data["approved_uplinks"]), allow_metered=data["allow_metered"])
            return cls(*paths, tuple((p.kind, p.remote) for p in providers), policy,
                       data["enabled"], data["interval"])
        except (OSError, ValueError, TypeError, KeyError, BackupError) as exc:
            raise BackupError("invalid_staged_worker_config") from exc

    def plan(self):
        return {"enabled": self.enabled, "source": "published_ready_batches_only",
                "destinations": [{"kind": kind, "remote": remote} for kind, remote in self.destinations],
                "approved_uplinks": list(self.policy.approved_uplinks), "allow_metered": self.policy.allow_metered,
                "interval": self.interval, "automatic_deletion": False,
                "changes_usb_or_wifi": False, "cloud_accounts_verified": False}


def observe_uplink():
    """Cached observation only; no NM connectivity check or route mutation."""
    report = network_status()
    route = report["preferred_ipv4_route"]
    if report["cached_global_connectivity"] != "full" or not route:
        return "unknown", Connectivity.UNKNOWN, True
    name = route["interface"]
    device = report["interfaces"].get(name, {})
    if device.get("state_code") != 100:
        return "unknown", Connectivity.UNKNOWN, True
    uplink = "wifi" if device.get("type") in ("wifi", "802-11-wireless") else \
        "usb_host_ics" if name == "usb0" and device.get("type") == "ethernet" else "unknown"
    try:
        result = subprocess.run(["nmcli", "-g", "GENERAL.METERED", "device", "show", name],
                                capture_output=True, text=True, timeout=5, check=False,
                                env={**os.environ, "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        # nmcli renders guessed enum values with parentheses, unlike D-Bus
        # enum names. Accept only explicit C-locale values; unknown stays shut.
        metered = {"yes": True, "no": False, "guess-yes": True, "guess-no": False,
                   "yes (guessed)": True, "no (guessed)": False}.get(result.stdout.strip())
        if result.returncode or metered is None:
            return "unknown", Connectivity.UNKNOWN, True
        return uplink, Connectivity.ONLINE, metered
    except (OSError, subprocess.TimeoutExpired):
        return "unknown", Connectivity.UNKNOWN, True


@contextmanager
def scheduler_lock(directory):
    fd = os.open(Path(directory) / "staged-worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BackupError("unsafe_scheduler_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("staged_worker_busy") from exc
        yield
    finally:
        os.close(fd)


class StagedWorker:
    def __init__(self, settings, history, providers):
        self.settings, self.history, self.providers = settings, history, tuple(providers)
        self.engine = BackupEngine(history)
        self.reconciled = set()
        self.rejected = {}
        with history.connection:
            history.connection.execute("""CREATE TABLE IF NOT EXISTS staged_receipts (
                batch_root TEXT NOT NULL, destination TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                registered_at REAL NOT NULL, PRIMARY KEY(batch_root,destination,manifest_sha256)
            )""")
            history.connection.execute("""CREATE TABLE IF NOT EXISTS staged_rejections (
                batch_root TEXT PRIMARY KEY, fingerprint TEXT NOT NULL
            )""")

    def _reject(self, root, fingerprint):
        # Keep unchanged malformed batches from monopolizing every fresh child.
        self.rejected[root] = fingerprint
        with self.history.connection:
            self.history.connection.execute(
                "INSERT OR REPLACE INTO staged_rejections VALUES (?,?)", (root, fingerprint))

    def tick(self, *, now=None, observation=None):
        now = time.time() if now is None else now
        result = {"jobs_added": 0, "rejected_batches": 0, "cloud_results": [],
                  "reconciliation_pending": False}
        staging = self.settings.staging
        if staging.is_symlink():
            raise BackupError("unsafe_staging_roots")
        if staging.exists():
            with os.scandir(staging) as entries:
                newly_checked = 0
                for entry in entries:
                    if entry.path in self.reconciled or not entry.name.endswith(".ready"):
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        result["rejected_batches"] += 1
                        continue
                    info = entry.stat(follow_symlinks=False)
                    try:
                        manifest_info = (Path(entry.path) / "manifest.json").lstat()
                        fingerprint = (info.st_ino, info.st_mtime_ns, manifest_info.st_mtime_ns, manifest_info.st_size)
                    except OSError:
                        fingerprint = (info.st_ino, info.st_mtime_ns)
                    fingerprint = json.dumps(fingerprint, separators=(",", ":"))
                    previous_rejection = self.history.connection.execute(
                        "SELECT fingerprint FROM staged_rejections WHERE batch_root=?", (entry.path,)).fetchone()
                    if self.rejected.get(entry.path) == fingerprint or \
                            (previous_rejection and previous_rejection[0] == fingerprint):
                        continue
                    charged = False
                    try:
                        digest = hashlib.sha256()
                        with open_regular(Path(entry.path), "manifest.json") as manifest:
                            for block in iter(lambda: manifest.read(65536), b""):
                                digest.update(block)
                        signature = digest.hexdigest()
                        missing = [provider for provider in self.providers if not self.history.connection.execute(
                            "SELECT 1 FROM staged_receipts WHERE batch_root=? AND destination=? AND manifest_sha256=?",
                            (entry.path, provider.destination_id, signature)).fetchone()]
                        if not missing:
                            self.reconciled.add(entry.path)
                            continue
                        if newly_checked >= 100:
                            result["reconciliation_pending"] = True
                            break
                        newly_checked += 1
                        charged = True
                        batch = ReadyBatch.load(Path(entry.path))
                        for provider in missing:
                            result["jobs_added"] += self.history.enqueue(batch, provider.destination_id, now)
                            with self.history.connection:
                                self.history.connection.execute("INSERT OR IGNORE INTO staged_receipts VALUES (?,?,?,?)",
                                                                (entry.path, provider.destination_id, signature, now))
                        self.reconciled.add(entry.path)
                    except (BackupError, OSError):
                        # Opening a missing/unsafe manifest also consumes the
                        # reconciliation budget, while known receipts do not.
                        if not charged:
                            if newly_checked >= 100:
                                result["reconciliation_pending"] = True
                                break
                            newly_checked += 1
                        result["rejected_batches"] += 1
                        self._reject(entry.path, fingerprint)
        uplink, connectivity, metered = observe_uplink() if observation is None else observation
        allowed = approved_internet(self.settings.policy, uplink, connectivity)
        for provider in self.providers:
            result["cloud_results"].append(self.engine.run_once(
                provider, now=now, connectivity=connectivity if allowed else Connectivity.UNKNOWN,
                metered=metered, allow_metered=self.settings.policy.allow_metered,
            ))
        result.update(observed_uplink=uplink, metered=metered,
                      network_policy_allows_upload=allowed and (not metered or self.settings.policy.allow_metered),
                      updated_at=now, destinations=[p.destination_id for p in self.providers], automatic_deletion=False)
        destinations = tuple(provider.destination_id for provider in self.providers)
        placeholders = ",".join("?" for _ in destinations)
        result["pending_jobs"] = self.history.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','retry','uploading','uploaded','verifying') "
            f"AND destination IN ({placeholders})", destinations).fetchone()[0] if destinations else 0
        return result


def run_staged_worker(settings, *, once=False):
    if settings.enabled is not True:
        raise BackupError("staged_worker_disabled")
    try:
        info = settings.rclone_config.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 \
                or shutil.which("rclone") is None:
            raise OSError()
    except OSError as exc:
        raise BackupError("worker_rclone_prerequisites_missing") from exc
    state = settings.state
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state.stat()
    if state.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BackupError("unsafe_worker_state")
    providers = [RcloneProvider(kind, remote, settings.rclone_config, enabled=True)
                 for kind, remote in settings.destinations]
    with scheduler_lock(state), History(state / "history.sqlite3") as history:
        worker = StagedWorker(settings, history, providers)
        while True:
            try:
                result = worker.tick()
            except Exception:
                # Do not log raw provider/configuration/filename errors or discard data.
                result = {"error": "staged_worker_tick_failed", "updated_at": time.time(),
                          "automatic_deletion": False}
            temporary = state / "worker-status.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, state / "worker-status.json")
            _fsync_directory(state)
            if once:
                return result
            time.sleep(settings.interval)
