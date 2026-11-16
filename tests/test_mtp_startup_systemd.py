"""Opt-in real-systemd regression; only unique disposable inert fixture units.

Normal unit-test discovery skips this test. Run on an explicitly approved test
machine/CI runner as root with RCSP_TEST_SYSTEMD=1. It never addresses a USB
gadget, actual Cloud Stick unit, network connection, credential or cloud API.
"""
import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import time
import unittest
import uuid


REPOSITORY = Path(__file__).resolve().parents[1]
RUNTIME = REPOSITORY / "tools/experimental/mtp/runtime"
STARTUP = RUNTIME / "startup"
BROKER = "raspi-cloud-mtp-completion.service"
PRODUCER = "raspi-cloud-mtp-responder-experiment.service"
PREPARE = "raspi-cloud-mtp-prepare.service"
VERIFY = "raspi-cloud-mtp-host-check.service"
BINDER = "raspi-cloud-mtp-bind.service"
LEGACY = "rcsp-disposable-legacy-precheck.service"

HELPER = r'''
import errno,json,os,sys,time
from pathlib import Path
root=Path(sys.argv[1]);action=sys.argv[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(root/'site'))
from raspi_cloud_sync_pro.mtp_startup import require_host_mount_namespace,StartupError
work=root/'data/work';stage=root/'data/staging'
def record(value):
    value.update(action=action,pid=os.getpid(),mount_namespace=os.readlink('/proc/self/ns/mnt'),
                 monotonic_ns=time.monotonic_ns(),host_mount_namespace=(
                     os.readlink('/proc/1/ns/mnt')if action in('check','legacy-check')
                     else(root/'host-mount-namespace').read_text().strip()))
    with (work/'events.jsonl').open('a')as stream:
        stream.write(json.dumps(value,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
def mount_id(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:return next(int(line.split()[1])for line in Path('/proc/self/fdinfo/'+str(fd)).read_text().splitlines()if line.startswith('mnt_id:'))
    finally:os.close(fd)
if action in('check','legacy-check'):
    try:
        require_host_mount_namespace()
        if (root/'refuse').exists():raise StartupError('fixture_approval_refused')
    except StartupError as error:
        record({'passed':False,'code':error.code});raise SystemExit(19)
    record({'passed':True})
elif action=='prepare':record({'passed':True})
elif action=='bind':record({'passed':True})
elif action=='broker':
    try:
        (root/'data/private/credential').read_bytes();private_readable=True
    except OSError:private_readable=False
    try:
        (root/'data/export/forbidden').write_bytes(b'forbidden');export_error=0
    except OSError as error:export_error=error.errno
    status=dict(line.split(':',1)for line in Path('/proc/self/status').read_text().splitlines()if':'in line)
    record({'passed':True,'private_credential_readable':private_readable,
            'export_write_errno':export_error,'work_mount_id':mount_id(work),
            'staging_mount_id':mount_id(stage),'no_new_privileges':status['NoNewPrivs'].strip(),
            'effective_capabilities':status['CapEff'].strip()})
    while True:time.sleep(1)
elif action=='producer':
    while True:time.sleep(1)
else:raise SystemExit(64)
'''


def parse_unit(path):
    value = configparser.ConfigParser(interpolation=None)
    value.optionxform = str
    value.read_string(path.read_text())
    return value


@unittest.skipUnless(os.environ.get("RCSP_TEST_SYSTEMD") == "1",
                     "real systemd fixtures require explicit RCSP_TEST_SYSTEMD=1 approval")
