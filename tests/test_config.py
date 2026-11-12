import copy
import json
import tempfile
import unittest
from pathlib import Path

from raspi_cloud_sync_pro.config import ConfigError, load_config, validate_config

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.example = json.loads((ROOT / "config/config.example.json").read_text())

    def test_example_has_safe_defaults(self):
        settings = load_config(ROOT / "config/config.example.json")
        self.assertEqual(settings.provider, "disabled")
        self.assertFalse(settings.sync_enabled)
        self.assertFalse(settings.automatic_deletion)

    def test_planned_providers_remain_disabled(self):
        for provider in ("koofr_webdav", "google_drive_rclone"):
            with self.subTest(provider=provider):
                self.example["sync"]["provider"] = provider
                settings = validate_config(self.example)
                self.assertEqual(settings.provider, provider)
                self.assertFalse(settings.sync_enabled)

    def test_sync_and_deletion_cannot_be_enabled_or_coerced(self):
        for section, field in (("sync", "enabled"), ("safety", "automatic_deletion")):
            for value in (True, 0, 1, "false", "true", None, [], {}):
                with self.subTest(section=section, value=value):
                    data = copy.deepcopy(self.example)
                    data[section][field] = value
                    with self.assertRaises(ConfigError):
                        validate_config(data)

    def test_malformed_shapes_and_unknown_fields_are_rejected(self):
        cases = [None, [], "config", {}, {**self.example, "password": "do-not-echo"}]
        for section in ("sync", "safety"):
            for value in (None, [], False, {}, {"unexpected": 1}):
                cases.append({**self.example, section: value})
        for version in (True, "1", 1.0, 2, None):
            cases.append({**self.example, "schema_version": version})
        for provider in ("dropbox", [], {}, None, 1):
            cases.append({**self.example, "sync": {"provider": provider, "enabled": False}})
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ConfigError) as result:
                validate_config(case)
            self.assertNotIn("do-not-echo", str(result.exception))

    def test_missing_file_and_invalid_text_have_controlled_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with self.assertRaisesRegex(ConfigError, "cannot read"):
                load_config(path)
            for contents in (b'{"secret":"do-not-echo",broken}', b"\xff"):
                path.write_bytes(contents)
                with self.assertRaises(ConfigError) as result:
                    load_config(path)
                self.assertNotIn("do-not-echo", str(result.exception))

    def test_duplicate_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for contents in (
                '{"schema_version":1,"schema_version":1,"sync":{},"safety":{}}',
                '{"schema_version":1,"sync":{"provider":"disabled","enabled":true,'
                '"enabled":false},"safety":{"automatic_deletion":false}}',
            ):
                path.write_text(contents)
                with self.assertRaisesRegex(ConfigError, "duplicate"):
                    load_config(path)
