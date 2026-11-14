"""Stage stable SMB file versions under server-enforced deny-write/delete handles.

Local scans are candidate hints only. Local writers are forbidden on the share;
all clients must use Samba so its share-mode/lease rules serialize the snapshot.
"""

import hashlib
import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import BackupError
from .staging import CHUNK_SIZE, ReadyBatch, StagedFile, _fsync_directory, relative_path


class LocalSambaReader:
    """Optional smbprotocol client; no session is opened before the hardware gate."""

    def __init__(self, share="CloudSync", username=None, password=None, *, approval_guard=None):
        from .linux_gadget import require_hardware_test_approval
        if not share or not share.isascii() or not share.replace("_", "").isalnum():
            raise BackupError("invalid_smb_share")
        if approval_guard is not None and not callable(approval_guard):
            raise BackupError("invalid_smb_approval_guard")
        self.share, self.username, self.password = share, username, password
        self._approval_guard = require_hardware_test_approval if approval_guard is None else approval_guard

    @contextmanager
    def open_version(self, relative):
        self._approval_guard()  # Default gate, or the separate capture-only authority, before networking.
        try:
            import smbclient
        except ImportError as exc:
            raise BackupError("optional_smb_dependency_missing") from exc
        path = "\\\\127.0.0.1\\" + self.share + "\\" + relative_path(relative).replace("/", "\\")
        try:
            with smbclient.open_file(path, mode="rb", share_access="r", buffering=0,
                                     username=self.username, password=self.password, encrypt=True,
                                     connection_timeout=10) as incoming:
                # Only FILE_SHARE_READ is granted: Samba must reject active writers,
                # break conflicting leases and prevent write/delete opens until close.
                yield incoming
        except OSError as exc:
            raise BackupError("smb_version_unavailable", retryable=True) from exc
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError("smb_version_unavailable", retryable=True) from exc


