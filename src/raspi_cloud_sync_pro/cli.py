"""Development diagnostics, an authenticated dashboard and code-gated device tools."""

import argparse
import fcntl
import io
import json
import math
import os
import sys
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import ConfigError, load_config
from .errors import BackupError
from .history import read_history


_GOOGLE_UPLOAD_ERROR = "Private Google upload unavailable; no private details were printed."
_GOOGLE_TEST_ERROR = "Private Google Test unavailable; no uploads were enabled and no private details were printed."
_GOOGLE_TEST_BROKER_ERROR = "Google Test coordinator unavailable; no uploads were enabled and no private details were printed."
_GOOGLE_CONTROL_RUNNER_ERROR = "Google control runner unavailable; no private details were printed."
_GOOGLE_CONTROL_ERROR = "Private Google control operation unavailable; no private details were printed."
_SMB_CAPTURE_ERROR = "SMB capture unavailable; no cloud uploads were enabled and no private details were printed."
_GOOGLE_TEST_BROKER_CODES = frozenset("google_test_broker_" + value for value in (
    "approval_required authority_changed canary_invalid cleanup_unconfirmed command_invalid completion_invalid "
    "configuration_changed configuration_invalid control_applied disabled identity_unsafe installation_unsafe loopback_reached "
    "manager_unapproved no_request no_test pidfd_required receipt_invalid request_invalid single_thread_required "
    "source_refused source_unavailable_before_worker storage_invalid systemd_unavailable test_verified_not_enabled timeout unavailable "
    "unit_changed unit_conflict unit_invalid wifi_unavailable wifi_unavailable_before_worker worker_changed worker_failed worker_invalid"
).split())


def _smb_capture_projection(report):
    counts = ("staged_files", "deferred_files", "rejected_files")
    flags = ("uploads_enabled", "cloud_accounts_verified", "automatic_deletion")
    if (not isinstance(report, dict) or set(report) != {"capture_state", *counts, *flags}
            or report["capture_state"] not in ("disabled", "blocked", "unavailable", "idle", "deferred", "captured")
            or any(type(report[key]) is not int or not 0 <= report[key] <= 32 for key in counts)
            or sum(report[key] for key in counts) > 32 or any(report[key] is not False for key in flags)):
        raise BackupError("smb_capture_report_invalid")
    state = report["capture_state"]
    if ((state in ("disabled", "blocked", "unavailable") and any(report[key] for key in counts))
            or (state == "idle" and (report["staged_files"] or report["deferred_files"]))
            or (state == "deferred" and (report["staged_files"] or not report["deferred_files"]))
            or (state == "captured" and not report["staged_files"])):
        raise BackupError("smb_capture_report_invalid")
    return {key: report[key] for key in ("capture_state", *counts, *flags)}


def _google_test_broker_projection(report):
    fields = {"status", "code", "uploads_enabled", "account_verified", "current_worker_state_checked"}
    if (not isinstance(report, dict) or set(report) != fields
            or report["status"] not in ("disabled", "idle", "applied", "tested", "failed")
            or not isinstance(report["code"], str) or report["code"] not in _GOOGLE_TEST_BROKER_CODES
            or any(report[key] is not False for key in
                   ("uploads_enabled", "account_verified", "current_worker_state_checked"))):
        raise BackupError("google_test_broker_report_invalid")
    completed = {"disabled": {"google_test_broker_disabled"},
                 "idle": {"google_test_broker_no_request", "google_test_broker_no_test"},
                 "applied": {"google_test_broker_control_applied"},
                 "tested": {"google_test_broker_test_verified_not_enabled"}}
    if ((report["status"] in completed and report["code"] not in completed[report["status"]])
            or (report["status"] == "failed" and report["code"] in set().union(*completed.values()))):
        raise BackupError("google_test_broker_report_invalid")
    return {key: report[key] for key in ("status", "code", "uploads_enabled", "account_verified", "current_worker_state_checked")}


def _google_control_runner_projection(report):
    if isinstance(report, dict) and isinstance(report.get("code"), str) and report["code"] in _GOOGLE_TEST_BROKER_CODES:
        return _google_test_broker_projection(report)
    expected = {"disabled": {"google_control_runner_disabled"},
                "idle": {"google_control_runner_no_pending_request"},
                "deferred": {"google_control_runner_source_unavailable", "google_control_runner_request_deferred"},
                "failed": {"google_control_runner_configuration_invalid", "google_control_runner_source_invalid",
                           "google_control_runner_unavailable"}}
    if (not isinstance(report, dict) or set(report) != {"status", "code", "uploads_enabled", "account_verified", "current_worker_state_checked"}
            or not isinstance(report.get("status"), str) or report["status"] not in expected
            or not isinstance(report.get("code"), str) or report["code"] not in expected[report["status"]]
            or any(report[key] is not False for key in ("uploads_enabled", "account_verified", "current_worker_state_checked"))):
        raise BackupError("google_control_runner_report_invalid")
    return dict(report)


