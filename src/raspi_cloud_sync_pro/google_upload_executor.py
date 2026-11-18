"""Private Google batch bootstrap; no activation means no state or provider I/O.

The fixed four inherited root streams and actual sandbox precede public canaries.
Only the separate upload API's PID/batch/admission-bound root gate can authorize
credential use. A successful private CLI return proves receipt framing finished;
valid pending/failed receipts retain the same live completion barrier as success.
Requires the paired CLI internal expected-control binding; source15 alone refuses.
No saved QA report or internal unit name grants admission. Test is unchanged.
"""

import argparse
import fcntl
import io
import math
import os
import sys
import time
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

from raspi_cloud_sync_pro import cli
from raspi_cloud_sync_pro import google_control as control
from raspi_cloud_sync_pro import google_test
from raspi_cloud_sync_pro import google_test_executor as trusted
from raspi_cloud_sync_pro import google_upload
from raspi_cloud_sync_pro.errors import BackupError

COMPLETION_TIMEOUT = 15.0
_ERROR = "Private Google upload executor unavailable; no private details were printed."
_COUNTS = ("files_verified_now", "files_previously_verified", "files_pending", "files_failed")
_FLAGS = ("current_worker_state_checked", "account_verified", "automatic_deletion")


class GoogleUploadExecutorError(BackupError):
    """Fixed failure only; private CLI/canary values never reach diagnostics."""


def _fail():
    raise GoogleUploadExecutorError("google_upload_executor_refused") from None


def _arguments(prepared_state, operational_state, staging, batch_id, manifest_sha256,
               control_request_id, executable, executable_sha256, timeout, canary_timeout):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300
            or type(canary_timeout) not in (int, float) or not math.isfinite(canary_timeout)
            or not 0 < canary_timeout <= 60 or not control._id(batch_id)
            or not control._id(control_request_id) or not control._hash(manifest_sha256)
            or not control._hash(executable_sha256)):
        _fail()
    for value in (prepared_state, operational_state, staging, executable):
        if (not isinstance(value, (str, Path)) or not Path(value).is_absolute()
                or ".." in Path(value).parts or "\x00" in str(value)):
            _fail()


def _summary(value):
    """Accept only the CLI's fixed receipt-written projection, never raw output."""
    if (not isinstance(value, dict) or set(value) != {"schema_version", "provider", "batch_state", *_COUNTS, *_FLAGS}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["provider"] != "google_drive" or value["batch_state"] not in ("verified", "historical", "pending", "failed")
            or any(value[key] is not False for key in _FLAGS)
            or any(type(value[key]) is not int or not 0 <= value[key] <= google_upload.batches.MAX_FILES for key in _COUNTS)
            or not 1 <= sum(value[key] for key in _COUNTS) <= google_upload.batches.MAX_FILES):
        _fail()
    state = value["batch_state"]
    if ((state == "verified" and (not value["files_verified_now"] or value["files_pending"] or value["files_failed"]))
            or (state == "historical" and (value["files_verified_now"] or not value["files_previously_verified"]
                                           or value["files_pending"] or value["files_failed"]))
            or (state == "pending" and (not value["files_pending"] or value["files_failed"]))
            or (state == "failed" and not value["files_failed"])):
        _fail()
    return 0 if state in ("verified", "historical") else 2