def candidate_files(root: Path):
    """Full periodic inventory covers watcher overflow/restarts; no file bytes read."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise BackupError("unsafe_share_root")

    def fail(error):
        if not isinstance(error, FileNotFoundError):
            raise error

    try:
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=fail):
            regular_names = []
            for name in dirs + names:
                path = Path(directory) / name
                try:
                    mode = path.lstat().st_mode
                except FileNotFoundError:
                    continue  # Concurrent SMB rename/delete: a later scan reconciles it.
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise BackupError("unsupported_share_entry")
                if name in names and stat.S_ISREG(mode):
                    regular_names.append(name)
            for name in sorted(regular_names):
                relative = relative_path((Path(directory) / name).relative_to(root).as_posix())
                # Names/suffixes do not establish completeness or justify exclusions.
                yield relative
    except OSError as exc:
        raise BackupError("share_inventory_failed", retryable=True) from exc


def stage_smb_version(reader, relative: str, destination: Path, *, reserve_bytes=16 * 1024 * 1024):
    """Publish exactly the bytes read while the SMB share-mode handle remains held."""
    relative = relative_path(relative)
    if ":" in relative:  # Exclude alternate data stream syntax from this file-only contract.
        raise BackupError("unsupported_smb_stream")
    if type(reserve_bytes) is not int or reserve_bytes < 0:
        raise BackupError("invalid_space_reserve")
    destination = Path(destination).absolute()
    if destination.is_symlink():
        raise BackupError("unsafe_staging_roots")
    try:
        # Do not create a batch when a writer is still open or a lease cannot break.
        with reader.open_version(relative) as incoming:
            expected_size = incoming.seek(0, os.SEEK_END)
            incoming.seek(0)
            if type(expected_size) is not int or expected_size < 0:
                raise BackupError("smb_size_unverified")
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            if shutil.disk_usage(destination).free < expected_size + reserve_bytes:
                raise BackupError("staging_space_low")
            temporary = destination / (uuid.uuid4().hex + ".incomplete")
            temporary.mkdir(mode=0o700)
            output_path = temporary / "files" / relative
            output_path.parent.mkdir(parents=True)
            digest = hashlib.sha256()
            size = 0
            with output_path.open("xb") as outgoing:
                for block in iter(lambda: incoming.read(CHUNK_SIZE), b""):
                    if shutil.disk_usage(destination).free < reserve_bytes + len(block):
                        raise BackupError("staging_space_low")
                    outgoing.write(block)
                    size += len(block)
                    digest.update(block)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if size != expected_size:
                raise BackupError("smb_short_read")
            item = StagedFile(relative, size, digest.hexdigest())
            with (temporary / "manifest.json").open("x") as manifest:
                json.dump({"schema_version": 1, "files": [vars(item)]}, manifest)
                manifest.flush()
                os.fsync(manifest.fileno())
            for directory, _, _ in os.walk(temporary, topdown=False):
                _fsync_directory(Path(directory))
        # Publish only after successful CLOSE, so interrupted/failed handles stay incomplete.
        ready = temporary.with_suffix(".ready")
        temporary.rename(ready)
        _fsync_directory(destination)
        return ReadyBatch.load(ready)
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError("smb_staging_io_error", retryable=True) from exc


class SambaIngestor:
    def __init__(self, reader, staging_directory, history, destinations):
        if not destinations:
            raise BackupError("backup_destination_required")
        self.reader = reader
        self.staging = Path(staging_directory)
        self.history = history
        self.destinations = tuple(destinations)
        self._seen = {}
        self._reconciled = set()

    def capture(self, relative, now):
        batch = stage_smb_version(self.reader, relative, self.staging)
        added = sum(self.history.enqueue(batch, target, now) for target in self.destinations)
        self._reconciled.add(batch.root)
        return batch, added

    def scan_once(self, share_root, now):
        """Metadata limits candidate work; only the SMB lock authorizes byte reads."""
        counts = {"staged": 0, "deferred": 0}
        for relative in candidate_files(share_root):
            try:
                info = (Path(share_root) / relative).lstat()
                fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if self._seen.get(relative) == fingerprint:
                    continue
                self.capture(relative, now)
                self._seen[relative] = fingerprint
                counts["staged"] += 1
            except BackupError as exc:
                if not exc.retryable:
                    raise
                counts["deferred"] += 1
            except FileNotFoundError:
                counts["deferred"] += 1
            except OSError as exc:
                raise BackupError("share_inventory_failed", retryable=True) from exc
        return counts

    def reconcile(self, now):
        """Recover publication→queue crashes without reading the live share."""
        added = 0
        if self.staging.exists():
            for path in sorted(self.staging.glob("*.ready")):
                if path in self._reconciled:
                    continue
                batch = ReadyBatch.load(path)
                added += sum(self.history.enqueue(batch, target, now) for target in self.destinations)
                self._reconciled.add(path)
        return added


class NetworkBackupCoordinator:
    """USB share capture works without Wi-Fi; cloud attempts require approved internet."""

    def __init__(self, ingestor, engine, providers, policy):
        from .device_modes import DeviceMode
        if policy.mode != DeviceMode.ETHERNET_SAMBA:
            raise BackupError("ethernet_mode_required")
        self.ingestor, self.engine, self.providers, self.policy = ingestor, engine, tuple(providers), policy

    def tick(self, share_root, now, *, observed_uplink="unknown", connectivity=None, metered=False):
        from .device_modes import approved_internet
        from .network import Connectivity
        connectivity = Connectivity.UNKNOWN if connectivity is None else connectivity
        self.ingestor.reconcile(now)
        result = self.ingestor.scan_once(share_root, now)
        approved = approved_internet(self.policy, observed_uplink, connectivity)
        result["cloud"] = [self.engine.run_once(
            provider, now=now, connectivity=connectivity if approved else Connectivity.UNKNOWN,
            metered=metered, allow_metered=self.policy.allow_metered,
        ) for provider in self.providers]
        return result
