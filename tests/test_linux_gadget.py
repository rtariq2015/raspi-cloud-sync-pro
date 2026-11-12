import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.cli import main
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.hardware import power_diagnostics, readiness
from raspi_cloud_sync_pro.linux_gadget import GadgetSettings, LinuxGadgetBackend, LiveLinuxIO


class RecordingLinuxIO:
    """In-memory device tree and command results; never executes commands."""
    def __init__(self):
        self.commands, self.writes, self.directories = [], [], []
        self.values = {}
        self.identity = (1, 2, 1048576)
        self.loop_read_only = True
        self.mountinfo = ""
        self.power_value = "throttled=0x0"
        self.module = None

    def regular_image(self, path): return self.identity
    def protected_directory(self, path): return None
    def read(self, path):
        path = str(path)
        if path == "/proc/self/mountinfo": return self.mountinfo
        if path.endswith("/state"): return "configured"
        if path.endswith("/removable"): return "1"
        if path.endswith(("/ro", "/nofua")): return "0"
        return self.values.get(path, "")
    def power(self): return self.power_value
    def exists(self, path): return str(path) == self.module or str(path) in self.values
    def children(self, path):
        path = Path(path)
        if path.name == "functions": return [path / "mass_storage.0"]
        if path.name == "configs": return [path / "c.1"]
        if path.name == "mass_storage.0": return [path / "lun.0"]
        return []
    def link_matches(self, link, target): return True
    def mkdir(self, path): self.directories.append(str(path))
    def write(self, path, value):
        self.writes.append((str(path), value))
        self.values[str(path)] = value
    def symlink(self, source, destination): self.writes.append((str(destination), str(source)))
    def command(self, arguments):
        self.commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "/dev/loop42" if "--find" in arguments else "", "")
    def query(self, arguments):
        if arguments[0] == "losetup":
            data = {"loopdevices": [{"name": "/dev/loop42", "back-file": "/protected/disposable.img",
                                     "ro": self.loop_read_only}]}
        else:
            data = {"blockdevices": [{"name": "/dev/loop42", "maj:min": "7:42", "ro": self.loop_read_only,
                                      "children": [{"name": "/dev/loop42p1", "maj:min": "259:1",
                                                    "ro": self.loop_read_only}]}]}
        return subprocess.CompletedProcess(arguments, 0, json.dumps(data), "")


