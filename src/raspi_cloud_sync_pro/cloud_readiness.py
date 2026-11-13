"""Local-only rclone backend metadata checks, separate from account/backup activation."""

import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

from .config import _unique_object
from .errors import BackupError

EXPECTED_BACKENDS = {"koofr_webdav": "webdav", "google_drive_rclone": "drive"}
MAX_INVENTORY_BYTES = 65536


def _protected_path(path, *, private=False, executable=False):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise BackupError("cloud_path_unprotected")
    # Resolve only a protected executable symlink (e.g. a root-managed version
    # link). Credential files must be actual regular files, never symlinks.
    info = path.lstat()
    allowed_owners = (0, os.getuid()) if executable else (os.getuid(),)
    if info.st_uid not in allowed_owners or (path.is_symlink() and not executable):
        raise BackupError("cloud_path_unprotected")
    for parent in path.parents:
        ancestor = parent.lstat()
        sticky_root = ancestor.st_uid == 0 and ancestor.st_mode & stat.S_ISVTX
        if not stat.S_ISDIR(ancestor.st_mode) or ancestor.st_uid not in (0, os.getuid()) \
                or (ancestor.st_mode & 0o022 and not sticky_root):
            raise BackupError("cloud_path_unprotected")
    if path.is_symlink():
        return _protected_path(path.resolve(strict=True), executable=True)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & (0o077 if private else 0o022) \
            or (executable and not info.st_mode & 0o111):
        raise BackupError("cloud_path_unprotected")
    return path, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _capture_inventory(command, *, timeout=5):
    """Bound memory, elapsed process time and raw output; never emit stderr."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("RCLONE_") or key == "RCLONE_CONFIG_PASS"}
    process = None
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=environment, start_new_session=True)
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise BackupError("cloud_inventory_timeout")
                block = os.read(process.stdout.fileno(), min(8192, MAX_INVENTORY_BYTES + 1 - len(output)))
                if not block:
                    break
                output.extend(block)
                if len(output) > MAX_INVENTORY_BYTES:
                    raise BackupError("cloud_inventory_too_large")
        if process.wait(timeout=max(0, deadline - time.monotonic())):
            raise BackupError("cloud_inventory_unavailable")
        return bytes(output)
    except subprocess.TimeoutExpired as exc:
        raise BackupError("cloud_inventory_timeout") from exc
    except OSError as exc:
        raise BackupError("cloud_inventory_unavailable") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                # The process owns a new group; stop only this read-only probe.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            process.stdout.close()


def _metadata(output):
    try:
        records = json.loads(output, object_pairs_hook=_unique_object)
        if not isinstance(records, list):
            raise ValueError()
        remotes = {}
        for item in records:
            if not isinstance(item, dict) or not {"name", "type", "source"} <= set(item) \
                    or not isinstance(item["name"], str) or not 1 <= len(item["name"]) <= 256 \
                    or any(ord(char) < 32 for char in item["name"]) \
                    or not isinstance(item["type"], str) \
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item["type"]) \
                    or item["source"] != "file" or item["name"] in remotes:
                raise ValueError()
            # Descriptions and all unrelated fields are discarded, never logged.
            remotes[item["name"]] = item["type"]
        return remotes
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise BackupError("cloud_inventory_invalid") from exc


def cloud_readiness(settings, *, executable=None):
    """Inspect named backend types without initializing remote filesystems."""
    configured = []
    for kind, remote in settings.destinations:
        configured.append({"kind": kind, "remote": remote, "expected_backend": EXPECTED_BACKENDS[kind],
                           "configured_backend": None, "status": "not_checked"})
    report = {"read_only": True, "uploads_activated": False, "cloud_network_tested": False,
              "backend_types_match": False, "cloud_accounts_verified": False,
              "account_authorization": "not_tested", "tls_and_server_identity": "not_tested",
              "koofr_endpoint": "not_checked", "destinations": configured,
              "limitations": ["Backend type alone does not establish account access, quota or destination ownership.",
                              "A webdav type does not prove the endpoint is Koofr or uses valid HTTPS.",
                              "An alias backend may target a local path or another remote; its target is not inspected.",
                              "No upload, token refresh, network request or configuration write is requested."]}
    try:
        config, identity = _protected_path(settings.rclone_config, private=True)
        chosen = executable if executable is not None else shutil.which("rclone")
        if chosen is None:
            raise BackupError("cloud_rclone_unavailable")
        binary, _ = _protected_path(chosen, executable=True)
        output = _capture_inventory([str(binary), "listremotes", "--json", "--config", str(config),
                                     "--ask-password=false", "--log-level", "ERROR"])
        if _protected_path(config, private=True)[1] != identity:
            raise BackupError("cloud_config_changed")
        remotes = _metadata(output)
        for destination in configured:
            name = destination["remote"].split(":", 1)[0]
            backend = remotes.get(name)
            destination["configured_backend"] = backend if backend in ("drive", "webdav", "alias", "local") \
                else "other" if backend else None
            destination["status"] = "missing_remote" if backend is None else \
                "backend_matches" if backend == destination["expected_backend"] else \
                "alias_backend" if backend == "alias" else "backend_mismatch"
        report["backend_types_match"] = bool(configured) and all(
            item["status"] == "backend_matches" for item in configured)
    except BackupError as exc:
        report["error"] = exc.code
    except (OSError, ValueError, TypeError, RuntimeError):
        report["error"] = "cloud_metadata_unavailable"
    return report
