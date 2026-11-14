"""Real private filesystem generations, synthetic grants and no provider I/O."""

import copy
import errno
import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import google_destination as destination
from raspi_cloud_sync_pro import google_operational as operational
from raspi_cloud_sync_pro.history import History
from raspi_cloud_sync_pro.staging import ReadyBatch

REQUEST = "a" * 32
ORIGIN = "https://private-device.example"
FOLDER = "Private App Folder"
SECRET = "PRIVATE-operational-client-secret"
ACCESS = "PRIVATE-operational-access-token"
REFRESH = "PRIVATE-operational-refresh-token"


@unittest.skipIf(os.geteuid() == 0, "Run private generation acceptance as an ordinary UID")
class GoogleOperationalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prepared = self.root / REQUEST
        self.operational = self.root / "operational"
        self.prepared.mkdir(mode=0o700)
        self.operational.mkdir(mode=0o700)
        client = {"client_id": "test-client.apps.googleusercontent.com", "client_secret": SECRET}
        self.bundle = {
            "schema_version": 1, "provider": "google_drive", "source_origin": ORIGIN,
            "client": client,
            "authorization": {
                "schema_version": 1, "provider": "google_drive",
                "client_id_sha256": hashlib.sha256(client["client_id"].encode()).hexdigest(),
                "received_at": 1000000000, "expires_at": 1000003600,
                "token_type": "Bearer", "access_token": ACCESS, "refresh_token": REFRESH,
                "scopes": [destination.GOOGLE_SCOPE],
            },
        }
        raw = destination._canonical(self.bundle)
        read_fd, write_fd = os.pipe()
        os.write(write_fd, len(raw).to_bytes(4, "big") + raw)
        os.close(write_fd)
        destination.prepare_google_destination(self.prepared, read_fd, FOLDER)
        self.proof = self.prepared / destination.PREPARATION_DIRECTORY
        self.manifest_path = self.proof / "preparation.json"
        self.seed_path = self.proof / "rclone.conf"
        self.manifest = json.loads(self.manifest_path.read_text())
        self.auth_digest = self.manifest["authorization_sha256"]
        self.config_digest = self.manifest["rclone_config_sha256"]
        self.original = self.snapshot()

    @staticmethod
    def write_json(path, data):
        path.write_bytes(destination._canonical(data))
        path.chmod(0o600)

    def snapshot(self):
        def item(path):
            data = path.read_bytes() if path.is_file() else None
            info = path.stat()
            return (data, info.st_dev, info.st_ino, info.st_uid, info.st_mode,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        return {path: item(path) for path in
                (self.prepared, self.proof, self.manifest_path, self.seed_path)}

    def create(self, **changes):
        arguments = dict(prepared_state=self.prepared, operational_state=self.operational,
                         request_id=REQUEST, expected_folder_name=FOLDER,
                         expected_authorization_sha256=self.auth_digest,
                         expected_rclone_config_sha256=self.config_digest, now=1234567890)
        arguments.update(changes)
        return operational.create_google_operational_generation(**arguments)

    def inspect(self, generation, **changes):
        arguments = dict(prepared_state=self.prepared, operational_state=self.operational,
                         generation_id=generation.generation_id, request_id=REQUEST,
                         expected_folder_name=FOLDER, expected_authorization_sha256=self.auth_digest,
                         expected_rclone_config_sha256=self.config_digest)
        arguments.update(changes)
        return operational.inspect_google_operational_generation(**arguments)

    def assert_private(self, path, mode):
        info = path.lstat()
        self.assertEqual(info.st_uid, os.geteuid())
        self.assertEqual(stat.S_IMODE(info.st_mode), mode)
        self.assertFalse(stat.S_ISLNK(info.st_mode))

    def assert_safe(self, value):
        exposed = repr(value) + str(value)
        if isinstance(value, dict):
            exposed += json.dumps(value)
        for private in (SECRET, ACCESS, REFRESH, REQUEST, FOLDER, ORIGIN,
                        self.auth_digest, self.config_digest, str(self.root)):
            self.assertNotIn(private, exposed)

    def assert_disabled(self, generation):
        public = generation.projection()
        for field in ("enabled", "account_verified", "folder_exists_verified", "automatic_deletion"):
            self.assertIs(public[field], False)
        self.assertIs(public["test_required"], True)
        self.assertIs(public["mutable_configuration_requires_validation"], True)
        self.assertEqual(public["approved_uplinks"], ["wifi"])
        self.assertIs(public["allow_metered"], False)
        self.assert_safe(public)
        self.assert_safe(generation)

    def test_seed_uses_separate_private_writable_config_and_preserves_original(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("no process")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("no process")), \
                mock.patch("socket.socket", side_effect=AssertionError("no network")), \
                mock.patch("os.system", side_effect=AssertionError("no shell")):
            generation = self.create()
            inspected = self.inspect(generation)
        self.assertEqual(generation, inspected)
        self.assertTrue(generation.mutable_config_matches_seed)
        directory = self.operational / generation.generation_id
        self.assertEqual(set(directory.iterdir()), {directory / "generation.json", directory / "config"})
        self.assert_private(directory, 0o700)
        self.assert_private(directory / "config", 0o700)
        self.assert_private(generation.config_path, 0o600)
        self.assert_private(directory / "generation.json", 0o600)
        self.assertNotEqual(generation.config_path.stat().st_ino, self.seed_path.stat().st_ino)
        self.assertEqual(generation.config_path.read_bytes(), self.seed_path.read_bytes())
        self.assertEqual(self.snapshot(), self.original)
        self.assert_disabled(generation)

    def test_permissive_umask_does_not_expose_seed_or_provenance(self):
        previous = os.umask(0)
        try:
            generation = self.create()
        finally:
            os.umask(previous)
        for directory in (generation.config_path.parent, generation.config_path.parent.parent):
            self.assert_private(directory, 0o700)
        self.assert_private(generation.config_path, 0o600)
        self.assert_private(generation.config_path.parent.parent / "generation.json", 0o600)

    def test_mutable_config_replacement_is_only_byte_observation_not_trust(self):
        generation = self.create()
        provenance = generation.config_path.parent.parent / "generation.json"
        before = provenance.read_bytes(), provenance.stat().st_ino
        original_inode = generation.config_path.stat().st_ino
        replacement = generation.config_path.parent / "refresh.tmp"
        # Exercise a real rename as rclone uses for refresh, with different token
        # bytes and layout. This module does not accept those bytes for use.
        replacement.write_bytes(b"opaque unvalidated refreshed config PRIVATE-new-token\n")
        replacement.chmod(0o600)
        replacement.replace(generation.config_path)
        inspected = self.inspect(generation)
        self.assertFalse(inspected.mutable_config_matches_seed)
        self.assertNotEqual(generation.config_path.stat().st_ino, original_inode)
        self.assertEqual((provenance.read_bytes(), provenance.stat().st_ino), before)
        self.assertEqual(inspected.destination_id, generation.destination_id)
        self.assert_disabled(inspected)
        self.assertEqual(self.snapshot(), self.original)

    def test_generations_and_folder_bindings_have_distinct_history_identity(self):
        first, second = self.create(), self.create()
        self.assertNotEqual(first.generation_id, second.generation_id)
        self.assertNotEqual(first.destination_id, second.destination_id)
        self.assertNotEqual(first.destination_id, "google_drive_rclone/rcsp_google:")
        # Even a deliberately repeated random ID at another state root cannot
        # reuse an earlier folder's history destination identity.
        alternate = self.root / "another-operational"
        alternate.mkdir(mode=0o700)
        changed = {**self.manifest, "folder_name": "Another App Folder"}
        self.write_json(self.manifest_path, changed)
        with mock.patch.object(operational.secrets, "token_hex", side_effect=[first.generation_id, "c" * 32]):
            other = self.create(operational_state=alternate, expected_folder_name="Another App Folder")
        self.assertEqual(other.generation_id, first.generation_id)
        self.assertNotEqual(other.destination_id, first.destination_id)
        # Verify the existing real SQLite uniqueness boundary: identical file
        # content already verified for the old folder still queues for the new
        # generation when the future coordinator uses this destination identity.
        batch_path = self.root / "fixture.ready"
        (batch_path / "files").mkdir(parents=True, mode=0o700)
        content = b"same local bytes, different destination folder"
        (batch_path / "files" / "recording.txt").write_bytes(content)
        self.write_json(batch_path / "manifest.json", {
            "schema_version": 1, "files": [{"relative": "recording.txt",
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}]})
        batch = ReadyBatch.load(batch_path)
        with History(self.root / "history.sqlite3") as history:
            self.assertEqual(history.enqueue(batch, first.destination_id, 100), 1)
            history.update(history.jobs()[0]["id"], "verified", 101)
            self.assertEqual(history.enqueue(batch, first.destination_id, 102), 0)
            self.assertEqual(history.enqueue(batch, other.destination_id, 102), 1)
            self.assertEqual([job["state"] for job in history.jobs()], ["verified", "queued"])
        with self.assertRaises(operational.GoogleOperationalError):
            self.inspect(first, expected_folder_name="Another App Folder")

    def test_existing_final_empty_or_populated_is_never_replaced(self):
        for populated in (False, True):
            with self.subTest(populated=populated):
                generation_id = ("b" if populated else "c") * 32
                final = self.operational / generation_id
                final.mkdir(mode=0o700)
                if populated:
                    (final / "retained").write_text("keep")
                before = final.stat().st_ino
                with mock.patch.object(operational.secrets, "token_hex", side_effect=[generation_id, ("e" if populated else "f") * 32]):
                    with self.assertRaises(operational.GoogleOperationalError) as error:
                        self.create()
                self.assertEqual(error.exception.code, "google_operational_exists")
                self.assertEqual(final.stat().st_ino, before)
                self.assertEqual(set(p.name for p in final.iterdir()), {"retained"} if populated else set())
                partial = list(self.operational.glob("*.incomplete"))
                self.assertTrue(partial)
                self.assertTrue(any((p / "config" / "rclone.conf").exists() for p in partial))
        self.assertEqual(self.snapshot(), self.original)

    def test_write_failure_retains_private_partial_and_never_publishes(self):
        original_write = destination._write_private
        def fail_manifest(fd, name, payload):
            if name == "generation.json":
                raise OSError(errno.ENOSPC, "PRIVATE must not escape")
            return original_write(fd, name, payload)
        with mock.patch.object(destination, "_write_private", side_effect=fail_manifest):
            with self.assertRaises(operational.GoogleOperationalError) as error:
                self.create()
        self.assertEqual(error.exception.code, "google_operational_creation_failed")
        self.assert_safe(error.exception)
        retained = list(self.operational.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertTrue(retained[0].name.endswith(".incomplete"))
        self.assert_private(retained[0], 0o700)
        self.assert_private(retained[0] / "config" / "rclone.conf", 0o600)
        self.assertEqual(self.snapshot(), self.original)

    def test_existing_incomplete_collision_is_retained_without_writes(self):
        partial = self.operational / (".google-operational-" + "e" * 32 + ".incomplete")
        partial.mkdir(mode=0o700)
        (partial / "retained").write_bytes(b"unchanged")
        before = partial.stat().st_ino
        with mock.patch.object(operational.secrets, "token_hex", side_effect=["d" * 32, "e" * 32]):
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
        self.assertEqual(partial.stat().st_ino, before)
        self.assertEqual((partial / "retained").read_bytes(), b"unchanged")
        self.assertEqual(set(p.name for p in partial.iterdir()), {"retained"})

    def test_independent_expected_hashes_and_label_reject_rebound_manifest(self):
        changes = [dict(expected_authorization_sha256="d" * 64),
                   dict(expected_rclone_config_sha256="e" * 64),
                   dict(expected_folder_name="Different Label")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(operational.GoogleOperationalError):
                self.create(**change)
        # Replace the actual config with another valid Google seed and update
        # the untrusted manifest consistently, preserving its claimed grant hash.
        forged_bundle = copy.deepcopy(self.bundle)
        forged_bundle["authorization"]["access_token"] = "PRIVATE-forged-token"
        forged = destination._render_config(destination._validate_bundle(forged_bundle))
        self.seed_path.write_bytes(forged)
        self.write_json(self.manifest_path, {**self.manifest, "rclone_config_sha256": hashlib.sha256(forged).hexdigest()})
        with self.assertRaises(operational.GoogleOperationalError):
            self.create()
        self.assertEqual(list(self.operational.iterdir()), [])

    def test_preparation_disabled_policy_and_exact_config_are_validated(self):
        for field, value in (("enabled", True), ("test_required", False),
                             ("automatic_deletion", True), ("allow_metered", True),
                             ("approved_uplinks", ["wifi", "ethernet"]), ("schema_version", True)):
            with self.subTest(field=field):
                self.write_json(self.manifest_path, {**self.manifest, field: value})
                with self.assertRaises(operational.GoogleOperationalError):
                    self.create()
        self.write_json(self.manifest_path, self.manifest)
        bad = self.seed_path.read_bytes() + b"root_folder_id = unauthorized\n"
        self.seed_path.write_bytes(bad)
        bad_digest = hashlib.sha256(bad).hexdigest()
        self.write_json(self.manifest_path, {**self.manifest, "rclone_config_sha256": bad_digest})
        with self.assertRaises(operational.GoogleOperationalError):
            self.create(expected_rclone_config_sha256=bad_digest)
        self.assertEqual(list(self.operational.iterdir()), [])

    def test_unsafe_paths_and_overlapping_state_roots_fail_before_writes(self):
        link = self.root / "linked"
        link.symlink_to(self.operational, target_is_directory=True)
        cases = [dict(operational_state=link), dict(operational_state=self.prepared),
                 dict(operational_state=self.proof), dict(operational_state=self.root),
                 dict(operational_state="relative"),
                 dict(operational_state=str(self.operational / ".." / "operational")),
                 dict(request_id="../escape"), dict(prepared_state=self.root / "wrong-name")]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(operational.GoogleOperationalError):
                self.create(**case)
        self.assertEqual(list(self.operational.iterdir()), [])
        self.assertEqual(self.snapshot(), self.original)

    def test_insecure_prepared_and_operational_permissions_are_rejected(self):
        for path, insecure, secure in ((self.prepared, 0o750, 0o700),
                                       (self.proof, 0o770, 0o700),
                                       (self.seed_path, 0o640, 0o600),
                                       (self.manifest_path, 0o644, 0o600),
                                       (self.operational, 0o755, 0o700)):
            with self.subTest(path=path):
                path.chmod(insecure)
                try:
                    with self.assertRaises(operational.GoogleOperationalError):
                        self.create()
                finally:
                    path.chmod(secure)
        self.assertEqual(list(self.operational.iterdir()), [])

    def test_prepared_symlink_and_hardlink_inputs_are_rejected(self):
        original = self.seed_path.read_bytes()
        outside = self.root / "original.conf"
        self.seed_path.rename(outside)
        self.seed_path.symlink_to(outside)
        with self.assertRaises(operational.GoogleOperationalError):
            self.create()
        self.seed_path.unlink()
        os.link(outside, self.seed_path)
        with self.assertRaises(operational.GoogleOperationalError):
            self.create()
        self.assertEqual(outside.read_bytes(), original)
        self.assertEqual(list(self.operational.iterdir()), [])

    def test_inspection_rejects_forged_enable_or_seed_provenance(self):
        generation = self.create()
        path = generation.config_path.parent.parent / "generation.json"
        original = json.loads(path.read_text())
        for key, value in (("enabled", True), ("test_required", False),
                           ("source_authorization_sha256", "c" * 64),
                           ("seed_rclone_config_sha256", "d" * 64),
                           ("source_folder_name", "Other Folder"),
                           ("destination_id", "google_drive_rclone/rcsp_google:"),
                           ("extra", False), ("created_at", True)):
            with self.subTest(key=key):
                self.write_json(path, {**original, key: value})
                with self.assertRaises(operational.GoogleOperationalError):
                    self.inspect(generation)
        self.write_json(path, original)
        self.assert_disabled(self.inspect(generation))

    def test_inspection_rejects_missing_symlink_hardlink_and_oversized_mutable_config(self):
        generation = self.create()
        path = generation.config_path
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(operational.GoogleOperationalError):
            self.inspect(generation)
        path.symlink_to(self.seed_path)
        with self.assertRaises(operational.GoogleOperationalError):
            self.inspect(generation)
        path.unlink()
        other = path.parent / "other"
        other.write_bytes(original)
        other.chmod(0o600)
        os.link(other, path)
        with self.assertRaises(operational.GoogleOperationalError):
            self.inspect(generation)
        path.unlink()
        for data in (b"", b"x" * 65537):
            path.write_bytes(data)
            path.chmod(0o600)
            with self.assertRaises(operational.GoogleOperationalError):
                self.inspect(generation)

    def test_inspection_rejects_symlinked_generation_and_insecure_nested_files(self):
        generation = self.create()
        root = generation.config_path.parent.parent
        for path, bad, good in ((root, 0o750, 0o700),
                                (root / "config", 0o770, 0o700),
                                (root / "generation.json", 0o640, 0o600),
                                (generation.config_path, 0o644, 0o600)):
            path.chmod(bad)
            try:
                with self.assertRaises(operational.GoogleOperationalError):
                    self.inspect(generation)
            finally:
                path.chmod(good)
        relocated = self.operational / "relocated"
        root.rename(relocated)
        root.symlink_to(relocated, target_is_directory=True)
        with self.assertRaises(operational.GoogleOperationalError):
            self.inspect(generation)

    def test_source_file_substitution_during_creation_retains_partial(self):
        original_write = destination._write_private
        def substitute(fd, name, payload):
            result = original_write(fd, name, payload)
            if name == "generation.json":
                # Same bytes on a different inode must also be noticed.
                replacement = self.proof / "replacement"
                replacement.write_bytes(self.seed_path.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(self.seed_path)
            return result
        with mock.patch.object(destination, "_write_private", side_effect=substitute):
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
        retained = list(self.operational.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertTrue(retained[0].name.endswith(".incomplete"))
        self.assertTrue((retained[0] / "generation.json").exists())

    def test_operational_root_rename_during_creation_does_not_write_replacement(self):
        moved = self.root / "moved-operational"
        original_write = destination._write_private
        def relocate(fd, name, payload):
            result = original_write(fd, name, payload)
            if name == "generation.json":
                self.operational.rename(moved)
                self.operational.mkdir(mode=0o700)
            return result
        with mock.patch.object(destination, "_write_private", side_effect=relocate):
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
        self.assertEqual(list(self.operational.iterdir()), [])
        retained = list(moved.iterdir())
        self.assertEqual(len(retained), 1)
        self.assertTrue(retained[0].name.endswith(".incomplete"))
        self.assertEqual(self.snapshot(), self.original)

    def test_swapped_partial_entry_is_not_returned_as_a_valid_generation(self):
        original_publish = operational._publish
        retained = self.operational / "retained-original"

        def replace_partial(root, partial, generation_id):
            (self.operational / partial).rename(retained)
            (self.operational / partial).mkdir(mode=0o700)
            original_publish(root, partial, generation_id)

        with mock.patch.object(operational, "_publish", side_effect=replace_partial):
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
        self.assertTrue((retained / "config" / "rclone.conf").is_file())
        self.assertEqual(self.snapshot(), self.original)

    def test_post_publication_fsync_failure_retains_recoverable_final(self):
        original_publish = operational._publish
        published = []
        def publish_then_fail(root, partial, generation_id):
            original_publish(root, partial, generation_id)
            published.append(generation_id)
            raise OSError(errno.EIO, "PRIVATE failed after atomic publication")
        with mock.patch.object(operational, "_publish", side_effect=publish_then_fail):
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
        self.assertEqual(len(published), 1)
        # Inspection provides local conservative reconciliation; creation never
        # retries over or deletes the already-published final directory.
        descriptor = operational.GoogleOperationalGeneration(
            published[0], "unused", self.operational / published[0] / "config" / "rclone.conf", False)
        recovered = self.inspect(descriptor)
        self.assertTrue(recovered.mutable_config_matches_seed)
        self.assert_disabled(recovered)
        self.assertEqual(self.snapshot(), self.original)

    def test_writable_ancestor_and_symlinked_ancestor_fail_closed(self):
        self.root.chmod(0o777)
        try:
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
        finally:
            self.root.chmod(0o700)
        linked = self.root / "linked-root"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(operational.GoogleOperationalError):
            self.create(operational_state=linked / self.operational.name)
        self.assertEqual(list(self.operational.iterdir()), [])

    def test_duplicate_or_oversized_provenance_fails_without_exposing_contents(self):
        generation = self.create()
        path = generation.config_path.parent.parent / "generation.json"
        for contents in (b'{"enabled":false,"enabled":false}', b"x" * 8193):
            path.write_bytes(contents)
            with self.assertRaises(operational.GoogleOperationalError) as error:
                self.inspect(generation)
            self.assert_safe(error.exception)

    def test_source_preparation_replacement_after_creation_invalidates_inspection(self):
        generation = self.create()
        path = generation.config_path.parent.parent / "generation.json"
        provenance = path.read_bytes()
        self.write_json(self.manifest_path, {**self.manifest, "folder_name": "Unapproved Label"})
        with self.assertRaises(operational.GoogleOperationalError):
            self.inspect(generation)
        self.assertEqual(path.read_bytes(), provenance)

    def test_prepared_file_and_operational_file_mutation_while_reading_fails_closed(self):
        generation = self.create()
        original_read = os.read
        target_inode = generation.config_path.stat().st_ino
        mutated = [False]
        def mutate(fd, size):
            result = original_read(fd, size)
            if not mutated[0] and os.fstat(fd).st_ino == target_inode:
                mutated[0] = True
                generation.config_path.write_bytes(b"changed while reading")
            return result
        with mock.patch.object(operational.os, "read", side_effect=mutate):
            with self.assertRaises(operational.GoogleOperationalError):
                self.inspect(generation)
        self.assertTrue(mutated[0])
        self.assertEqual(self.snapshot(), self.original)

    def test_root_or_saved_root_identity_rejected_before_open_or_write(self):
        with mock.patch.object(operational.os, "getresuid", return_value=(os.getuid(), os.getuid(), 0)), \
                mock.patch.object(destination, "_private_directory") as opened:
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
            opened.assert_not_called()
        with mock.patch.object(operational.os, "geteuid", return_value=0), \
                mock.patch.object(destination, "_private_directory") as opened:
            with self.assertRaises(operational.GoogleOperationalError):
                self.create()
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
