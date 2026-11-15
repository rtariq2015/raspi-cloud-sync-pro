"""Disposable worker history/report fixtures; no devices or cloud accounts."""

import copy
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raspi_cloud_sync_pro import backup_status as status
from raspi_cloud_sync_pro.dashboard import create_app, initialize_dashboard
from raspi_cloud_sync_pro.history import History


class BackupStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = self.root / "private-worker"
        self.worker.mkdir(mode=0o700)
        self.output = self.root / "public-status"
        self.output.mkdir(mode=0o750)
        self.database = self.worker / "history.sqlite3"
        with History(self.database):
            pass
        self.database.chmod(0o600)
        self.worker_report = {"jobs_added": 0, "rejected_batches": 0, "cloud_results": ["paused"],
                              "observed_uplink": "wifi", "metered": False,
                              "network_policy_allows_upload": False, "updated_at": 1000,
                              "destinations": ["google_drive_rclone/private-remote:sensitive-folder"],
                              "automatic_deletion": False, "pending_jobs": 1, "failed_jobs": 0}
        path = self.worker / "worker-status.json"
        path.write_text(json.dumps(self.worker_report))
        path.chmod(0o600)

    def add_job(self, state="verified", *, error=None, destination="google_drive_rclone/private-remote:sensitive-folder"):
        with sqlite3.connect(self.database) as connection:
            ident = connection.execute("SELECT COUNT(*)+1 FROM jobs").fetchone()[0]
            connection.execute(
                "INSERT INTO jobs (id,destination,batch_root,relative,size,sha256,state,attempts,next_attempt,error_code,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, destination, "/private/patient-path", "sensitive-patient-name-" + str(ident),
                 123, str(ident), state, 2, 1100 if state == "retry" else 0, error, 900, 1000))

    def report(self, service="running"):
        return status.observe_status(self.database, self.worker, now=1001,
                                     service_reader=lambda: {"state": service})

    def publish(self, report=None):
        report = self.report() if report is None else report
        status.publish_status(self.output, report)
        return self.output / status.REPORT_NAME

    def test_whole_history_totals_are_not_limited_to_recent_rows_and_reads_do_not_change_jobs(self):
        for _ in range(205):
            self.add_job()
        for state in ("queued", "retry", "uploading", "uploaded", "verifying", "failed"):
            self.add_job(state)
        before = self.database.read_bytes()
        report = self.report()
        self.assertEqual(report["history"]["total_jobs"], 211)
        self.assertEqual(report["history"]["counts"]["verified"], 205)
        self.assertEqual(len(report["jobs"]), 100)
        self.assertEqual(report["google_drive"]["state"], "historically_verified")
        self.assertEqual(report["google_drive"]["verified_jobs"], 205)
        self.assertEqual(report["google_drive"]["last_verified_at"], 1000)
        self.assertEqual(report["google_drive"]["current_access"], "not_tested")
        self.assertEqual(before, self.database.read_bytes())
        self.assertFalse(report["byte_progress_available"])

    def test_redacts_filenames_remotes_paths_and_unknown_error_text(self):
        self.add_job("failed", error="Bearer secret-token https://private/patient-path")
        report = self.report()
        raw = json.dumps(report)
        for private in ("sensitive", "private-remote", "patient", "secret-token", "Bearer", "/private"):
            self.assertNotIn(private, raw)
        job = report["jobs"][0]
        self.assertEqual(job["label"], "Backup #1")
        self.assertEqual(job["destination"], "Google Drive")
        self.assertEqual(job["error_code"], "backup_error")
        self.assertEqual(job["error_message"], status.ERRORS["backup_error"])

    def test_error_allowlist_retry_and_stopped_service_do_not_claim_current_cloud_access(self):
        self.add_job("retry", error="rclone_timeout")
        report = self.report("stopped")
        self.assertEqual(report["service"]["state"], "stopped")
        self.assertEqual(report["jobs"][0]["next_attempt"], 1100)
        self.assertEqual(report["jobs"][0]["error_message"], status.ERRORS["rclone_timeout"])
        self.assertEqual(report["google_drive"]["state"], "recorded")
        self.assertFalse(report["worker"]["network_policy_allows_upload"])
        self.assertEqual(report["worker"]["outcomes"], ["paused"])

    def test_stale_bridge_and_worker_ages_are_recomputed_without_current_healthy_claim(self):
        self.add_job()
        self.publish()
        report = status.read_status(self.output, now=1500)
        self.assertEqual(report["state"], "stale")
        self.assertEqual(report["age_seconds"], 499)
        self.assertEqual(report["service"]["state"], "unknown")
        self.assertEqual(report["worker"]["state"], "stale")
        self.assertEqual(report["worker"]["age_seconds"], 500)
        self.assertEqual(report["history"]["counts"]["verified"], 1)

    def test_missing_invalid_or_unknown_schema_database_means_unknown_not_zero(self):
        for change in ("missing", "schema", "state"):
            with self.subTest(change=change):
                if change == "missing":
                    path = self.worker / "missing.sqlite3"
                else:
                    path = self.database
                    with sqlite3.connect(path) as connection:
                        if change == "schema":
                            connection.execute("PRAGMA user_version=8")
                        else:
                            connection.execute("PRAGMA user_version=1")
                            self.add_job("invented-state")
                report = status.observe_status(path, self.worker, now=1001)
                self.assertEqual(report["history"]["state"], "unavailable")
                self.assertIsNone(report["history"]["counts"])
                self.assertEqual(report["jobs"], [])
        self.assertFalse((self.worker / "missing.sqlite3").exists())

    def test_current_wal_rows_are_visible_without_immutable_mode(self):
        connection = sqlite3.connect(self.database)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        self.add_job()
        report = self.report()
        self.assertEqual(report["history"]["counts"]["verified"], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_locked_database_and_query_error_fail_without_changing_history(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            self.assertIsNone(self.report()["history"]["counts"])
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self.report()["history"]["total_jobs"], 0)

    def test_publication_is_atomic_and_uses_read_only_group_permissions(self):
        self.add_job()
        path = self.publish()
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        before = path.read_bytes()
        with mock.patch.object(status.os, "replace", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([x.name for x in self.output.iterdir()], [status.REPORT_NAME])
        self.assertEqual(status.read_status(self.output, now=1002)["history"]["counts"]["verified"], 1)

    def test_reader_rejects_links_writable_files_and_untrusted_fields(self):
        self.add_job()
        report = self.report()
        mutations = [lambda d: d.update(secret="token"),
                     lambda d: d["service"].update(secret="token"),
                     lambda d: d["jobs"][0].update(label="sensitive-patient-name"),
                     lambda d: d["jobs"][0].update(error_message="Bearer secret-token"),
                     lambda d: d["worker"].update(destinations=["private-remote"]),
                     lambda d: d.update(observed_at=float("nan")),
                     lambda d: d.update(observed_at=999999),
                     lambda d: d["history"]["counts"].update(verified=True),
                     lambda d: d.update(byte_progress_available=True)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(report)
                mutate(value)
                path = self.output / status.REPORT_NAME
                path.write_text(json.dumps({"schema_version": 1, **value}))
                path.chmod(0o640)
                self.assertEqual(status.read_status(self.output, now=1002)["state"], "invalid")
        path = self.publish(report)
        path.chmod(0o660)
        self.assertEqual(status.read_status(self.output, now=1002)["state"], "invalid")
        path.chmod(0o640)
        alias = self.root / "alias"
        alias.symlink_to(self.output)
        self.assertEqual(status.read_status(alias, now=1002)["state"], "invalid")
        os.link(path, self.root / "hardlink")
        self.assertEqual(status.read_status(self.output, now=1002)["state"], "invalid")

    def test_reader_refuses_duplicate_keys_oversize_and_missing_reports(self):
        self.assertEqual(status.read_status(None)["state"], "not_connected")
        self.assertEqual(status.read_status(self.output)["state"], "unavailable")
        path = self.output / status.REPORT_NAME
        for content in ('{"schema_version":1,"schema_version":1}', "x" * (status.MAX_REPORT + 1)):
            path.write_text(content)
            path.chmod(0o640)
            self.assertEqual(status.read_status(self.output, now=1002)["state"], "invalid")

    def test_service_query_is_fixed_read_only_and_environment_is_clean(self):
        result = mock.Mock(stdout="ActiveState=active\nSubState=running\n")
        with mock.patch.object(status.subprocess, "run", return_value=result) as run:
            self.assertEqual(status.service_observation(), {"state": "running"})
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/systemctl", "show", "raspi-cloud-mass-storage-backup.service",
                                   "--property=ActiveState,SubState"])
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertEqual(kwargs["timeout"], 3)
        with mock.patch.object(status.subprocess, "run", side_effect=OSError("private detail")):
            self.assertEqual(status.service_observation(), {"state": "unknown"})

    def test_web_reads_never_execute_service_or_rclone_commands(self):
        self.add_job()
        report = self.report()
        self.publish(report)
        web = self.root / "web"
        web.mkdir(mode=0o700)
        initialize_dashboard(web, password="synthetic-owner-password")
        initialize_dashboard(web, username="viewer", role="viewer", password="synthetic-viewer-password")
        with mock.patch.object(status.subprocess, "run", side_effect=AssertionError("No command from HTTP")):
            app = create_app(web, backup_status=self.output, network_reader=lambda: {})
            client = app.test_client()
            self.assertEqual(client.get("/api/backups").status_code, 401)
            client.get("/login")
            with client.session_transaction() as session:
                csrf = session["csrf"]
            client.post("/login", data={"csrf": csrf, "username": "viewer", "password": "synthetic-viewer-password"})
            with mock.patch("raspi_cloud_sync_pro.dashboard.read_status",
                            side_effect=lambda path: status.read_status(path, now=1002)):
                response = client.get("/api/backups")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["history"]["counts"]["verified"], 1)
                self.assertEqual(client.get("/api/status").json["cloud"], "historically_verified")
                legacy = client.get("/api/history")
            self.assertEqual(legacy.json["jobs"][0]["relative"], "Backup #1")
            for response in (response, legacy):
                self.assertNotIn(b"sensitive", response.data)
                self.assertNotIn(b"private-remote", response.data)
            self.assertEqual(client.post("/api/backups").status_code, 405)

    def test_owner_only_advanced_tools_are_disabled_by_default_and_fail_closed(self):
        web = self.root / "web"
        web.mkdir(mode=0o700)
        initialize_dashboard(web, password="synthetic-owner-password")
        initialize_dashboard(web, username="viewer", role="viewer", password="synthetic-viewer-password")
        reader = mock.Mock(return_value={"state": "ready", "open_url": "http://127.0.0.1:5573/",
                                         "client_tunnel_verified": False})

        def client_for(app, username, password):
            client = app.test_client()
            client.get("/login")
            with client.session_transaction() as session:
                csrf = session["csrf"]
            self.assertEqual(client.post("/login", data={"csrf": csrf, "username": username,
                                                         "password": password}).status_code, 302)
            return client

        app = create_app(web, rclone_admin_reader=reader, network_reader=lambda: {})
        self.assertEqual(app.test_client().get("/api/advanced/rclone").status_code, 401)
        owner = client_for(app, "owner", "synthetic-owner-password")
        self.assertEqual(owner.get("/api/advanced/rclone").json["state"], "disabled")
        reader.assert_not_called()
        viewer = client_for(app, "viewer", "synthetic-viewer-password")
        self.assertEqual(viewer.get("/api/advanced/rclone").status_code, 403)
        self.assertNotIn(b'id="rclone-tools"', viewer.get("/").data)
        self.assertNotIn(b'data-view="advanced"', viewer.get("/").data)
        self.assertIn(b'id="rclone-tools"', owner.get("/").data)

        app = create_app(web, rclone_admin_access=True, rclone_admin_reader=reader, network_reader=lambda: {})
        viewer = client_for(app, "viewer", "synthetic-viewer-password")
        self.assertEqual(viewer.get("/api/advanced/rclone").status_code, 403)
        reader.assert_not_called()
        owner = client_for(app, "owner", "synthetic-owner-password")
        ready = owner.get("/api/advanced/rclone")
        self.assertEqual(ready.json, reader.return_value)
        self.assertFalse(ready.json["client_tunnel_verified"])
        for poisoned in ({"state": "ready", "open_url": "https://public-admin.example/", "client_tunnel_verified": False},
                         {"state": "ready", "open_url": "http://user:secret@127.0.0.1:5573/", "client_tunnel_verified": False},
                         {**reader.return_value, "password": "secret"}):
            reader.return_value = poisoned
            response = owner.get("/api/advanced/rclone")
            self.assertEqual(response.json["state"], "unavailable")
            self.assertIsNone(response.json["open_url"])
            self.assertNotIn(b"secret", response.data)

    def test_new_dashboard_options_are_explicit_and_do_not_start_admin_services(self):
        from raspi_cloud_sync_pro.cli import main
        with mock.patch("raspi_cloud_sync_pro.dashboard.serve_dashboard") as serve:
            self.assertEqual(main(["dashboard", "--state", "/private/dashboard"]), 0)
            self.assertNotIn("backup_status", serve.call_args.kwargs)
            self.assertNotIn("rclone_admin_access", serve.call_args.kwargs)
        with mock.patch("raspi_cloud_sync_pro.dashboard.serve_dashboard") as serve:
            self.assertEqual(main(["dashboard", "--state", "/private/dashboard", "--backup-status",
                                   "/run/raspi-cloud-backup-status", "--rclone-admin-access"]), 0)
            self.assertEqual(serve.call_args.kwargs["backup_status"], Path("/run/raspi-cloud-backup-status"))
            self.assertIs(serve.call_args.kwargs["rclone_admin_access"], True)

    def test_missing_bridge_masks_direct_history_instead_of_falling_back_to_private_paths(self):
        self.add_job()
        web = self.root / "web"
        web.mkdir(mode=0o700)
        initialize_dashboard(web, password="synthetic-owner-password")
        app = create_app(web, history=self.database, backup_status=self.output, network_reader=lambda: {})
        client = app.test_client()
        client.get("/login")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        client.post("/login", data={"csrf": csrf, "username": "owner", "password": "synthetic-owner-password"})
        response = client.get("/api/backups")
        self.assertEqual(response.json["state"], "unavailable")
        self.assertIsNone(response.json["history"]["counts"])
        self.assertFalse(client.get("/api/history").json["available"])
        self.assertEqual(client.get("/api/status").json["cloud"], "unknown")


if __name__ == "__main__":
    unittest.main()
