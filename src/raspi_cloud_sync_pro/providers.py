"""Opt-in rclone adapter for configured Koofr WebDAV, Drive and Dropbox remotes."""

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from .errors import BackupError
from .staging import ReadyBatch, StagedFile, relative_path


class RcloneProvider:
    def __init__(self, kind: str, remote: str, config: Path, *, enabled=False,
                 executable="rclone", process_timeout=900):
        if kind not in ("koofr_webdav", "google_drive_rclone", "dropbox_rclone"):
            raise ValueError("unsupported provider")
        if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*:[^:\\]*", remote):
            raise ValueError("use a named rclone remote with a relative backup directory")
        name, prefix = remote.split(":", 1)
        if prefix:
            relative_path(prefix)
        if type(enabled) is not bool or type(process_timeout) is not int or not 1 <= process_timeout <= 3600:
            raise ValueError("invalid provider limits")
        self.kind = kind
        self.remote = name + ":" + prefix
        self.config = Path(config).absolute()
        self.enabled = enabled
        self.executable = executable
        self.process_timeout = process_timeout
        self.destination_id = kind + "/" + self.remote

    def _run(self, args):
        if not self.enabled:
            raise BackupError("provider_disabled")
        try:
            info = self.config.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise BackupError("insecure_rclone_config")
            command = [
                self.executable, *args, "--config", str(self.config),
                "--retries", "1", "--low-level-retries", "1",
                "--transfers", "1", "--checkers", "1", "--contimeout", "15s",
                "--timeout", "60s", "--buffer-size", "1Mi", "--multi-thread-streams", "0",
                "--log-level", "ERROR",
            ]
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("RCLONE_") or key == "RCLONE_CONFIG_PASS"}
            # Suppress raw output: provider messages may contain URLs or credentials.
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=self.process_timeout,
                                    check=False, env=environment)
            if result.returncode:
                raise BackupError("rclone_command_failed", retryable=result.returncode not in (2, 6, 7))
        except FileNotFoundError as exc:
            raise BackupError("rclone_or_config_missing") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackupError("rclone_timeout", retryable=True) from exc
        except OSError as exc:
            raise BackupError("rclone_execution_error") from exc

    def _target(self, key):
        relative_path(key)
        separator = "" if self.remote.endswith(":") else "/"
        return self.remote + separator + key

    def upload(self, batch: ReadyBatch, item: StagedFile):
        batch.check_file(item)
        arguments = ["copyto", str(batch.root / "files" / item.relative), self._target(item.remote_key),
                     "--immutable", "--checksum"]
        if self.kind == "dropbox_rclone":
            # Wait for the batch commit; asynchronous mode cannot establish the
            # uploaded state. Reduce the backend's default in-memory chunk size.
            arguments += ["--dropbox-batch-mode", "sync", "--dropbox-chunk-size", "8Mi"]
        self._run(arguments)

    def verify(self, batch: ReadyBatch, item: StagedFile):
        # Download hashing also works when WebDAV has no server checksum support.
        with tempfile.TemporaryDirectory(prefix="raspi-cloud-check-") as directory:
            checksum = Path(directory) / "SHA256SUMS"
            checksum.write_text(f"{item.sha256}  {item.remote_key}\n", encoding="utf-8")
            self._run(["checksum", "sha256", str(checksum), self.remote, "--download", "--one-way"])