def _run_private_google_test(args):
    """Consume operator channels; the worker alone validates a root release.

    The original gate stays owned here while a private duplicate is transferred
    to the worker. No credentials, owner intent or root release is created here.
    """
    from . import google_destination as destination
    from . import google_test

    with ExitStack() as stack:
        channels = {}
        seen = set()
        invalid = False
        # Validate the receipt endpoint before anything can contact Google.
        # Independently consume every accepted partner even after a refusal.
        for name, fd, writing in (("receipt", args.receipt_fd, True),
                                  ("request", args.request_fd, False),
                                  ("gate", args.root_gate_fd, False)):
            if type(fd) is not int or fd in seen:
                invalid = True
                continue
            seen.add(fd)
            try:
                channels[name] = stack.enter_context(destination._channel(fd, writing=writing))
            except destination.GoogleDestinationError:
                invalid = True
        identities = [destination._descriptor_identity(fd) for fd in channels.values()]
        if invalid or len(channels) != 3 or len(set(identities)) != 3:
            raise BackupError("google_test_channel_invalid")
        if (not math.isfinite(args.timeout) or not 0 < args.timeout <= 300
                or not google_test.control._hash(args.rclone_sha256)
                or any(not path.is_absolute() or ".." in path.parts
                       for path in (args.prepared_state, args.operational_state, args.rclone))):
            raise BackupError("google_test_arguments_invalid")
        request = destination._read_frame(channels["request"], destination._deadline(5))
        if not isinstance(request, dict):
            raise BackupError("google_test_request_invalid")

        gate_identity = destination._descriptor_identity(channels["gate"])
        private_gate = fcntl.fcntl(channels["gate"], fcntl.F_DUPFD_CLOEXEC, 3)
        try:
            outcome = google_test.run_google_test(
                args.prepared_state, args.operational_state, request,
                root_gate_fd=private_gate, executable=args.rclone,
                executable_sha256=args.rclone_sha256, timeout=args.timeout)
        finally:
            # The real worker consumes its accepted gate immediately. A failed
            # handoff/stub may leave it open; never close an unrelated reused FD.
            try:
                if destination._descriptor_identity(private_gate) == gate_identity:
                    os.close(private_gate)
            except OSError:
                pass
        if (type(outcome) is not google_test.GoogleTestOutcome or not isinstance(outcome.receipt, dict)
                or outcome.receipt.get("outcome") not in ("verified", "failed")
                or outcome.receipt.get("enabled") is not False
                or outcome.receipt.get("automatic_deletion") is not False):
            raise BackupError("google_test_result_invalid")
        destination._write_frame(channels["receipt"], destination._canonical(outcome.receipt),
                                 destination._deadline(5))
        # Build the public fields here; never relay a receipt, raw exception,
        # identity, path or a projection supplied by a replacement worker.
        verified = outcome.receipt["outcome"] == "verified"
        return {"schema_version": 1, "provider": "google_drive",
                "test_state": "verified" if verified else "failed",
                "error_code": None if verified and outcome.error_code is None else "google_test_failed",
                "enabled": False, "current_worker_state_checked": False,
                "account_verified": False, "automatic_deletion": False}