def run_google_upload_executor(prepared_state, operational_state, staging, batch_id,
                               manifest_sha256, control_request_id, executable, executable_sha256, *,
                               timeout=300, canary_timeout=trusted.CANARY_TIMEOUT):
    """Consume activation streams; this bootstrap creates no root admission.

    Staging and credential paths are only passed to the existing gated CLI; the
    bootstrap never opens them. Root must verify the actual readonly staging
    mount, exact argv/process, held Wi-Fi policy and fresh source admission.
    The private fresh Test-unit namespace preserves the existing inspector ABI;
    upload's exact argv, gate purpose and bound batch distinguish its authority.
    """
    try:
        with trusted._activation() as (channels, accepted):
            _arguments(prepared_state, operational_state, staging, batch_id, manifest_sha256,
                       control_request_id, executable, executable_sha256, timeout, canary_timeout)
            # Do not erase LISTEN_* before activation authenticates/consumes it.
            os.environ.clear()
            deadline = time.monotonic() + canary_timeout
            trusted._sandbox()
            runner = google_test._Runner(executable, executable_sha256, deadline, Path("/dev/null"))
            canary = channels["canary"]
            trusted.canary_write_frame(canary, {"schema_version": 1, "provider": "google_drive",
                                               "phase": "ready", "worker_pid": os.getpid()}, deadline)
            binding = trusted._binding(trusted.canary_read_frame(canary, deadline), "go")
            if binding["request_id"] != control_request_id:
                _fail()
            report = trusted._canaries(binding, runner, deadline)
            trusted._inherited(set(channels.values()))
            trusted.canary_write_frame(canary, report, deadline)
            trusted._shutdown_write(canary)
            acknowledgement = trusted._binding(trusted.canary_read_frame(canary, deadline), "ack")
            if acknowledgement != {**binding, "phase": "ack"}:
                _fail()
            trusted._eof(canary, deadline)
            identity, flags = accepted.pop(canary)
            trusted._safe_close(canary, identity, flags)
            del channels["canary"]
            trusted._inherited(set(channels.values()))
            trusted._check(deadline)
            handoff = {}
            for name, fd in channels.items():
                identity, flags = accepted[fd]
                fcntl.fcntl(fd, fcntl.F_SETFL, flags)
                handoff[fd] = identity, flags
            receipt_identity, receipt_flags = accepted[channels["receipt"]]
            completion = fcntl.fcntl(channels["receipt"], fcntl.F_DUPFD_CLOEXEC, 3)
            try:
                arguments = SimpleNamespace(prepared_state=Path(prepared_state), operational_state=Path(operational_state),
                    staging=Path(staging), batch_id=batch_id, manifest_sha256=manifest_sha256,
                    admission_fd=channels["request"], root_gate_fd=channels["gate"], receipt_fd=channels["receipt"],
                    rclone=Path(executable), rclone_sha256=executable_sha256, timeout=timeout)
                for fd in handoff:
                    accepted.pop(fd)
                # This trusted helper returns only AFTER validating and writing
                # the private receipt. Exceptions and write failures do not
                # produce the marker. No stdout parsing or new proxy is needed.
                result = _summary(cli._run_private_google_upload(
                    arguments, expected_control_request_id=control_request_id))
                # CLI consumed every original endpoint. No staging, config,
                # history, provider or retry work follows this point.
                trusted._inherited({completion})
                trusted._sandbox()
                fcntl.fcntl(completion, fcntl.F_SETFL, receipt_flags | os.O_NONBLOCK)
                trusted._shutdown_write(completion)
                completion_deadline = time.monotonic() + COMPLETION_TIMEOUT
                acknowledgement = trusted.canary_read_frame(completion, completion_deadline)
                expected = {"schema_version": 1, "provider": "google_drive", "phase": "upload_complete",
                            "worker_pid": os.getpid(), "control_request_id": control_request_id,
                            "batch_id": batch_id, "manifest_sha256": manifest_sha256, "nonce": binding["nonce"]}
                if (acknowledgement != expected or type(acknowledgement.get("schema_version")) is not int
                        or type(acknowledgement.get("worker_pid")) is not int):
                    _fail()
                trusted._eof(completion, completion_deadline)
                # ACK permits only closure and the previously determined exit.
                return result
            finally:
                for fd, (identity, flags) in handoff.items():
                    trusted._safe_close(fd, identity, flags)
                trusted._safe_close(completion, receipt_identity, receipt_flags)
    except GoogleUploadExecutorError:
        raise
    except Exception:
        _fail()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private root-coordinated selected Google batch bootstrap")
    parser.add_argument("--prepared-state", required=True, type=Path)
    parser.add_argument("--operational-state", required=True, type=Path)
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--control-request-id", required=True)
    parser.add_argument("--rclone", required=True, type=Path)
    parser.add_argument("--rclone-sha256", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    try:
        with redirect_stderr(io.StringIO()):
            args = parser.parse_args(argv)
    except SystemExit as error:
        if error.code == 0:
            return 0
        print(_ERROR, file=sys.stderr)
        return 2
    try:
        return run_google_upload_executor(args.prepared_state, args.operational_state, args.staging,
            args.batch_id, args.manifest_sha256, args.control_request_id, args.rclone, args.rclone_sha256,
            timeout=args.timeout)
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
