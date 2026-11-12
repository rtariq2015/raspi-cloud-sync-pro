"""Read-only development commands; no mount, setup, sync, or deletion commands."""

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
    check = commands.add_parser("check-config", help="validate a non-secret planning configuration")
    check.add_argument("path", type=Path)
    history = commands.add_parser("history", help="read existing backup history without starting a worker")
    history.add_argument("database", type=Path)
    history.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps({
            "project": "Raspi Cloud Sync Pro",
            "version": __version__,
            "stage": "phase_1",
            "hardware_access": "disabled",
            "cloud_sync": "adapters_available_service_disabled",
            "backup_engine": "staged_files_only",
            "wifi_management": "validation_only",
            "automatic_deletion": False,
        }, indent=2))
        return 0
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