def _run_private_google_upload(args, *, expected_control_request_id=None):
    """Consume private channels; only the upload API authenticates root release."""
    from . import google_destination as destination
    from . import google_upload

    with ExitStack() as stack:
        channels, seen, invalid = {}, set(), False
        for name, fd, writing in (("receipt", args.receipt_fd, True),
                                  ("admission", args.admission_fd, False),
                                  ("gate", args.root_gate_fd, False)):
            if type(fd) is not int or fd in seen:
                invalid = True
                continue
            seen.add(fd)
            try:
                channels[name] = stack.enter_context(destination._channel(fd, writing=writing))
            except destination.GoogleDestinationError:
                invalid = True
        identities = [destination._descriptor_identity(fd) for fd in channels.values()]
        if invalid or len(channels) != 3 or len(set(identities)) != 3:
            raise BackupError("google_upload_channel_invalid")
        if expected_control_request_id is not None and not google_upload.control._id(expected_control_request_id):
            raise BackupError("google_upload_arguments_invalid")
        if (not math.isfinite(args.timeout) or not 0 < args.timeout <= 300
                or not google_upload.control._id(args.batch_id)
                or not google_upload.control._hash(args.manifest_sha256)
                or not google_upload.control._hash(args.rclone_sha256)
                or any(not path.is_absolute() or ".." in path.parts or "\x00" in str(path)
                       for path in (args.prepared_state, args.operational_state, args.staging, args.rclone))):
            raise BackupError("google_upload_arguments_invalid")
        admission = destination._read_frame(channels["admission"], destination._deadline(5))
        if not isinstance(admission, dict) or len(destination._canonical(admission)) > 16384:
            raise BackupError("google_upload_admission_invalid")
        if expected_control_request_id is not None and admission.get("control_request_id") != expected_control_request_id:
            raise BackupError("google_upload_admission_invalid")
        gate_identity = destination._descriptor_identity(channels["gate"])
        private_gate = fcntl.fcntl(channels["gate"], fcntl.F_DUPFD_CLOEXEC, 3)
        try:
            outcome = google_upload.run_google_batch_upload(
                args.prepared_state, args.operational_state, args.staging, args.batch_id,
                args.manifest_sha256, admission, root_gate_fd=private_gate,
                executable=args.rclone, executable_sha256=args.rclone_sha256, timeout=args.timeout)
        finally:
            try:
                if destination._descriptor_identity(private_gate) == gate_identity:
                    os.close(private_gate)
            except OSError:
                pass
        if type(outcome) is not google_upload.GoogleUploadOutcome or not isinstance(outcome.receipt, dict):
            raise BackupError("google_upload_result_invalid")
        receipt = outcome.receipt
        counts = ("files_verified_now", "files_previously_verified", "files_pending", "files_failed")
        state = receipt.get("batch_state")
        if (not google_upload.control.setup._valid_header(receipt) or state not in ("verified", "historical", "pending", "failed")
                or receipt.get("batch_id") != args.batch_id or receipt.get("manifest_sha256") != args.manifest_sha256
                or any(receipt.get(key) != admission.get(key) for key in ("control_request_id", "preparation", "test"))
                or receipt.get("automatic_deletion") is not False
                or any(type(receipt.get(key)) is not int or not 0 <= receipt[key] <= google_upload.batches.MAX_FILES for key in counts)
                or not 1 <= sum(receipt[key] for key in counts) <= google_upload.batches.MAX_FILES
                or (state == "verified" and (not receipt["files_verified_now"] or receipt["files_pending"] or receipt["files_failed"]))
                or (state == "historical" and (receipt["files_verified_now"] or not receipt["files_previously_verified"]
                                               or receipt["files_pending"] or receipt["files_failed"]))
                or (state == "pending" and (not receipt["files_pending"] or receipt["files_failed"]))
                or (state == "failed" and not receipt["files_failed"])):
            raise BackupError("google_upload_result_invalid")
        destination._write_frame(channels["receipt"], destination._canonical(receipt), destination._deadline(5))
        return {"schema_version": 1, "provider": "google_drive", "batch_state": state,
                **{key: receipt[key] for key in counts}, "current_worker_state_checked": False,
                "account_verified": False, "automatic_deletion": False}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Raspi Cloud Sync Pro development tools")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show this release's capabilities, without probing hardware")
    commands.add_parser("network-status", help="inspect existing USB/Wi-Fi addressing without changing it")
    cloud = commands.add_parser("cloud-check", help="read local rclone backend types without contacting cloud accounts")
    cloud.add_argument("--config", type=Path, required=True)
    cloud.add_argument("--rclone", type=Path, help="reviewed absolute rclone executable; defaults to a protected PATH match")
    export = commands.add_parser("google-export-authorization", help="operator-only private pipe handoff; no cloud request")
    export.add_argument("--state", type=Path, required=True, help="private dashboard state owned by the current non-root user")
    export.add_argument("--public-origin", required=True, help="exact registered HTTPS origin")
    export.add_argument("--output-fd", type=int, required=True, help="dedicated inherited pipe or connected Unix stream, never stdio")
    prepare = commands.add_parser("google-prepare-destination", help="prepare a disabled private Google remote from an inherited pipe")
    prepare.add_argument("--state", type=Path, required=True, help="private backup state owned by the current non-root user")
    prepare.add_argument("--input-fd", type=int, required=True, help="dedicated inherited pipe or connected Unix stream, never stdio")
    prepare.add_argument("--folder-name", required=True, help="proposed app folder name; this does not create or verify it")
    setup_export = commands.add_parser("google-setup-export", help="broker-only private export of a current owner preparation request")
    setup_export.add_argument("--state", type=Path, required=True)
    setup_export.add_argument("--public-origin", required=True)
    setup_export.add_argument("--request-id", required=True)
    setup_export.add_argument("--metadata-fd", type=int, required=True)
    setup_export.add_argument("--authorization-fd", type=int, required=True)
    setup_record = commands.add_parser("google-setup-record", help="broker-only private receipt recording; never enable uploads")
    setup_record.add_argument("--state", type=Path, required=True)
    setup_record.add_argument("--public-origin", required=True)
    setup_record.add_argument("--request-id", required=True)
    setup_record.add_argument("--receipt-fd", type=int, required=True)
    setup_receipt = commands.add_parser("google-setup-receipt", help="privately describe an untested worker preparation")
    setup_receipt.add_argument("--state", type=Path, required=True)
    setup_receipt.add_argument("--request-id", required=True)
    setup_receipt.add_argument("--output-fd", type=int, required=True)
    setup_import = commands.add_parser("google-setup-import", help="prepare under an anchored private backup root as its ordinary identity")
    setup_import.add_argument("--state", type=Path, required=True)
    setup_import.add_argument("--request-id", required=True)
    setup_import.add_argument("--root-fd", type=int, required=True)
    setup_import.add_argument("--input-fd", type=int, required=True)
    setup_import.add_argument("--folder-name", required=True)
    setup_broker = commands.add_parser("google-setup-broker", help="gated Pi-only preparation broker; no network or upload actions")
    setup_broker.add_argument("--config", type=Path, required=True)
    test_broker = commands.add_parser("google-test-broker", help="one explicit root-coordinated owner Test; disabled configuration starts nothing")
    test_broker.add_argument("--config", type=Path, required=True, help="protected root-owned private coordinator configuration")
    control_runner = commands.add_parser("google-control-runner", help="process an existing pending owner decision; disabled configuration starts nothing")
    control_runner.add_argument("--config", type=Path, required=True)
    smb_capture = commands.add_parser("smb-capture", help="one approved local SMB capture scan; never upload or configure the host")
    smb_capture.add_argument("--config", type=Path, required=True, help="nonsecret root-protected configuration readable by the backup identity")
    google_test = commands.add_parser("google-test", help="private owner Test worker; inherited root release required")
    google_test.add_argument("--prepared-state", type=Path, required=True)
    google_test.add_argument("--operational-state", type=Path, required=True)
    google_test.add_argument("--request-fd", type=int, required=True)
    google_test.add_argument("--root-gate-fd", type=int, help="inherited root-authenticated Unix stream; omission refuses Test")
    google_test.add_argument("--receipt-fd", type=int, required=True)
    google_test.add_argument("--rclone", type=Path, required=True, help="reviewed protected absolute executable")
    google_test.add_argument("--rclone-sha256", required=True, help="reviewed protected executable SHA256")
    google_test.add_argument("--timeout", type=float, default=120.0, help="bounded Test deadline, at most 300 seconds")
    google_upload = commands.add_parser("google-upload", help="private selected-batch upload; fresh inherited root release required")
    google_upload.add_argument("--prepared-state", type=Path, required=True)
    google_upload.add_argument("--operational-state", type=Path, required=True)
    google_upload.add_argument("--staging", type=Path, required=True)
    google_upload.add_argument("--batch-id", required=True)
    google_upload.add_argument("--manifest-sha256", required=True)
    google_upload.add_argument("--admission-fd", type=int, required=True)
    google_upload.add_argument("--root-gate-fd", type=int, help="inherited root-authenticated Unix stream; omission refuses upload")
    google_upload.add_argument("--receipt-fd", type=int, required=True)
    google_upload.add_argument("--rclone", type=Path, required=True)
    google_upload.add_argument("--rclone-sha256", required=True)
    google_upload.add_argument("--timeout", type=float, default=300.0, help="bounded batch deadline, at most 300 seconds")
    for command, help_text in (
        ("google-control-status", "read conservative source control status; never inspect a running worker"),
        ("google-control-export", "privately export an existing owner control intent; never create one"),
        ("google-control-record", "record a private coordinator receipt for an existing owner intent"),
        ("google-control-admission", "privately export one fresh admission decision; never start a worker"),
        ("google-control-pending", "privately describe eligibility only; never authorize or start a worker"),
    ):
        control = commands.add_parser(command, help=help_text)
        control.add_argument("--state", type=Path, required=True)
        control.add_argument("--public-origin", required=True)
        if command in ("google-control-export", "google-control-record"):
            control.add_argument("--request-id", required=True)
        if command in ("google-control-export", "google-control-admission", "google-control-pending"):
            control.add_argument("--output-fd", type=int, required=True)
        elif command == "google-control-record":
            control.add_argument("--receipt-fd", type=int, required=True)
    for command, help_text in (
        ("web-batch-describe", "send selected completed-batch metadata only over a private inherited descriptor"),
        ("web-batch-export", "stream one selected completed web batch over a private inherited descriptor"),
        ("web-batch-import", "verify and stage one selected private batch without replacement or cloud upload"),
    ):
        batch = commands.add_parser(command, help=help_text)
        batch.add_argument("--state", type=Path, required=True, help="private web-inbox or backup staging root")
        batch.add_argument("--batch-id", required=True)
        batch.add_argument("--root-fd", type=int, required=True)
        batch.add_argument("--timeout", type=float, default=300.0, help="one bounded transfer deadline in seconds")
        if command == "web-batch-import":
            batch.add_argument("--input-fd", type=int, required=True)
        else:
            batch.add_argument("--output-fd", type=int, required=True)
        if command != "web-batch-describe":
            batch.add_argument("--expected-manifest-sha256", required=True,
                               help="private canonical manifest binding independently obtained by the coordinator")
    wifi = commands.add_parser("wifi-plan", help="validate a non-secret Wi-Fi profile and show its NM mapping")
    wifi.add_argument("config", type=Path)
    account = commands.add_parser("dashboard-init", help="create a dashboard account with a private password prompt")
    account.add_argument("--state", type=Path, required=True)
    account.add_argument("--username", default="owner")
    account.add_argument("--role", choices=("owner", "viewer"), default="owner")
    recovery = commands.add_parser("dashboard-reset-password", help="reset an existing local dashboard account privately")
    recovery.add_argument("--state", type=Path, required=True)
    recovery.add_argument("--username", required=True)
    dashboard = commands.add_parser("dashboard", help="serve the authenticated dashboard on loopback only")
    dashboard.add_argument("--state", type=Path, required=True)
    dashboard.add_argument("--history", type=Path)
    dashboard.add_argument("--worker-state", type=Path, help="read only the completed-batch worker's status directory")
    dashboard.add_argument("--port", type=int, default=8080)
    dashboard.add_argument("--public-origin", help="reviewed HTTPS origin behind the reverse proxy")
    dashboard.add_argument("--google-setup-requests", action="store_true",
                           help="allow owner preparation requests only after the separate setup service is installed")
    dashboard.add_argument("--google-control-requests", action="store_true",
                           help="allow owner Test/Enable/Disable intents only after a reviewed executor is ready")
    for command in ("worker-plan", "backup-staged"):
        worker = commands.add_parser(command, help="inspect or run an opt-in completed-batch backup worker")
        worker.add_argument("--config", type=Path, required=True)
        if command == "backup-staged":
            worker.add_argument("--once", action="store_true")
    check = commands.add_parser("check-config", help="validate a non-secret planning configuration")
    check.add_argument("path", type=Path)
    history = commands.add_parser("history", help="read existing backup history without starting a worker")
    history.add_argument("database", type=Path)
    history.add_argument("--limit", type=int, default=100)
    for command in ("diagnostics", "check-hardware"):
        hardware = commands.add_parser(command, help="read-only Pi USB/power diagnostics")
        hardware.add_argument("--config", type=Path)
        hardware.add_argument("--mode", choices=("mass_storage", "ethernet_samba"), default="ethernet_samba")
    mode = commands.add_parser("mode-plan", help="inspect USB mode policy without applying it")
    mode.add_argument("config", type=Path)
    for command in ("controller-run", "controller-recover"):
        control = commands.add_parser(command, help="code-gated disposable-test controller")
        control.add_argument("--config", type=Path, required=True)
        control.add_argument("--device-policy", type=Path, required=True)
        control.add_argument("--once", action="store_true")
    share = commands.add_parser("share-service", help="code-gated Samba staging/backup service")
    share.add_argument("--config", type=Path, required=True)
    share.add_argument("--once", action="store_true")
    gate = commands.add_parser("device-gate", help="fail unless the reviewed hardware code gate is enabled")
    gate.add_argument("--mode", choices=("mass_storage", "ethernet_samba"))
    gate.add_argument("--device-policy", type=Path)
    private_command = (sys.argv[1:] if argv is None else argv)[:1]
    if private_command in (["google-test"], ["google-test-broker"], ["google-upload"], ["smb-capture"],
                           ["google-control-runner"], ["google-control-pending"]):
        # Private operator arguments can themselves contain private strings.
        # Argparse diagnostics normally echo invalid values and unknown options.
        try:
            with redirect_stderr(io.StringIO()):
                args = parser.parse_args(argv)
        except SystemExit as error:
            if error.code == 0:
                return 0
            message = {"google-test-broker": _GOOGLE_TEST_BROKER_ERROR, "google-test": _GOOGLE_TEST_ERROR,
                       "google-control-runner": _GOOGLE_CONTROL_RUNNER_ERROR, "google-control-pending": _GOOGLE_CONTROL_ERROR,
                       "google-upload": _GOOGLE_UPLOAD_ERROR, "smb-capture": _SMB_CAPTURE_ERROR}[private_command[0]]
            print(message, file=sys.stderr)
            return 2
    else:
        args = parser.parse_args(argv)
    if args.command == "smb-capture":
        try:
            from .smb_capture import SmbCaptureSettings, run_smb_capture
            report = _smb_capture_projection(run_smb_capture(SmbCaptureSettings.load(args.config), once=True))
            print(json.dumps(report, indent=2))
            return 2 if report["capture_state"] in ("blocked", "unavailable") or report["rejected_files"] else 0
        except Exception:
            print(_SMB_CAPTURE_ERROR, file=sys.stderr)
            return 2
    if args.command == "google-test-broker":
        try:
            from .google_test_broker import GoogleTestBrokerSettings, run_google_test_broker
            report = _google_test_broker_projection(run_google_test_broker(GoogleTestBrokerSettings.load(args.config)))
            print(json.dumps(report, indent=2))
            return 2 if report["status"] == "failed" else 0
        except Exception:
            print(_GOOGLE_TEST_BROKER_ERROR, file=sys.stderr)
            return 2
    if args.command == "google-control-runner":
        try:
            from .google_test_broker import GoogleTestBrokerSettings
            from .google_control_runner import run_google_control_runner
            report = _google_control_runner_projection(run_google_control_runner(GoogleTestBrokerSettings.load(args.config)))
            print(json.dumps(report, indent=2))
            return 75 if report["status"] == "deferred" else 2 if report["status"] == "failed" else 0
        except Exception:
            print(_GOOGLE_CONTROL_RUNNER_ERROR, file=sys.stderr)
            return 2
    if args.command == "google-upload":
        try:
            report = _run_private_google_upload(args)
            print(json.dumps(report, indent=2))
            return 0 if report["batch_state"] in ("verified", "historical") else 2
        except Exception:
            print(_GOOGLE_UPLOAD_ERROR, file=sys.stderr)
            return 2
    if args.command == "google-test":
        try:
            report = _run_private_google_test(args)
            print(json.dumps(report, indent=2))
            return 0 if report["test_state"] == "verified" else 2
        except Exception:
            # This is the private worker's public error boundary, including raw
            # subprocess/configuration failures. Never print exception context.
            print(_GOOGLE_TEST_ERROR, file=sys.stderr)
            return 2
    if args.command == "google-control-pending":
        try:
            from . import google_control
            report = google_control.export_google_control_pending(args.state, args.public_origin, args.output_fd)
            print(json.dumps(report, indent=2))
            return 2 if report.get("control_state") in ("invalid", "unavailable") else 0
        except Exception:
            print(_GOOGLE_CONTROL_ERROR, file=sys.stderr)
            return 2
    if args.command in ("google-control-status", "google-control-export", "google-control-record",
                        "google-control-admission"):
        try:
            from . import google_control
            if args.command == "google-control-status":
                report = google_control.google_control_status(args.state, args.public_origin)
            elif args.command == "google-control-export":
                report = google_control.export_requested_google_control(
                    args.state, args.public_origin, args.request_id, args.output_fd)
            elif args.command == "google-control-record":
                report = google_control.record_google_control_receipt(
                    args.state, args.public_origin, args.request_id, args.receipt_fd)
            else:
                report = google_control.export_google_control_admission(args.state, args.public_origin, args.output_fd)
            print(json.dumps(report, indent=2))
            return 2 if report.get("control_state") in ("invalid", "unavailable") else 0
        except (BackupError, OSError, ValueError, TypeError):
            print("Private Google control operation unavailable; no private details were printed.", file=sys.stderr)
            return 2
    if args.command in ("web-batch-describe", "web-batch-export", "web-batch-import"):
        try:
            from . import batch_transfer
            if args.command == "web-batch-describe":
                from . import google_destination
                # Describe returns PRIVATE metadata. Accept/consume the output
                # channel first; the API independently accepts/consumes its root
                # anchor. No batch identity or hash is ever printed to stdout.
                with google_destination._channel(args.output_fd, writing=True) as channel:
                    deadline = batch_transfer._deadline(args.timeout)
                    metadata = batch_transfer.describe_web_batch(args.state, args.batch_id, args.root_fd,
                                                                 timeout=args.timeout)
                    google_destination._write_frame(channel, google_destination._canonical(metadata), deadline)
                report = {"schema_version": 1, "transfer_state": "described", "already_present": False,
                          "uploads_enabled": False, "automatic_deletion": False}
            elif args.command == "web-batch-export":
                report = batch_transfer.export_web_batch(
                    args.state, args.batch_id, args.root_fd, args.output_fd,
                    args.expected_manifest_sha256, timeout=args.timeout)
            else:
                report = batch_transfer.import_web_batch(
                    args.state, args.batch_id, args.root_fd, args.input_fd,
                    args.expected_manifest_sha256, timeout=args.timeout)
            print(json.dumps(report, indent=2))
            return 0
        except (BackupError, OSError, ValueError, TypeError):
            print("Private batch transfer unavailable; no cloud operation was started and no private details were printed.",
                  file=sys.stderr)
            return 2
    if args.command in ("google-setup-export", "google-setup-record", "google-setup-receipt",
                        "google-setup-import", "google-setup-broker"):
        try:
            if args.command == "google-setup-export":
                from .google_setup import export_requested_google_setup
                report = export_requested_google_setup(args.state, args.public_origin, args.request_id,
                                                      args.metadata_fd, args.authorization_fd)
            elif args.command == "google-setup-record":
                from .google_setup import record_google_setup_receipt
                report = record_google_setup_receipt(args.state, args.public_origin, args.request_id, args.receipt_fd)
            elif args.command == "google-setup-receipt":
                from .google_broker import export_google_preparation_receipt
                report = export_google_preparation_receipt(args.state, args.request_id, args.output_fd)
            elif args.command == "google-setup-import":
                from .google_broker import import_requested_google_setup
                report = import_requested_google_setup(args.state, args.request_id, args.root_fd,
                                                       args.input_fd, args.folder_name)
            else:
                from .google_broker import GoogleBrokerSettings, run_google_setup_broker
                report = run_google_setup_broker(GoogleBrokerSettings.load(args.config))
            print(json.dumps(report, indent=2))
            return 2 if report.get("status") == "failed" else 0
        except (BackupError, OSError, ValueError, TypeError):
            print("Google preparation workflow unavailable; no uploads were enabled and no private details were printed.",
                  file=sys.stderr)
            return 2
    if args.command in ("google-export-authorization", "google-prepare-destination"):
        from .google_destination import export_google_authorization, prepare_google_destination
        try:
            if args.command == "google-export-authorization":
                report = export_google_authorization(args.state, args.public_origin, args.output_fd)
            else:
                report = prepare_google_destination(args.state, args.input_fd, args.folder_name)
            print(json.dumps(report, indent=2))
            return 0
        except (BackupError, OSError, ValueError, TypeError):
            print("Google destination preparation unavailable; no uploads were enabled and no credentials were printed.",
                  file=sys.stderr)
            return 2
    if args.command == "cloud-check":
        from .cloud_readiness import cloud_readiness
        from .staged_worker import WorkerSettings
        try:
            report = cloud_readiness(WorkerSettings.load(args.config), executable=args.rclone)
            print(json.dumps(report, indent=2))
            return 0 if report["backend_types_match"] else 2
        except (BackupError, OSError, ValueError, TypeError):
            print("Cloud metadata configuration unavailable; no account was contacted or enabled.", file=sys.stderr)
            return 2
    if args.command == "wifi-plan":
        from .wifi_plan import load_wifi_profile, wifi_plan
        try:
            print(json.dumps(wifi_plan(load_wifi_profile(args.config)), indent=2))
            return 0
        except (BackupError, ValueError):
            print("Invalid non-secret Wi-Fi profile plan", file=sys.stderr)
            return 2
    if args.command in ("worker-plan", "backup-staged"):
        from .staged_worker import WorkerSettings, run_staged_worker
        try:
            settings = WorkerSettings.load(args.config)
            print(json.dumps(settings.plan() if args.command == "worker-plan" else
                             run_staged_worker(settings, once=args.once), indent=2))
            return 0
        except BackupError as exc:
            print(f"Staged worker unavailable: {exc.code}", file=sys.stderr)
        except (OSError, ValueError, TypeError):
            print("Staged worker configuration or I/O error", file=sys.stderr)
        return 2
    if args.command in ("dashboard-init", "dashboard-reset-password", "dashboard"):
        try:
            from .dashboard import initialize_dashboard, serve_dashboard
            if args.command == "dashboard-init":
                initialize_dashboard(args.state, username=args.username, role=args.role)
                print("Dashboard account created. No network or backup service was changed.")
            elif args.command == "dashboard-reset-password":
                from .accounts import reset_dashboard_password
                reset_dashboard_password(args.state, username=args.username)
                print("Dashboard password reset. This account's existing sessions are revoked on their next request.")
            else:
                serve_dashboard(args.state, history=args.history, worker_state=args.worker_state, port=args.port,
                                public_origin=args.public_origin, google_setup_requests=args.google_setup_requests,
                                enable_google_control_requests=args.google_control_requests)
            return 0
        except ImportError:
            print("Install the optional dashboard dependencies in a virtual environment.", file=sys.stderr)
        except (OSError, ValueError, TypeError, BackupError):
            print("Dashboard configuration or I/O error; no credentials were printed.", file=sys.stderr)
        return 2
    if args.command == "network-status":
        from .network_diagnostics import network_status
        print(json.dumps(network_status(), indent=2))
        return 0
    if args.command == "status":
        print(json.dumps({
            "project": "Raspi Cloud Sync Pro",
            "version": __version__,
            "stage": "phase_2",
            "hardware_access": "disabled",
            "cloud_sync": "adapters_available_service_disabled",
            "backup_engine": "staged_files_only",
            "wifi_management": "validation_only",
            "automatic_deletion": False,
            "preferred_usb_mode": "ethernet_samba",
            "live_mode_switch": "disabled",
            "dashboard": "optional_authenticated_preview",
        }, indent=2))
        return 0
    if args.command in ("diagnostics", "check-hardware", "mode-plan", "controller-run",
                        "controller-recover", "share-service", "device-gate"):
        try:
            if args.command == "device-gate":
                from .linux_gadget import require_hardware_test_approval
                require_hardware_test_approval()
                if args.mode:
                    from .device_modes import DevicePolicy
                    from .linux_gadget import LEGACY_GADGET_MODULES
                    if args.device_policy is None or DevicePolicy.load(args.device_policy).mode.value != args.mode:
                        raise BackupError("selected_device_mode_mismatch")
                    if args.mode == "ethernet_samba" and (
                        not Path("/sys/module/g_ether").exists()
                        or any(Path("/sys/module", name).exists() for name in LEGACY_GADGET_MODULES if name != "g_ether")
                    ):
                        raise BackupError("ethernet_provider_unverified")
                return 0
            if args.command in ("diagnostics", "check-hardware"):
                from .hardware import readiness
                from .linux_gadget import GadgetSettings
                from .device_modes import DevicePolicy
                settings = GadgetSettings.load(args.config) if args.config and args.mode == "mass_storage" else None
                policy = DevicePolicy.load(args.config) if args.config and args.mode == "ethernet_samba" else None
                report = readiness(settings=settings, mode=args.mode, policy=policy)
                print(json.dumps(report, indent=2))
                return 0 if args.command == "diagnostics" else 2
            if args.command == "mode-plan":
                from .device_modes import DeviceMode, DevicePolicy
                from .ethernet import OfficialEthernet
                policy = DevicePolicy.load(args.config)
                print(json.dumps(OfficialEthernet().plan(policy) if policy.mode == DeviceMode.ETHERNET_SAMBA
                                 else policy.plan(), indent=2))
                return 0
            from .device_service import run_controller, run_share_service
            if args.command == "share-service":
                result = run_share_service(args.config, once=args.once)
            else:
                result = run_controller(args.config, args.device_policy,
                                        recover=args.command == "controller-recover", once=args.once)
            print(json.dumps(result))
            return 0
        except BackupError as exc:
            print(f"Device operation unavailable: {exc.code}", file=sys.stderr)
            return 2
        except (OSError, ValueError, TypeError, KeyError):
            print("Device configuration or I/O error", file=sys.stderr)
            return 2
    if args.command == "history":
        try:
            print(json.dumps(read_history(args.database, args.limit), indent=2))
        except BackupError as exc:
            print(f"Cannot read backup history: {exc.code}", file=sys.stderr)
            return 2
        return 0
    try:
        load_config(args.path)
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    print("Configuration valid. Hardware access and cloud sync remain disabled.")
    return 0
