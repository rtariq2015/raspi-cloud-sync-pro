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
    wifi = commands.add_parser("wifi-plan", help="validate a non-secret Wi-Fi profile and show its NM mapping")
    wifi.add_argument("config", type=Path)
    account = commands.add_parser("dashboard-init", help="create a dashboard account with a private password prompt")
    account.add_argument("--state", type=Path, required=True)
    account.add_argument("--username", default="owner")
    account.add_argument("--role", choices=("owner", "viewer"), default="owner")
    dashboard = commands.add_parser("dashboard", help="serve the authenticated dashboard on loopback only")
    dashboard.add_argument("--state", type=Path, required=True)
    dashboard.add_argument("--history", type=Path)
    dashboard.add_argument("--worker-state", type=Path, help="read only the completed-batch worker's status directory")
    dashboard.add_argument("--port", type=int, default=8080)
    dashboard.add_argument("--public-origin", help="reviewed HTTPS origin behind the reverse proxy")
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
    if args.command in ("dashboard-init", "dashboard"):
        try:
            from .dashboard import initialize_dashboard, serve_dashboard
            if args.command == "dashboard-init":
                initialize_dashboard(args.state, username=args.username, role=args.role)
                print("Dashboard account created. No network or backup service was changed.")
            else:
                serve_dashboard(args.state, history=args.history, worker_state=args.worker_state, port=args.port,
                                public_origin=args.public_origin)
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
