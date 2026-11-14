import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro.errors import BackupError
from raspi_cloud_sync_pro.gadget import GadgetController, GadgetObservation
from raspi_cloud_sync_pro.storage import Ownership


class SimulatedGadget:
    def __init__(self, root):
        self.image = root / "disposable.img"
        self.image.write_bytes(b"image fixture, not a filesystem")
        self.image_identity = (1, 2, self.image.stat().st_size)
        self.mountpoint = root / "simulated-volume"
        self.mountpoint.mkdir()
        (self.mountpoint / "original.txt").write_bytes(b"retain original")
        self.bound, self.backing, self.configured = False, "", False
        self.loop, self.mounted = None, False
        self.power_ok, self.profile_ok, self.read_only, self.clean = True, True, True, True
        self.events = []
        self.failure = None

    def event(self, name):
        self.events.append(name)
        if name == self.failure:
            raise BackupError("simulated_" + name)

    def observe(self):
        return GadgetObservation(self.bound, self.backing, self.configured,
                                 (self.loop,) if self.loop else (),
                                 (str(self.mountpoint),) if self.mounted else (),
                                 self.power_ok, self.profile_ok)

    def attach_gadget(self):
        self.event("attach")
        if self.loop or self.mounted:
            raise AssertionError("host/Pi overlap")
        self.bound, self.backing, self.configured = True, str(self.image), True

    def detach_gadget(self):
        self.event("detach")
        if self.backing:
            raise AssertionError("detached before eject")
        self.bound, self.configured = False, False

    def eject(self):
        self.backing = ""

    def attach_loop_read_only(self):
        self.event("loop_attach")
        if self.bound or self.backing:
            raise AssertionError("live image read")
        self.loop = "/dev/loop42"
        return self.loop

    def verify_loop_read_only(self, loop):
        self.event("verify_loop")
        if not self.read_only or self.loop != loop:
            raise BackupError("loop_not_read_only")

    def check_filesystem(self, loop):
        self.event("fsck")
        if not self.clean:
            raise BackupError("dirty_filesystem")

    def mount_read_only(self, loop):
        self.event("mount")
        if self.bound or self.backing:
            raise AssertionError("simultaneous ownership")
        self.mounted = True

    def verify_mount_read_only(self, loop):
        self.event("verify_mount")
        if not self.mounted or not self.read_only:
            raise BackupError("mount_not_read_only")

    def unmount(self):
        self.event("unmount")
        self.mounted = False

    def detach_loop(self, loop):
        self.event("loop_detach")
        if self.mounted:
            raise AssertionError("detached mounted loop")
        self.loop = None


class GadgetControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = SimulatedGadget(self.root)
        self.controller = GadgetController(self.backend, self.root / "state")

    def arm(self, controller):
        controller.start()
        self.assertEqual(controller.poll(self.root / "staging"), "waiting_for_host_eject")

    def test_complete_eject_stage_and_reattach(self):
        with self.controller.session():
            self.arm(self.controller)
            initial_events = len(self.backend.events)
            self.backend.eject()
            batch = self.controller.poll(self.root / "staging")
            self.assertEqual((batch.root / "files/original.txt").read_bytes(), b"retain original")
            self.assertEqual(self.controller.ownership.state, Ownership.HOST)
            self.assertTrue(self.backend.bound)
            self.assertFalse(self.backend.mounted)
            events = self.backend.events[initial_events:]
            self.assertLess(events.index("detach"), events.index("mount"))
            self.assertLess(events.index("unmount"), events.index("attach"))
            self.assertEqual(self.controller.journal.data["phase"], "host")
        self.assertEqual((self.backend.mountpoint / "original.txt").read_bytes(), b"retain original")

    def test_quiet_host_never_detached_or_mounted(self):
        with self.controller.session():
            self.arm(self.controller)
            self.backend.events.clear()
            for _ in range(20):
                self.assertEqual(self.controller.poll(self.root / "staging"), "waiting_for_host_eject")
            self.assertEqual(self.backend.events, [])

    def test_explicit_new_journal_host_adoption_waits_for_fresh_poll(self):
        self.backend.attach_gadget()
        self.backend.events.clear()
        with self.controller.session():
            self.controller.start(adopt_host=True)
            self.assertTrue(self.controller.started)
            self.assertFalse(self.controller.armed)
            self.assertEqual(self.controller.ownership.state, Ownership.HOST)
            self.assertEqual(self.backend.events, [])
            self.assertFalse((self.root / "staging").exists())
            saved = json.loads((self.root / "state/controller.json").read_text())
            self.assertEqual(saved["phase"], "host")
            self.assertEqual(saved["image"], str(self.backend.image))
            self.assertEqual(saved["image_identity"], list(self.backend.image_identity))
            self.assertIsNone(saved["loop"])
            self.assertEqual(self.controller.poll(self.root / "staging"), "waiting_for_host_eject")
            self.assertTrue(self.controller.armed)
            self.assertEqual(self.backend.events, [])
            self.backend.eject()
            batch = self.controller.poll(self.root / "staging")
            self.assertEqual((batch.root / "files/original.txt").read_bytes(), b"retain original")
            self.assertTrue(self.backend.bound)
        self.assertEqual((self.backend.mountpoint / "original.txt").read_bytes(), b"retain original")

    def test_adoption_does_not_authorize_empty_lun_before_loaded_poll(self):
        self.backend.attach_gadget()
        self.backend.events.clear()
        with self.controller.session():
            self.controller.start(adopt_host=True)
            self.backend.eject()
            with self.assertRaisesRegex(BackupError, "host_release_unproven"):
                self.controller.poll(self.root / "staging")
            self.assertEqual(self.backend.events, [])
            self.assertFalse((self.root / "staging").exists())
            self.assertFalse(self.controller.armed)
            self.assertEqual(self.controller.journal.data["phase"], "recovery_required")

    def test_default_start_does_not_adopt_loaded_host(self):
        self.backend.attach_gadget()
        self.backend.events.clear()
        with self.controller.session(), self.assertRaisesRegex(BackupError, "image_not_exclusive"):
            self.controller.start()
        self.assertEqual(self.backend.events, [])

    def test_adoption_flag_refuses_coercion_before_backend_observation(self):
        for value in (1, 0, "false", "true", [], {}, None):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backend = SimulatedGadget(root)
                controller = GadgetController(backend, root / "state")
                with controller.session(), mock.patch.object(backend, "observe") as observe:
                    with self.assertRaisesRegex(BackupError, "invalid_host_adoption_request"):
                        controller.start(adopt_host=value)
                    observe.assert_not_called()
                self.assertEqual(backend.events, [])

    def test_adoption_requires_verified_configured_exclusive_loaded_host(self):
        faults = {"unbound": ("bound", False), "unconfigured": ("configured", False),
                  "empty": ("backing", ""), "wrong_image": ("backing", "/unexpected/image"),
                  "loop": ("loop", "/dev/loop42"), "mounted": ("mounted", True),
                  "power": ("power_ok", False), "profile": ("profile_ok", False)}
        for fault, (attribute, value) in faults.items():
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backend = SimulatedGadget(root)
                backend.attach_gadget()
                backend.events.clear()
                setattr(backend, attribute, value)
                controller = GadgetController(backend, root / "state")
                with controller.session(), self.assertRaises(BackupError):
                    controller.start(adopt_host=True)
                self.assertEqual(backend.events, [])
                self.assertFalse(controller.armed)
                self.assertFalse((root / "staging").exists())

    def test_adoption_cannot_override_ambiguous_existing_journal(self):
        for phase in ("recovery_required", "attach_intent", "eject_observed"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backend = SimulatedGadget(root)
                backend.attach_gadget()
                backend.events.clear()
                controller = GadgetController(backend, root / "state")
                with controller.session():
                    controller._save(phase)
                restarted = GadgetController(backend, root / "state")
                with restarted.session(), self.assertRaisesRegex(BackupError, "controller_recovery_required"):
                    restarted.start(adopt_host=True)
                self.assertEqual(backend.events, [])
                self.assertFalse(restarted.armed)

    def test_startup_with_empty_lun_cannot_claim_ejection(self):
        with self.controller.session():
            self.arm(self.controller)
        self.backend.eject()
        restarted = GadgetController(self.backend, self.root / "state")
        self.backend.events.clear()
        with restarted.session(), self.assertRaises(BackupError):
            restarted.start()
        self.assertEqual(self.backend.events, [])

    def test_link_loss_disarms_release_evidence(self):
        with self.controller.session():
            self.arm(self.controller)
            self.backend.configured = False
            self.assertEqual(self.controller.poll(self.root / "staging"), "waiting_for_host")
            self.backend.eject()
            self.backend.configured = True
            with self.assertRaisesRegex(BackupError, "release_unproven"):
                self.controller.poll(self.root / "staging")
            self.assertTrue(self.backend.bound)

    def test_failures_never_reattach_or_delete_originals(self):
        for failure in ("detach", "loop_attach", "fsck", "mount", "verify_mount", "unmount", "loop_detach", "attach"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backend = SimulatedGadget(root)
                controller = GadgetController(backend, root / "state")
                with controller.session():
                    controller.start()
                    controller.poll(root / "staging")
                    backend.eject()
                    backend.events.clear()
                    backend.failure = failure
                    with self.assertRaises(BackupError):
                        controller.poll(root / "staging")
                    self.assertEqual(controller.ownership.state, Ownership.ERROR)
                    self.assertEqual(controller.journal.data["phase"], "recovery_required")
                    if failure != "attach":
                        self.assertNotIn("attach", backend.events)
                self.assertEqual((backend.mountpoint / "original.txt").read_bytes(), b"retain original")

    def test_dirty_filesystem_and_bad_power_block_before_mount(self):
        for fault in ("dirty", "power", "profile", "writable_loop"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backend = SimulatedGadget(root)
                controller = GadgetController(backend, root / "state")
                with controller.session():
                    controller.start()
                    controller.poll(root / "staging")
                    backend.eject()
                    backend.events.clear()
                    if fault == "dirty": backend.clean = False
                    if fault == "power": backend.power_ok = False
                    if fault == "profile": backend.profile_ok = False
                    if fault == "writable_loop": backend.read_only = False
                    with self.assertRaises(BackupError): controller.poll(root / "staging")
                    self.assertNotIn("mount", backend.events)

    def test_power_loss_recovery_cleans_only_known_resources_and_never_reexports(self):
        with self.controller.session():
            self.arm(self.controller)
            self.backend.eject()
            with mock.patch("raspi_cloud_sync_pro.gadget.stage_snapshot", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt): self.controller.poll(self.root / "staging")
        self.assertTrue(self.backend.mounted)
        restarted = GadgetController(self.backend, self.root / "state")
        self.backend.events.clear()
        with restarted.session():
            with self.assertRaisesRegex(BackupError, "recovery_required"): restarted.start()
            self.assertEqual(self.backend.events, [])
            self.assertEqual(restarted.recover_resources(), "manual_review_required")
            self.assertNotIn("attach", self.backend.events)
            self.assertFalse(self.backend.mounted)
            self.assertIsNone(self.backend.loop)

    def test_journal_is_not_authorization_and_other_controller_is_excluded(self):
        with self.controller.session():
            other = GadgetController(self.backend, self.root / "state")
            with self.assertRaisesRegex(BackupError, "controller_busy"), other.session(): pass
        state = self.root / "state/controller.json"
        state.write_text("corrupt")
        with self.assertRaisesRegex(BackupError, "invalid_controller_journal"), self.controller.session(): pass

    def test_no_operations_outside_a_session(self):
        with self.assertRaisesRegex(BackupError, "session_required"): self.controller.start()
        self.assertEqual(self.backend.events, [])