class LinuxBackendTests(unittest.TestCase):
    def setUp(self):
        self.settings = GadgetSettings(Path("/protected/disposable.img"), Path("/run/disposable-mount"),
                                       Path("/protected/state"), Path("/protected/staging"), "mock-udc")
        self.io = RecordingLinuxIO()
        self.backend = LinuxGadgetBackend(self.settings, self.io)

    def test_production_gate_precedes_subprocess_device_and_file_mutations(self):
        live = LiveLinuxIO()
        with mock.patch("subprocess.run") as run, mock.patch("pathlib.Path.write_text") as write:
            for operation in (
                lambda: live.command(["mount", "fake", "fake"]),
                lambda: live.write("/sys/mock", "1"), lambda: live.mkdir("/sys/mock"),
                lambda: live.symlink("/sys/a", "/sys/b"),
                lambda: live.provision_command(["rpi-usb-gadget", "on"]),
            ):
                with self.assertRaisesRegex(BackupError, "hardware_operations_disabled"): operation()
        run.assert_not_called()
        write.assert_not_called()

    def test_disabled_cli_never_reads_config_or_creates_state(self):
        with mock.patch("pathlib.Path.read_text") as read, mock.patch("subprocess.run") as run, \
                redirect_stderr(io.StringIO()):
            for command in ("controller-run", "controller-recover"):
                self.assertEqual(main([command, "--config", "/not-present", "--device-policy", "/not-present"]), 2)
            self.assertEqual(main(["share-service", "--config", "/not-present"]), 2)
            self.assertEqual(main(["device-gate", "--mode", "ethernet_samba", "--device-policy", "/not-present"]), 2)
        read.assert_not_called()
        run.assert_not_called()

    def test_loop_and_mount_require_read_only_and_partition(self):
        self.backend.verify_loop_read_only("/dev/loop42")
        self.io.loop_read_only = False
        with self.assertRaisesRegex(BackupError, "read_only"):
            self.backend.verify_loop_read_only("/dev/loop42")
        with self.assertRaises(BackupError): self.backend.detach_loop("/dev/sda")

    def test_mount_verification_uses_device_numbers_and_all_safe_flags(self):
        self.io.mountinfo = "20 1 259:1 / /run/disposable-mount ro,nosuid,nodev,noexec - exfat /dev/alias ro"
        self.backend.verify_mount_read_only("/dev/loop42")
        self.io.mountinfo = self.io.mountinfo.replace("ro,nosuid", "rw,nosuid")
        with self.assertRaisesRegex(BackupError, "mount_not_read_only"):
            self.backend.verify_mount_read_only("/dev/loop42")

    def test_fsck_is_nonrepairing_and_loop_detach_refuses_mounts(self):
        self.backend.check_filesystem("/dev/loop42")
        self.assertEqual(self.io.commands[-1], ["fsck.exfat", "-n", "/dev/loop42p1"])
        self.io.mountinfo = "20 1 259:1 / /somewhere ro - exfat /dev/loop42p1 ro"
        with self.assertRaisesRegex(BackupError, "still_mounted"):
            self.backend.detach_loop("/dev/loop42")

    def test_detach_never_clears_loaded_lun_or_uses_forced_eject(self):
        self.io.values[str(self.backend.lun / "file")] = str(self.settings.image)
        with self.assertRaisesRegex(BackupError, "release_unproven"): self.backend.detach_gadget()
        self.assertEqual(self.io.writes, [])
        self.io.values[str(self.backend.lun / "file")] = ""
        self.backend.detach_gadget()
        self.assertEqual(self.io.writes, [(str(self.backend.root / "UDC"), "")])

    def test_prepare_creates_detached_profile_and_rejects_competing_drivers(self):
        self.backend.prepare()
        self.assertIn((str(self.backend.lun / "nofua"), "0"), self.io.writes)
        self.assertFalse(any(path.endswith("/UDC") for path, value in self.io.writes))
        self.io.module = "/sys/module/g_ether"
        with self.assertRaisesRegex(BackupError, "conflicting_gadget_driver"): self.backend.prepare()

    def test_changed_image_identity_is_refused(self):
        self.backend._pin_image()
        self.io.identity = (1, 999, 1048576)
        with self.assertRaisesRegex(BackupError, "identity_changed"): self.backend._pin_image()

    def test_extra_gadget_functions_are_rejected_as_unvalidated_composite(self):
        self.assertTrue(self.backend.observe().profile_ok)
        children = self.io.children
        with mock.patch.object(self.io, "children", side_effect=lambda path:
                               [self.backend.function, self.backend.root / "functions/ncm.0"]
                               if Path(path).name == "functions" else children(path)):
            self.assertFalse(self.backend.observe().profile_ok)

    def test_invalid_paths_and_identifiers_rejected(self):
        with self.assertRaises(BackupError):
            GadgetSettings(Path("relative"), Path("/mount"), Path("/state"), Path("/staging"), "udc")
        with self.assertRaises(BackupError):
            GadgetSettings(Path("/data/image"), Path("/data"), Path("/state"), Path("/staging"), "udc")

    def test_power_unknown_current_and_historical_faults_are_separate(self):
        self.assertTrue(power_diagnostics(self.io)["current_ok"])
        self.io.power_value = "throttled=0x10000"
        report = power_diagnostics(self.io)
        self.assertTrue(report["current_ok"])
        self.assertEqual(report["historical_faults"], ["undervoltage"])
        self.io.power_value = "throttled=0x1"
        self.assertFalse(power_diagnostics(self.io)["current_ok"])
        self.io.power_value = "unknown"
        self.assertIsNone(power_diagnostics(self.io)["current_ok"])

    def test_readiness_never_authorizes_hardware_and_reports_missing_prerequisites(self):
        report = readiness(self.io)
        self.assertFalse(report["ready_for_hardware_operations"])
        self.assertFalse(report["prerequisites_ok"])
        self.assertEqual(self.io.commands, [])
