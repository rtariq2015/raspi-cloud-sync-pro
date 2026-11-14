"""Development diagnostics, an authenticated dashboard and code-gated device tools."""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import ConfigError, load_config
from .errors import BackupError
from .history import read_history


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
    for command, help_text in (
        ("google-control-status", "read conservative source control status; never inspect a running worker"),
        ("google-control-export", "privately export an existing owner control intent; never create one"),
        ("google-control-record", "record a private coordinator receipt for an existing owner intent"),
        ("google-control-admission", "privately export one fresh admission decision; never start a worker"),
    ):
        control = commands.add_parser(command, help=help_text)
        control.add_argument("--state", type=Path, required=True)
        control.add_argument("--public-origin", required=True)
        if command in ("google-control-export", "google-control-record"):
            control.add_argument("--request-id", required=True)
        if command in ("google-control-export", "google-control-admission"):
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
    args = parser.parse_args(argv)
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
