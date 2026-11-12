"""Validate non-secret planning settings without accessing hardware or the cloud."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

PROVIDERS = ("disabled", "koofr_webdav", "google_drive_rclone")


class ConfigError(ValueError):
    """The configuration is invalid or requests an unavailable operation."""


@dataclass(frozen=True)
class Settings:
    provider: str
    sync_enabled: bool = False
    automatic_deletion: bool = False


def _object(value: Any, fields: set, section: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ConfigError(f"{section} has unsupported or missing fields")
    return value


def validate_config(data: Any) -> Settings:
    """Reject unknown keys, non-boolean flags, deletion, and premature sync enablement."""
    root = _object(data, {"schema_version", "sync", "safety"}, "configuration")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ConfigError("schema_version must be the integer 1")
    sync = _object(root["sync"], {"provider", "enabled"}, "sync")
    safety = _object(root["safety"], {"automatic_deletion"}, "safety")
    if not isinstance(sync["provider"], str) or sync["provider"] not in PROVIDERS:
        raise ConfigError("sync.provider is unsupported")
    if sync["enabled"] is not False:
        raise ConfigError("sync.enabled must be false; service integration remains disabled")
    if safety["automatic_deletion"] is not False:
        raise ConfigError("safety.automatic_deletion must be false")
    return Settings(provider=sync["provider"])


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("duplicate configuration field")
        result[key] = value
    return result


def load_config(path: Path) -> Settings:
    """Read JSON, keeping file contents and possible secrets out of error messages."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError) as exc:
        raise ConfigError("cannot read configuration file") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError("configuration must contain valid JSON") from exc
    return validate_config(data)
