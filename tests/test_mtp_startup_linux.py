"""Linux startup observations are mocked; no device or cloud access occurs."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import stat
import unittest
from unittest.mock import patch

from raspi_cloud_sync_pro import mtp_startup as startup


class LinuxReadinessTests(unittest.TestCase):
    def setUp(self):
        self.settings = startup.StartupSettings(1234, 1234, 100)
        self.backend = startup.LinuxStartupIO.__new__(startup.LinuxStartupIO)
        self.backend.approved = {"parent_entries": {
            "mtp-experiment": [], "mass-storage-staging": [],
            "mass-storage-backup": []}}
        self.units = {
            startup.BROKER: {"ActiveState": "active", "SubState": "running", "MainPID": "234"},
            startup.RESPONDER: {"ActiveState": "active", "SubState": "running", "MainPID": "235"}}
        self.backend._unit = lambda unit: dict(self.units[unit])
        self.text = {}
        self.binary = {}
        self.metadata = {}
        for pid, uid, executable in ((234, 0, startup.PREFIX / "serve-completions"),
                                     (235, 1234, startup.PREFIX / "umtprd")):
            self.text[f"/proc/{pid}/status"] = (
                "Uid:\t{0}\t{0}\t{0}\t{0}\nGid:\t{0}\t{0}\t{0}\t{0}\nNoNewPrivs:\t1\nCapEff:\t0000000000000000\n".format(uid))
            self.text[f"/proc/{pid}/stat"] = str(pid) + " (fixture) " + " ".join(["0"] * 19 + ["12345"])
            self.binary[f"/proc/{pid}/cmdline"] = str(executable).encode() + b"\0"
        self.text[str(startup.GADGETS / startup.PROFILE / "functions/ffs.mtp/ready")] = "1\n"
        self.metadata[str(startup.SOCKET)] = self.info(stat.S_IFSOCK | 0o660, 0, 1234)
        self.metadata[str(startup.SOCKET.parent)] = self.info(stat.S_IFDIR | 0o750, 0, 1234)
        for index, path in enumerate((startup.WORK, startup.STAGING, startup.EXPORT), 100):
            info = self.info(stat.S_IFDIR | 0o700, 0, 0, inode=index)
            self.metadata[str(path)] = info
            self.metadata[str(Path("/proc/234/root") / path.relative_to("/"))] = info
        info = self.info(stat.S_IFDIR | 0o500, 1234, 1234, inode=200)
        self.metadata[str(startup.FFS)] = info
        self.metadata[str(Path("/proc/235/root") / startup.FFS.relative_to("/"))] = info
        for name in ("ep0", "ep1", "ep2", "ep3"):
            self.metadata[str(startup.FFS / name)] = self.info(stat.S_IFREG | 0o600, 1234, 1234)
        self.mounts = [
            {"id": "10", "target": startup.ROOT, "options": ["rw"], "fs": "ext4", "source": "fixture"},
            {"id": "11", "target": startup.EXPORT, "options": ["ro"], "fs": "ext4", "source": "fixture"},
            {"id": "12", "target": startup.ROOT / "mass-storage-backup",
             "options": ["ro", "nosuid", "nodev", "noexec"], "fs": "tmpfs", "source": "fixture"}]
        self.socket_exists = True

    @staticmethod
    def info(mode, uid, gid, inode=1):
        return SimpleNamespace(st_dev=5, st_ino=inode, st_mode=mode, st_uid=uid, st_gid=gid)

    def ready(self):
        # Unexpected reads fail instead of falling through to real /proc,/sys.
        def text(path, *args, **kwargs):
            value = self.text[str(path)]
            return value() if callable(value) else value
        def binary(path, *args, **kwargs):
            return self.binary[str(path)]
        def metadata(path, *args, **kwargs):
            return self.metadata[str(path)]
        def exists(path):
            self.assertEqual(path, startup.SOCKET)
            return self.socket_exists
        def mounts(pid="self"):
            self.assertEqual(pid, 234)  # The running broker, not our host view.
            return self.mounts
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, "read_text", text))
            stack.enter_context(patch.object(Path, "read_bytes", binary))
            stack.enter_context(patch.object(Path, "lstat", metadata))
            stack.enter_context(patch.object(Path, "exists", exists))
            stack.enter_context(patch.object(startup, "_mounts", mounts))
            return self.backend.ready(self.settings)

    def test_ready_requires_actual_broker_shared_publication_and_masks(self):
        self.assertTrue(self.ready())

    def test_broker_private_staging_bind_cannot_authorize_publication(self):
        self.mounts.append({"id": "99", "target": startup.STAGING, "options": ["rw"],
                            "fs": "ext4", "source": "fixture"})
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_missing_private_worker_mask_refuses(self):
        self.mounts.pop()
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_writable_export_in_broker_namespace_refuses(self):
        self.mounts[1]["options"] = ["rw"]
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_missing_broker_socket_waits_without_becoming_ready(self):
        self.socket_exists = False
        self.assertFalse(self.ready())

    def test_wrong_socket_owner_refuses(self):
        self.metadata[str(startup.SOCKET)] = self.info(stat.S_IFSOCK | 0o660, 1234, 1234)
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_endpoint_world_access_refuses(self):
        self.metadata[str(startup.FFS / "ep1")] = self.info(stat.S_IFREG | 0o666, 1234, 1234)
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_descriptor_not_ready_waits_without_becoming_ready(self):
        self.text[str(startup.GADGETS / startup.PROFILE / "functions/ffs.mtp/ready")] = "0\n"
        self.assertFalse(self.ready())

    def test_privileged_responder_refuses(self):
        self.text["/proc/235/status"] = self.text["/proc/235/status"].replace(
            "CapEff:\t0000000000000000", "CapEff:\t0000000000000001")
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_wrong_running_executable_refuses(self):
        self.binary["/proc/235/cmdline"] = b"/unrelated/program\0"
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_broker_staging_directory_substitution_refuses(self):
        path = Path("/proc/234/root") / startup.STAGING.relative_to("/")
        self.metadata[str(path)] = self.info(stat.S_IFDIR | 0o700, 0, 0, inode=999)
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_responder_sees_same_functionfs_inode(self):
        path = Path("/proc/235/root") / startup.FFS.relative_to("/")
        self.metadata[str(path)] = self.info(stat.S_IFDIR | 0o500, 1234, 1234, inode=999)
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_wrong_responder_gid_refuses(self):
        self.text["/proc/235/status"] = self.text["/proc/235/status"].replace(
            "Gid:\t1234\t1234\t1234\t1234", "Gid:\t1235\t1235\t1235\t1235")
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_stopped_component_refuses(self):
        self.units[startup.RESPONDER]["ActiveState"] = "inactive"
        self.units[startup.RESPONDER]["SubState"] = "dead"
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_missing_no_new_privileges_refuses(self):
        self.text["/proc/235/status"] = self.text["/proc/235/status"].replace(
            "NoNewPrivs:\t1", "NoNewPrivs:\t0")
        with self.assertRaises(startup.StartupError):
            self.ready()

    def test_changed_process_start_time_refuses(self):
        reads = []
        original = self.text["/proc/234/stat"]
        def read_stat():
            reads.append(None)
            return original if len(reads) == 1 else original.replace("12345", "12346")
        self.text["/proc/234/stat"] = read_stat
        with self.assertRaises(startup.StartupError):
            self.ready()
        self.assertEqual(len(reads), 2)

    def test_wrong_responder_uid_refuses(self):
        self.text["/proc/235/status"] = self.text["/proc/235/status"].replace(
            "Uid:\t1234\t1234\t1234\t1234", "Uid:\t1235\t1235\t1235\t1235")
        with self.assertRaises(startup.StartupError):
            self.ready()


if __name__ == "__main__":
    unittest.main()
