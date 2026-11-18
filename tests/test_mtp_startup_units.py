"""Offline safety contracts for optional MTP startup unit templates."""
import ast
import configparser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
RUNTIME = REPOSITORY / "tools/experimental/mtp/runtime"
STARTUP = RUNTIME / "startup"
LAUNCHER = "/opt/raspi-cloud-sync-pro/experimental/mtp/start-mtp"
PREPARE = "raspi-cloud-mtp-prepare.service"
HOST_CHECK = "raspi-cloud-mtp-host-check.service"
BROKER = "raspi-cloud-mtp-completion.service"
PRODUCER = "raspi-cloud-mtp-responder-experiment.service"
BINDER = "raspi-cloud-mtp-bind.service"
TARGET = "raspi-cloud-mtp.target"
MSC = "raspi-cloud-mass-storage-appliance.service"


def unit(path):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(path.read_text())
    return parser


class MtpStartupUnitTests(unittest.TestCase):
    def test_only_explicit_target_has_install_enablement(self):
        for path in STARTUP.rglob("*"):
            if path.suffix not in (".service", ".target", ".conf"):
                continue
            value = unit(path)
            with self.subTest(path=path.name):
                if path.name == TARGET:
                    self.assertEqual(value["Install"]["WantedBy"], "multi-user.target")
                else:
                    self.assertFalse(value.has_section("Install"))

    def test_host_mount_operations_are_fixed_and_do_not_cleanup_or_restart(self):
        for name, action in ((PREPARE, "prepare"), (BINDER, "bind")):
            service = unit(STARTUP / name)["Service"]
            with self.subTest(unit=name):
                self.assertEqual(service["ExecStart"], LAUNCHER + " " + action)
                self.assertEqual(service["Type"], "oneshot")
                self.assertEqual(service["RemainAfterExit"], "yes")
                self.assertEqual(service["Restart"], "no")
                self.assertEqual(service["PrivateMounts"], "no")
                self.assertEqual(service["User"], "root")
                self.assertIn("CAP_SYS_PTRACE", service["CapabilityBoundingSet"].split())
                self.assertNotIn("ExecStop", service)
                self.assertNotIn("ExecStopPost", service)
                for setting in ("ProtectSystem", "ProtectHome", "PrivateTmp", "PrivateDevices",
                                "RootDirectory", "RootImage", "BindPaths", "ReadWritePaths"):
                    self.assertNotIn(setting, service)
        self.assertIn("CAP_SYS_MODULE", unit(STARTUP / PREPARE)["Service"]["CapabilityBoundingSet"])
        self.assertNotIn("CAP_SYS_MODULE", unit(STARTUP / BINDER)["Service"]["CapabilityBoundingSet"])

    def test_broker_guard_preserves_long_running_sandbox_and_execstart(self):
        value = unit(STARTUP / (BROKER + ".d/30-mtp-startup.conf"))
        self.assertEqual(value.sections(), ["Unit"])
        self.assertEqual(set(value["Unit"]["Requires"].split()), {PREPARE, HOST_CHECK})
        self.assertEqual(set(value["Unit"]["After"].split()), {PREPARE, HOST_CHECK})
        # No broker Service override may relax the accepted long-running
        # sandbox or reintroduce a privileged guard in its mount namespace.
        broker = unit(RUNTIME / BROKER)["Service"]
        self.assertEqual(broker["ExecStart"],
                         "/opt/raspi-cloud-sync-pro/experimental/mtp/serve-completions"
                         " --config /etc/raspi-cloud-sync-pro/mtp-experiment.json")
        self.assertEqual(broker["User"], "root")
        self.assertEqual(broker["Group"], "raspi-mtp")
        for setting in ("PrivateNetwork", "PrivateDevices", "NoNewPrivileges"):
            self.assertEqual(broker[setting], "yes")
        self.assertEqual(broker["ProtectSystem"], "strict")
        self.assertEqual(broker["ProtectHome"], "yes")
        self.assertEqual(broker["RestrictAddressFamilies"], "AF_UNIX")

    def test_host_check_is_fresh_fixed_read_only_and_not_a_usb_lifecycle(self):
        value = unit(STARTUP / HOST_CHECK)
        settings, service = value["Unit"], value["Service"]
        self.assertEqual(settings["Requires"], PREPARE)
        self.assertEqual(settings["After"], PREPARE)
        self.assertEqual(settings["Before"], BROKER)
        self.assertNotIn("BindsTo", settings)
        # A failed approval check must fail activation, not become a skipped
        # condition that systemd treats as a successful requirement.
        self.assertFalse(any(name.startswith(("Condition", "Assert")) for name in settings))
        self.assertEqual(service["ExecStart"], LAUNCHER + " check")
        self.assertEqual(service["Type"], "oneshot")
        self.assertEqual(service["RemainAfterExit"], "no")
        self.assertEqual(service["Restart"], "no")
        self.assertEqual(service["User"], "root")
        self.assertEqual(service["Group"], "root")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["PrivateMounts"], "no")
        self.assertEqual(set(service["CapabilityBoundingSet"].split()),
                         {"CAP_SYS_PTRACE", "CAP_DAC_OVERRIDE"})
        for setting in ("ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost",
                        "ProtectSystem", "ProtectHome", "PrivateTmp", "PrivateDevices",
                        "PrivateNetwork", "PrivateUsers", "DynamicUser", "ProtectProc",
                        "RootDirectory", "RootImage", "BindPaths", "BindReadOnlyPaths",
                        "ReadWritePaths", "ReadOnlyPaths", "InaccessiblePaths",
                        "TemporaryFileSystem", "MountAPIVFS", "RuntimeDirectory"):
            self.assertNotIn(setting, service)

    def test_fail_closed_dependency_chain_has_no_cloud_or_disruptive_conflicts(self):
        expected = {PREPARE, BROKER, PRODUCER}
        binder = unit(STARTUP / BINDER)["Unit"]
        self.assertEqual(set(binder["Requires"].split()), expected)
        self.assertTrue(expected <= set(binder["After"].split()))
        self.assertEqual(set(binder["BindsTo"].split()), {BROKER, PRODUCER})
        producer = unit(STARTUP / (PRODUCER + ".d/30-mtp-startup.conf"))["Unit"]
        self.assertEqual(producer["BindsTo"], BROKER)
        self.assertIn(BROKER, producer["After"].split())
        for path in STARTUP.rglob("*"):
            if path.suffix not in (".service", ".target", ".conf"):
                continue
            value = unit(path)
            for section in value.sections():
                self.assertNotIn("Conflicts", value[section])
                for setting, text in value[section].items():
                    if setting in ("After", "Before", "Requires", "Wants", "BindsTo"):
                        self.assertNotIn("network-online", text)
                        self.assertNotIn("mass-storage-backup", text)

    def test_old_msc_interlock_prevents_only_future_starts(self):
        value = unit(STARTUP / (MSC + ".d/30-mtp-startup-interlock.conf"))
        self.assertEqual(value.sections(), ["Service"])
        self.assertEqual(dict(value["Service"]), {
            "ExecCondition": "/usr/bin/test ! -e /etc/raspi-cloud-sync-pro/MTP_STARTUP_APPROVED"})

    def test_startup_order_has_no_cycle(self):
        nodes = {PREPARE, HOST_CHECK, BROKER, PRODUCER, BINDER, TARGET}
        edges = {name: set() for name in nodes}
        for name in nodes:
            path = STARTUP / name
            if not path.exists():
                path = STARTUP / (name + ".d/30-mtp-startup.conf")
            settings = unit(path)["Unit"]
            for other in settings.get("After", "").split():
                if other in nodes:
                    edges[other].add(name)
            for other in settings.get("Before", "").split():
                if other in nodes:
                    edges[name].add(other)
        ordered = []
        while edges:
            ready = [name for name in edges if all(name not in targets for targets in edges.values())]
            self.assertTrue(ready, "MTP startup ordering contains a cycle")
            for name in ready:
                ordered.append(name)
                del edges[name]
        self.assertLess(ordered.index(PREPARE), ordered.index(HOST_CHECK))
        self.assertLess(ordered.index(HOST_CHECK), ordered.index(BROKER))
        self.assertLess(ordered.index(BROKER), ordered.index(PRODUCER))
        self.assertLess(ordered.index(PRODUCER), ordered.index(BINDER))
        self.assertLess(ordered.index(BINDER), ordered.index(TARGET))

    def test_fixed_launcher_has_no_embedded_install_or_service_commands(self):
        source = (STARTUP / "start-mtp").read_text()
        ast.parse(source)
        self.assertIn("from raspi_cloud_sync_pro.mtp_startup import main", source)
        self.assertIn("sys.dont_write_bytecode = True", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("systemctl", source)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is unavailable")
    def test_systemd_verifies_offline_unit_bundle_without_starting_services(self):
        # An isolated filesystem tree supplies inert executable placeholders and
        # dependency stubs. systemd-analyze only parses it; nothing is installed.
        with tempfile.TemporaryDirectory(prefix="rcsp-mtp-unit-verify-") as directory:
            root = Path(directory)
            units = root / "etc/systemd/system"
            units.mkdir(parents=True)
            for path in STARTUP.rglob("*"):
                if path.suffix in (".service", ".target", ".conf"):
                    destination = units / path.relative_to(STARTUP)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
            shutil.copyfile(RUNTIME / BROKER, units / BROKER)
            for name in (PRODUCER, MSC, "systemd-modules-load.service"):
                (units / name).write_text("[Service]\nType=oneshot\nExecStart=/usr/bin/true\n")
            for name in ("local-fs.target", "multi-user.target", "basic.target", "sysinit.target",
                         "shutdown.target", "sockets.target", "slices.target"):
                (units / name).write_text("[Unit]\nDescription=Offline verification dependency\n")
            (units / "sys-kernel-config.mount").write_text(
                "[Mount]\nWhat=configfs\nWhere=/sys/kernel/config\nType=configfs\n")
            for command in (LAUNCHER, "/opt/raspi-cloud-sync-pro/experimental/mtp/serve-completions",
                            "/usr/bin/test", "/usr/bin/true"):
                executable = root / command.lstrip("/")
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n")
                executable.chmod(0o755)
            result = subprocess.run([shutil.which("systemd-analyze"), "verify", "--root=" + directory,
                                     str(units / TARGET), str(units / PREPARE), str(units / BINDER),
                                     str(units / HOST_CHECK),
                                     str(units / BROKER), str(units / PRODUCER), str(units / MSC)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