class RealMtpStartupSystemdTests(unittest.TestCase):
    def setUp(self):
        # An explicitly requested run must fail, rather than pass as skipped, if
        # its manager/privilege prerequisites are absent.
        self.assertEqual(os.geteuid(), 0, "The opt-in isolated test requires root")
        self.assertEqual(Path("/proc/1/comm").read_text().strip(), "systemd")
        self.assertIsNotNone(shutil.which("systemctl"))
        self.tag = "rcsp-mtp-ci-" + uuid.uuid4().hex
        self.root = Path("/run") / self.tag
        self.assertFalse(os.path.lexists(self.root))
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        info = self.root.lstat()
        self.root_identity = (info.st_dev, info.st_ino)
        self.units = {name: self.tag + "-" + suffix + ".service" for name, suffix in
                      ((PREPARE, "prepare"), (VERIFY, "verify"), (BROKER, "broker"),
                       (PRODUCER, "producer"), (BINDER, "binder"), (LEGACY, "legacy"))}
        self.created = []
        self.unit_digests = {}
        self.test_passed = False
        self.addCleanup(self.cleanup_fixture)
        self.manager_version = self.command("show", "--property=Version", "--value").stdout.strip()
        self.assertTrue(self.manager_version)
        (self.root / "host-mount-namespace").write_text(os.readlink("/proc/1/ns/mnt") + "\n")
        for name in ("work", "staging", "export", "private"):
            (self.root / "data" / name).mkdir(parents=True, mode=0o700)
        (self.root / "approved").write_text("synthetic fixture only\n")
        self.secret = b"synthetic fixture credential; no real account data\n"
        (self.root / "data/private/credential").write_bytes(self.secret)
        (self.root / "data/private/credential").chmod(0o600)
        (self.root / "helper.py").write_text(textwrap.dedent(HELPER))
        # Copy standard-library-only application source into the isolated /run
        # fixture so ProtectHome cannot conceal the guard imported by helpers.
        shutil.copytree(REPOSITORY / "src/raspi_cloud_sync_pro",
                        self.root / "site/raspi_cloud_sync_pro",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.write_fixture_units()
        self.command("daemon-reload")

    def command(self, *arguments, check=True):
        result = subprocess.run(["systemctl", "--no-pager", *arguments],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=25)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def executable(self, action):
        return "/usr/bin/python3 -I -B " + str(self.root / "helper.py") + " " + str(self.root) + " " + action

    def rewrite(self, text):
        for original, fixture in self.units.items():
            text = text.replace(original, fixture)
        return text

    def install(self, name, text):
        # Never overwrite any existing unit, and never enable a fixture unit.
        path = Path("/run/systemd/system") / self.units[name]
        self.assertFalse(os.path.lexists(path))
        with path.open("x") as stream:
            stream.write(self.rewrite(text))
        path.chmod(0o644)
        self.created.append(path)
        self.unit_digests[path] = hashlib.sha256(path.read_bytes()).hexdigest()

    def write_fixture_units(self):
        self.install(PREPARE, "[Unit]\nStartLimitIntervalSec=0\n[Service]\n"
                     "Type=oneshot\nRemainAfterExit=yes\nExecStart=" + self.executable("prepare") + "\n")
        # Exercise the actual host-check template, not a recreated dependency.
        verify = (STARTUP / VERIFY).read_text()
        verify = verify.replace("/opt/raspi-cloud-sync-pro/experimental/mtp/start-mtp check",
                                self.executable("check"))
        verify += "\n[Unit]\nStartLimitIntervalSec=0\n"
        self.install(VERIFY, verify)
        base = parse_unit(RUNTIME / BROKER)
        base.remove_option("Unit", "ConditionPathExists")
        base["Unit"]["StartLimitIntervalSec"] = "0"
        service = base["Service"]
        service["Group"] = "root"
        service["ExecStart"] = self.executable("broker")
        service["ReadWritePaths"] = str(self.root / "data")
        service["ReadOnlyPaths"] = str(self.root / "data/export")
        service["InaccessiblePaths"] = str(self.root / "data/private")
        # RuntimeDirectory is irrelevant to this inert fixture and must not
        # create/remove the production broker's shared socket directory.
        service.pop("RuntimeDirectory", None)
        service.pop("RuntimeDirectoryMode", None)
        from io import StringIO
        buffer = StringIO()
        base.write(buffer)
        # Reproduce the departed precheck's actual security context with the
        # exact namespace guard, without executing any Pi/platform setup.
        legacy = buffer.getvalue() + "\n[Unit]\nRequires=" + PREPARE + "\nAfter=" + PREPARE
        legacy += "\n[Service]\nExecStartPre=+" + self.executable("legacy-check") + "\n"
        self.install(LEGACY, legacy)
        broker = buffer.getvalue() + "\n" + (STARTUP / (BROKER + ".d/30-mtp-startup.conf")).read_text()
        self.install(BROKER, broker)
        producer = "[Unit]\nStartLimitIntervalSec=0\nRequires=" + BROKER + "\nAfter=" + BROKER + "\n"
        producer += "[Service]\nType=simple\nRestart=no\nExecStart=" + self.executable("producer") + "\n"
        producer += "\n" + (STARTUP / (PRODUCER + ".d/30-mtp-startup.conf")).read_text()
        self.install(PRODUCER, producer)
        binder = (STARTUP / BINDER).read_text()
        binder = binder.replace("ConditionPathExists=/etc/raspi-cloud-sync-pro/MTP_STARTUP_APPROVED",
                                "ConditionPathExists=" + str(self.root / "approved"))
        binder = binder.replace("/opt/raspi-cloud-sync-pro/experimental/mtp/start-mtp bind",
                                self.executable("bind"))
        binder += "\n[Unit]\nStartLimitIntervalSec=0\n"
        self.install(BINDER, binder)

    def properties(self, name):
        result = self.command("show", "--all", self.units[name],
                              "--property=ActiveState,SubState,MainPID,Result,InvocationID")
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def events(self, action):
        path = self.root / "data/work/events.jsonl"
        if not path.exists():
            return []
        return [record for record in map(json.loads, path.read_text().splitlines())
                if record["action"] == action]

    def wait_events(self, action, count):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            rows = self.events(action)
            if len(rows) >= count:
                return rows
            time.sleep(0.05)
        self.fail("Fixture did not produce the expected " + action + " observation")

    def assert_private_broker(self, expected_runs):
        rows = self.wait_events("broker", expected_runs)
        self.assertEqual(len(rows), expected_runs)
        value = rows[-1]
        self.assertNotEqual(value["mount_namespace"], value["host_mount_namespace"])
        self.assertFalse(value["private_credential_readable"])
        self.assertEqual(value["export_write_errno"], 30)  # EROFS
        self.assertEqual(value["work_mount_id"], value["staging_mount_id"])
        self.assertEqual(value["no_new_privileges"], "1")
        self.assertEqual(int(value["effective_capabilities"], 16), 0xB,
                         "Broker capability boundary changed from CHOWN/DAC_OVERRIDE/FOWNER")
        self.assertEqual(self.properties(BROKER)["ActiveState"], "active")
        preceding = [check for check in self.events("check")
                     if check["passed"] and check["monotonic_ns"] < value["monotonic_ns"]]
        self.assertTrue(preceding, "Broker started without a successful real host check")
        if len(rows) > 1:
            self.assertGreater(preceding[-1]["monotonic_ns"], rows[-2]["monotonic_ns"],
                               "A cached host check incorrectly authorized a new broker activation")
        return value["pid"]

    def assert_fresh_host_checks(self, count):
        rows = self.events("check")
        self.assertGreaterEqual(len(rows), count)
        self.assertLessEqual(len(rows), 12, "Unexpected repeated fixture activations")
        for row in rows:
            self.assertEqual(row["mount_namespace"], row["host_mount_namespace"])
        # A successful oneshot must not remain cached as active/exited.
        self.assertEqual(self.properties(VERIFY)["ActiveState"], "inactive")
        self.assertEqual(len(self.events("prepare")), 1)

    def test_actual_manager_fresh_checks_sandbox_refusal_and_recovery(self):
        legacy = self.command("start", self.units[LEGACY], check=False)
        self.assertNotEqual(legacy.returncode, 0)
        refusal = self.wait_events("legacy-check", 1)[0]
        self.assertFalse(refusal["passed"])
        self.assertEqual(refusal["code"], "mtp_startup_host_namespace_required")
        self.assertNotEqual(refusal["mount_namespace"], refusal["host_mount_namespace"])
        self.assertEqual(self.events("broker"), [])

        self.command("start", self.units[BINDER])
        first_pid = self.assert_private_broker(1)
        self.assert_fresh_host_checks(1)
        self.assertEqual(len(self.events("bind")), 1)

        # Cached prepare remains active, but an ordinary new activation checks
        # the current approval again before any broker/producer/binder launch.
        self.command("stop", self.units[BROKER])
        before = len(self.events("check"))
        self.command("start", self.units[BINDER])
        second_pid = self.assert_private_broker(2)
        self.assertNotEqual(first_pid, second_pid)
        self.assert_fresh_host_checks(before + 1)

        before = len(self.events("check"))
        self.command("restart", self.units[BROKER])
        third_pid = self.assert_private_broker(3)
        self.assertNotEqual(second_pid, third_pid)
        self.assert_fresh_host_checks(before + 1)
        # Restart deliberately does not revive downstream BindsTo consumers.
        self.command("start", self.units[BINDER])
        self.assertEqual(len(self.events("bind")), 3)

        self.command("stop", self.units[BROKER])
        (self.root / "refuse").write_text("synthetic approval refusal\n")
        before = len(self.events("check"))
        result = self.command("start", self.units[BINDER], check=False)
        self.assertNotEqual(result.returncode, 0)
        checks = self.wait_events("check", before + 1)
        self.assertEqual(checks[-1]["code"], "fixture_approval_refused")
        self.assertFalse(checks[-1]["passed"])
        self.assertEqual(self.properties(PREPARE)["ActiveState"], "active")
        self.assertEqual(len(self.events("prepare")), 1)
        self.assertEqual(len(self.events("broker")), 3)
        self.assertEqual(len(self.events("bind")), 3)
        for name in (BROKER, PRODUCER, BINDER):
            self.assertNotEqual(self.properties(name)["ActiveState"], "active")

        (self.root / "refuse").unlink()  # Only this disposable fixture marker.
        before = len(self.events("check"))
        self.command("start", self.units[BINDER])
        self.assert_private_broker(4)
        self.assert_fresh_host_checks(before + 1)
        self.assertEqual(len(self.events("bind")), 4)
        self.assertEqual((self.root / "data/private/credential").read_bytes(), self.secret)
        self.assertFalse((self.root / "data/export/forbidden").exists())
        print(json.dumps({"real_systemd_mtp_startup": "PASS", "host_checks": len(self.events("check")),
                          "systemd_manager_version": self.manager_version,
                          "broker_activations": 4, "cached_prepare_runs": 1,
                          "refused_activation_blocked_downstream": True,
                          "sandbox_and_credential_masks_preserved": True,
                          "legacy_precheck_namespace_refusal_reproduced": True,
                          "usb_or_cloud_operations": False}, sort_keys=True))
        self.test_passed = True

    def cleanup_fixture(self):
        # No wildcard systemctl operation, production unit, hook or enablement.
        # Stop ONLY exact unit names created by this test, then remove only the
        # matching files after verifying the disposable root's identity.
        if not self.test_passed:
            for path in self.created:
                state = self.command("show", path.name,
                                     "--property=ActiveState,SubState,MainPID,Result", check=False)
                logs = subprocess.run(["journalctl", "--no-pager", "-u", path.name, "-n", "15"],
                                      capture_output=True, text=True, timeout=15)
                print(json.dumps({"fixture_failure_unit": path.name, "state": state.stdout,
                                  "journal": logs.stdout, "events": self.events("check")}, sort_keys=True))
        existing = {path.name for path in self.created}
        stops = []
        for name in (BINDER, PRODUCER, BROKER, LEGACY, VERIFY, PREPARE):
            if self.units[name] in existing:
                stops.append(self.command("stop", self.units[name], check=False))
        self.assertTrue(all(result.returncode == 0 for result in stops),
                        "Fixture stop was not confirmed; preserve its files")
        for path in self.created:
            result = self.command("show", path.name, "--property=MainPID")
            self.assertEqual(result.stdout.strip(), "MainPID=0",
                             "Fixture process may still run; preserve its files")
        for path in self.created:
            self.assertEqual(path.parent, Path("/run/systemd/system"))
            self.assertTrue(path.name.startswith(self.tag + "-"))
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.lstat().st_uid, 0)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.unit_digests[path],
                             "Fixture unit changed; preserve evidence instead of unlinking it")
            path.unlink()
        self.command("daemon-reload")
        self.command("reset-failed", *self.units.values(), check=False)
        self.assertEqual(self.root.parent, Path("/run"))
        self.assertEqual(self.root.name, self.tag)
        self.assertFalse(self.root.is_symlink())
        self.assertEqual(self.root.stat().st_uid, 0)
        info = self.root.lstat()
        self.assertEqual((info.st_dev, info.st_ino), self.root_identity)
        self.assertEqual(info.st_mode & 0o7777, 0o700)
        shutil.rmtree(self.root)


if __name__ == "__main__":
    unittest.main()
