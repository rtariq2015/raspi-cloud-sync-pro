import io
import json
import os
import stat
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import smb_capture as capture
from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.smb_ingest import LocalSambaReader
from raspi_cloud_sync_pro.staging import ReadyBatch


def configuration():
    return {"schema_version": 1, "enabled": True, "approval_file": "/etc/rcsp/SMB_CAPTURE_APPROVED",
            "device_policy": "/etc/rcsp/device-policy.json", "share_name": "CloudSync",
            "share_root": "/srv/rcsp/share", "staging_directory": "/var/lib/rcsp/staging",
            "state_directory": "/var/lib/rcsp/capture", "credentials_file": "/var/lib/rcsp/capture/smb.json",
            "usb_address": "172.19.80.1/28", "usb_profile_uuid": "01234567-89ab-cdef-0123-456789abcdef",
            "interval": 5, "max_file_bytes": 100 * 1024 * 1024, "capture_timeout": 300}


class AuthorityTests(unittest.TestCase):
    def load(self, value):
        with mock.patch.object(capture.private_files, "_root_json", return_value=(value, (1, 2))):
            return capture.SmbCaptureSettings.load("/etc/rcsp/capture.json")

    def test_disabled_and_unavailable_do_no_io_or_network(self):
        with mock.patch.object(capture, "_guard") as guard, mock.patch.object(capture, "LocalSambaReader") as reader:
            self.assertEqual(capture.run_smb_capture(capture.SmbCaptureSettings(), once=True)["capture_state"], "disabled")
            self.assertEqual(capture.run_smb_capture(None, once=True)["capture_state"], "unavailable")
        guard.assert_not_called(); reader.assert_not_called()

    def test_specific_config_is_strict_and_has_no_live_address_constant(self):
        settings = self.load(configuration())
        self.assertEqual(settings.usb_address, "172.19.80.1/28")
        self.assertNotIn("172.19", repr(settings))
        changes = [{"enabled": 1}, {"interval": True}, {"capture_timeout": 0}, {"max_file_bytes": 16 * 1024 ** 3 + 1},
                   {"usb_address": "192.0.2.1/28"}, {"usb_address": "240.0.0.1/28"}, {"usb_address": "172.19.80.0/28"},
                   {"usb_address": "172.19.80.1/16"}, {"share_root": "/srv/../share"}, {"share_name": "bad/share"},
                   {"staging_directory": "/srv/rcsp/share/staged"}, {"credentials_file": "/elsewhere/secret"},
                   {"unknown": "private-secret"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(capture.SmbCaptureError, "^smb_capture_config_invalid$"):
                self.load(dict(configuration(), **change))

    def test_default_reader_keeps_global_gate_before_smb_import(self):
        client = types.SimpleNamespace(open_file=mock.Mock())
        with mock.patch.dict("sys.modules", {"smbclient": client}), self.assertRaisesRegex(BackupError, "hardware_operations_disabled"):
            with LocalSambaReader().open_version("one"):
                self.fail("refused")
        client.open_file.assert_not_called()

    def test_explicit_guard_is_before_smb_open_and_revoked_guard_refuses(self):
        client = types.SimpleNamespace(open_file=mock.Mock(return_value=io.BytesIO(b"version")))
        guard = mock.Mock()
        with mock.patch.dict("sys.modules", {"smbclient": client}):
            reader = LocalSambaReader(username="raspi-backup", password="synthetic", approval_guard=guard)
            with reader.open_version("one") as stream:
                self.assertEqual(stream.read(), b"version")
            guard.side_effect = capture.SmbCaptureError("smb_capture_approval_required", True)
            with self.assertRaisesRegex(BackupError, "smb_capture_approval_required"), reader.open_version("two"):
                self.fail("refused")
        self.assertEqual(client.open_file.call_count, 1)
        self.assertEqual(client.open_file.call_args.kwargs["share_access"], "r")
        self.assertTrue(client.open_file.call_args.kwargs["encrypt"])

    @unittest.skipIf(os.geteuid() == 0, "ordinary-UID readability test")
    def test_root_public_authority_reader_actually_reads_as_ordinary_uid(self):
        # A real root0644 JSON under root0755 ancestors; no mocked opens or UID.
        path = Path("/usr/share/nodejs/lru-cache/package.json")
        if not path.exists():
            self.skipTest("public root-owned JSON fixture unavailable")
        value, identity = capture.private_files._root_json(path)
        self.assertIsInstance(value, dict)
        self.assertEqual(len(identity), 5)
        # The same path reader accepts 0644, rather than demanding root0600.
        self.assertEqual(path.stat().st_uid, 0)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        with tempfile.TemporaryDirectory() as temporary:
            user_file = Path(temporary) / "authority.json"
            user_file.write_text('{"schema_version":1,"enabled":false}')
            with self.assertRaises(BackupError):
                capture.SmbCaptureSettings.load(user_file)

    def test_specific_approval_refuses_before_kernel_probes_and_state(self):
        settings = self.load(configuration())
        with mock.patch.object(capture.SmbCaptureSettings, "load", return_value=settings), \
                mock.patch.object(capture.private_files, "_root_json", return_value=({"schema_version": 1, "smb_capture_approved": False}, (1,))), \
                mock.patch.object(capture, "_ordinary") as ordinary, mock.patch.object(capture, "LocalSambaReader") as reader:
            report = capture.run_smb_capture(settings, once=True)
        self.assertEqual(report["capture_state"], "blocked")
        ordinary.assert_not_called(); reader.assert_not_called()

    def test_readonly_command_has_size_deadline_and_prelaunch_bounds(self):
        python = str(Path("/usr/bin/python3").resolve())
        self.assertEqual(capture._command([python, "-I", "-B", "-c", "print('ok')"], time.monotonic() + 3), (0, "ok"))
        for script in ("print('x'*9000)", "import time; time.sleep(2)"):
            with self.subTest(script=script), self.assertRaises(BackupError):
                capture._command([python, "-I", "-B", "-c", script], time.monotonic() + 0.15)
        with mock.patch.object(capture.subprocess, "Popen") as launch, self.assertRaises(BackupError):
            capture._command([python], time.monotonic() - 1)
        launch.assert_not_called()

    def test_probe_reap_is_bounded_even_after_successful_command(self):
        original = capture.subprocess.Popen
        waits = []
        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            wait = process.wait
            def bounded_wait(*args, **kwargs):
                waits.append(kwargs.get("timeout"))
                return wait(*args, **kwargs)
            process.wait = bounded_wait
            return process
        with mock.patch.object(capture.subprocess, "Popen", side_effect=launch):
            capture._command([str(Path("/usr/bin/python3").resolve()), "-I", "-B", "-c", "pass"], time.monotonic() + 3)
        self.assertEqual(waits[-1], 1)
        self.assertTrue(all(isinstance(value, (float, int)) and value > 0 for value in waits))

    def test_capability_bounding_set_must_also_be_empty(self):
        account = types.SimpleNamespace(pw_uid=1234)
        status = "Umask:\t0077\nNoNewPrivs:\t1\n" + "".join(name + ":\t0\n" for name in ("CapEff", "CapPrm", "CapInh", "CapAmb"))
        def opened(path, *args, **kwargs):
            return io.BytesIO(b"Raspberry Pi fixture\0") if path == "/proc/device-tree/model" else io.StringIO(status + "CapBnd:\t1\n")
        with mock.patch.object(capture.pwd, "getpwnam", return_value=account), \
                mock.patch.object(capture.os, "getresuid", return_value=(1234,) * 3), \
                mock.patch.object(capture.os, "getresgid", return_value=(1235,) * 3), \
                mock.patch.object(capture.os, "getegid", return_value=1235), \
                mock.patch.object(capture.platform, "system", return_value="Linux"), \
                mock.patch.object(capture.platform, "machine", return_value="aarch64"), \
                mock.patch("builtins.open", side_effect=opened), self.assertRaisesRegex(BackupError, "smb_capture_private_process_required"):
            capture._ordinary()

    def test_kernel_guard_rejects_usb_ics_overlap_or_filesystem_drift(self):
        settings = self.load(configuration())
        policy = {"schema_version": 1, "mode": "ethernet_samba", "approved_uplinks": ["wifi"], "allow_metered": False}
        def root_json(path):
            return (capture._APPROVAL if path == settings.approval_file else policy), (1,)
        def command(args, deadline):
            if args[1] == "is-active": return 3, "inactive"
            if args[1] == "is-enabled": return 1, "disabled"
            if args[0].endswith("nmcli"):
                field = args[4]
                return 0, {"GENERAL.CON-UUID": settings.usb_profile_uuid, "IP4.ADDRESS": settings.usb_address,
                           "IP4.GATEWAY": "", "IP4.DNS": "", "ipv4.method,ipv4.never-default,connection.interface-name": "manual\nyes\nusb0"}[field]
            if args[0].endswith("ip"): return 0, '[{"addr_info":[{"family":"inet","local":"192.168.4.22","prefixlen":24}]}]'
            return 0, '{"filesystems":[{"fstype":"ext4","source":"/dev/mmcblk0p2","options":"rw,noatime"}]}'
        @contextmanager
        def root_directory(path): yield 0
        metadata = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=os.geteuid() + 10, st_gid=os.getegid())
        with mock.patch.object(capture.SmbCaptureSettings, "load", return_value=settings), \
                mock.patch.object(capture.private_files, "_root_json", side_effect=root_json), \
                mock.patch.object(capture, "_ordinary"), mock.patch.object(capture, "bound_configfs_gadgets", return_value=()), \
                mock.patch.object(capture.Path, "exists", side_effect=lambda: True), \
                mock.patch.object(capture, "LEGACY_GADGET_MODULES", ("g_ether",)), \
                mock.patch.object(capture.private_files, "_root_directory", side_effect=root_directory), \
                mock.patch.object(capture.Path, "lstat", return_value=metadata), \
                mock.patch.object(capture, "_command", side_effect=command):
            capture._guard(settings, time.monotonic() + 10)
            for target in ("ics", "overlap", "filesystem", "share"):
                def drift(args, deadline):
                    if target == "ics" and args[1] == "is-enabled": return 0, "enabled"
                    if target == "overlap" and args[0].endswith("ip"):
                        return 0, '[{"addr_info":[{"family":"inet","local":"172.19.80.3","prefixlen":28}]}]'
                    if target == "filesystem" and args[0].endswith("findmnt"):
                        return 0, '{"filesystems":[{"fstype":"ext4","source":"/dev/loop0","options":"rw"}]}'
                    return command(args, deadline)
                with self.subTest(target=target), mock.patch.object(capture, "_command", side_effect=drift):
                    prior = metadata.st_mode
                    if target == "share": metadata.st_mode = stat.S_IFDIR | 0o777
                    try:
                        with self.assertRaises(BackupError): capture._guard(settings, time.monotonic() + 10)
                    finally: metadata.st_mode = prior


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.share = self.root / "share"; self.share.mkdir()
        self.staging = self.root / "staging"; self.staging.mkdir(mode=0o700)
        self.state = self.root / "state"; self.state.mkdir(mode=0o700)
        self.credentials = self.state / "smb.json"
        self.credentials.write_text(json.dumps({"username": "raspi-backup", "password": "synthetic-private-password"}))
        self.credentials.chmod(0o600)
        self.settings = capture.SmbCaptureSettings(enabled=True, share_name="CloudSync", share_root=self.share,
            staging_directory=self.staging, state_directory=self.state, credentials_file=self.credentials)
        self.old_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.old_umask)
        self.opens = []; self.busy = set(); self.read_hook = None; self.close_error = False
        self.guard = mock.Mock()
        self.guard_patch = mock.patch.object(capture, "_guard", self.guard); self.guard_patch.start()
        self.addCleanup(self.guard_patch.stop)
        owner = self
        class Reader:
            def __init__(self, share, username, password, *, approval_guard):
                self.guard = approval_guard
                owner.assertEqual((share, username, password), ("CloudSync", "raspi-backup", "synthetic-private-password"))

            @contextmanager
            def open_version(self, relative):
                self.guard()
                owner.opens.append(relative)
                if relative in owner.busy:
                    raise BackupError("sharing_violation", True)
                class Stream(io.BytesIO):
                    def read(self, count=-1):
                        if owner.read_hook:
                            owner.read_hook(relative)
                        return super().read(count)
                with Stream((owner.share / relative).read_bytes()) as stream:
                    yield stream
                if owner.close_error:
                    raise BackupError("synthetic_close_error", True)
        self.reader_patch = mock.patch.object(capture, "LocalSambaReader", Reader); self.reader_patch.start()
        self.addCleanup(self.reader_patch.stop)

    def run_once(self, settings=None):
        return capture.run_smb_capture(settings or self.settings, once=True)

    def ready(self):
        return sorted(self.staging.glob("*.ready"))

    def test_real_empty_unicode_large_snapshots_persist_skip_and_changed_version(self):
        originals = {"empty": b"", "nested/雪.txt": "hello雪".encode(), "large": bytes(range(256)) * 8193}
        for relative, data in originals.items():
            path = self.share / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        result = self.run_once()
        self.assertEqual(result["staged_files"], 3)
        self.assertFalse(result["uploads_enabled"])
        self.assertEqual(len(self.ready()), 3)
        for path in self.ready():
            batch = ReadyBatch.load(path)
            item = batch.files[0]
            self.assertEqual((path / "files" / item.relative).read_bytes(), originals[item.relative])
            self.assertEqual(stat.S_IMODE((path / "manifest.json").stat().st_mode), 0o600)
        self.assertEqual(self.run_once()["staged_files"], 0)  # New runtime instance, durable index.
        self.assertEqual(len(self.opens), 3)
        (self.share / "nested/雪.txt").write_bytes(b"new version")
        self.assertEqual(self.run_once()["staged_files"], 1)
        self.assertEqual(len(self.ready()), 4)
        self.assertEqual((self.share / "large").read_bytes(), originals["large"])
        for path in self.state.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_active_writer_deferred_then_captured_without_prior_batch(self):
        (self.share / "busy").write_bytes(b"original"); self.busy.add("busy")
        self.assertEqual(self.run_once()["deferred_files"], 1)
        self.assertEqual(list(self.staging.iterdir()), [])
        self.busy.clear()
        self.assertEqual(self.run_once()["staged_files"], 1)

    def test_busy_or_rejected_prefix_cannot_starve_later_completed_file(self):
        for number in range(33):
            (self.share / f"{number:02d}").write_bytes(b"version")
        self.busy.update(f"{number:02d}" for number in range(32))
        self.assertEqual(self.run_once()["deferred_files"], 32)
        self.assertNotIn("32", self.opens)
        # A new runtime invocation also resumes from the durable cursor.
        result = self.run_once()
        self.assertEqual((result["staged_files"], result["deferred_files"]), (1, 31))
        self.assertIn("32", self.opens)
        self.assertEqual(ReadyBatch.load(self.ready()[0]).files[0].relative, "32")
        self.assertTrue(all((self.share / f"{number:02d}").read_bytes() == b"version" for number in range(33)))
        # The same cursor advance applies to permanent size rejection.
        self.busy.clear()
        with capture._index(self.state) as (index, root):
            index.execute("DELETE FROM scan_cursor"); index.commit()
        for number in range(32):
            (self.share / f"{number:02d}").write_bytes(b"too-large")
        (self.share / "32").write_bytes(b"x")
        small = replace(self.settings, max_file_bytes=1)
        self.assertEqual(self.run_once(small)["rejected_files"], 32)
        self.assertEqual(self.run_once(small)["staged_files"], 1)

    def test_revoked_authority_defers_next_open_and_preserves_first(self):
        (self.share / "a").write_bytes(b"a"); (self.share / "b").write_bytes(b"b")
        self.guard.side_effect = [None, None, capture.SmbCaptureError("smb_capture_approval_required", True)]
        result = self.run_once()
        self.assertEqual((result["staged_files"], result["deferred_files"]), (1, 1))
        self.assertEqual(self.opens, ["a"])
        self.assertEqual(len(self.ready()), 1)

    def test_refused_initial_guard_does_not_read_credentials_or_open(self):
        self.guard.side_effect = RuntimeError("synthetic-private-password")
        with mock.patch.object(capture, "_credentials") as credentials:
            result = self.run_once()
        credentials.assert_not_called()
        self.assertEqual(self.opens, [])
        self.assertEqual(result["capture_state"], "blocked")
        self.assertNotIn("synthetic", json.dumps(result))

    def test_close_and_read_failure_retain_partial_without_ready_or_index_skip(self):
        (self.share / "one").write_bytes(b"original"); self.close_error = True
        self.assertEqual(self.run_once()["deferred_files"], 1)
        self.assertEqual(self.ready(), [])
        first = list(self.staging.glob("*.incomplete")); self.assertEqual(len(first), 1)
        self.close_error = False
        self.read_hook = mock.Mock(side_effect=BackupError("synthetic_disconnect", True))
        self.assertEqual(self.run_once()["deferred_files"], 1)
        self.assertEqual(len(list(self.staging.glob("*.incomplete"))), 2)
        self.read_hook = None
        self.assertEqual(self.run_once()["staged_files"], 1)
        self.assertTrue(first[0].exists())
        self.assertEqual((self.share / "one").read_bytes(), b"original")

    def test_stream_deadline_retains_partial_and_maximum_refuses_before_batch(self):
        (self.share / "one").write_bytes(b"original")
        self.assertEqual(self.run_once(replace(self.settings, max_file_bytes=2))["rejected_files"], 1)
        self.assertEqual(list(self.staging.iterdir()), [])
        now = [100.0]
        self.read_hook = lambda relative: now.__setitem__(0, 500.0)
        with mock.patch.object(capture.time, "monotonic", side_effect=lambda: now[0]):
            self.assertEqual(self.run_once(replace(self.settings, capture_timeout=1))["deferred_files"], 1)
        self.assertEqual(self.ready(), [])
        self.assertEqual(len(list(self.staging.glob("*.incomplete"))), 1)

    def test_corrupt_published_copy_is_retained_and_source_recaptured(self):
        (self.share / "one").write_bytes(b"original")
        self.run_once(); damaged = self.ready()[0]
        (damaged / "files/one").write_bytes(b"damaged")
        self.assertEqual(self.run_once()["staged_files"], 1)
        self.assertEqual(len(self.ready()), 2)
        self.assertEqual((damaged / "files/one").read_bytes(), b"damaged")

    def test_publication_before_index_failure_retains_ready_and_retry(self):
        (self.share / "one").write_bytes(b"original")
        actual = capture._manifest
        with mock.patch.object(capture, "_manifest", side_effect=OSError("simulated interruption")):
            self.assertEqual(self.run_once()["capture_state"], "blocked")
        self.assertEqual(len(self.ready()), 1)
        with mock.patch.object(capture, "_manifest", side_effect=actual):
            self.assertEqual(self.run_once()["staged_files"], 1)
        self.assertEqual(len(self.ready()), 2)  # No destructive rollback or hidden batch.

    def test_concurrent_index_owner_is_refused_without_capture(self):
        (self.share / "one").write_bytes(b"original")
        with capture._index(self.state):
            self.assertEqual(self.run_once()["capture_state"], "blocked")
        self.assertEqual(self.opens, [])

    def test_verified_cache_is_bounded_and_clearing_revalidates_old_batch(self):
        (self.share / "one").write_bytes(b"original")
        self.run_once()
        verified = {"obsolete-one", "obsolete-two"}
        with mock.patch.object(capture, "_MAX_ENTRIES", 2), mock.patch.object(capture.ReadyBatch, "load", wraps=ReadyBatch.load) as load:
            self.assertEqual(capture._tick(self.settings, verified)["staged_files"], 0)
        self.assertEqual(verified, {self.ready()[0].name})
        load.assert_called_once()

    def test_credentials_symlink_hardlink_public_or_duplicate_json_refused(self):
        original = self.credentials.read_text()
        self.credentials.chmod(0o644)
        with self.assertRaises(BackupError): capture._credentials(self.settings)
        self.credentials.chmod(0o600)
        linked = self.state / "linked"; os.link(self.credentials, linked)
        with self.assertRaises(BackupError): capture._credentials(self.settings)
        linked.unlink()
        self.credentials.write_text('{"username":"raspi-backup","password":"a","password":"b"}')
        with self.assertRaises((BackupError, ValueError)): capture._credentials(self.settings)
        self.credentials.unlink(); linked.write_text(original); linked.chmod(0o600); self.credentials.symlink_to(linked)
        with self.assertRaises(BackupError): capture._credentials(self.settings)

    def test_inventory_rejects_links_ads_and_depth_or_count_overflow(self):
        file = self.share / "one"; file.write_bytes(b"x")
        for kind in ("symlink", "hardlink", "ads"):
            extra = self.share / ("bad:stream" if kind == "ads" else "bad")
            if kind == "symlink": extra.symlink_to(file)
            elif kind == "hardlink": os.link(file, extra)
            else: extra.write_bytes(b"x")
            with self.subTest(kind=kind), self.assertRaises(BackupError):
                capture._inventory(self.share, time.monotonic() + 3)
            extra.unlink()
        with mock.patch.object(capture, "_MAX_ENTRIES", 0), self.assertRaises(BackupError):
            capture._inventory(self.share, time.monotonic() + 3)
        deep = self.share.joinpath(*(["d"] * 9)); deep.mkdir(parents=True)
        with self.assertRaises(BackupError): capture._inventory(self.share, time.monotonic() + 3)

    def test_state_and_staging_symlinks_fail_closed_and_no_provider_is_constructed(self):
        (self.share / "one").write_bytes(b"x")
        alias = self.root / "alias"; alias.symlink_to(self.staging, target_is_directory=True)
        with mock.patch("raspi_cloud_sync_pro.providers.RcloneProvider", side_effect=AssertionError("no cloud")):
            self.assertEqual(self.run_once(replace(self.settings, staging_directory=alias))["capture_state"], "blocked")
            self.assertEqual(self.run_once()["staged_files"], 1)


if __name__ == "__main__":
    unittest.main()
