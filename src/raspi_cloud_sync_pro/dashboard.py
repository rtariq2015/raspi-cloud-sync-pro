"""Authenticated dashboard/PWA. No gadget, network mutation or cloud activation API."""

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .accounts import change_dashboard_password, initialize_dashboard as initialize_dashboard, read_auth as _read_auth
from .config import _unique_object
from .errors import BackupError
from .history import read_history
from .network_diagnostics import network_status
from .staging import open_regular, relative_path
from .web_ingest import stage_upload


def worker_snapshot(directory, *, now=None):
    """Read only a bounded status report; it cannot prove account setup or liveness."""
    unavailable = {"state": "not_connected" if directory is None else "unavailable",
                   "updated_at": None, "age_seconds": None, "pending_jobs": None,
                   "destination_count": None, "network_policy_allows_upload": None,
                   "reconciliation_pending": None, "outcomes": [],
                   "cloud_accounts_verified": False}
    if directory is None:
        return unavailable
    root = Path(directory)
    now = time.time() if now is None else now
    try:
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError()
        for parent in root.parents:
            info = parent.lstat()
            trusted_sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if not stat.S_ISDIR(info.st_mode) or (info.st_mode & 0o022 and not trusted_sticky):
                raise ValueError()
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ValueError()
        with open_regular(root, "worker-status.json") as stream:
            info = os.fstat(stream.fileno())
            if info.st_mode & 0o022 or info.st_size > 65536:
                raise ValueError()
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(data, dict):
            raise ValueError()
        updated = data.get("updated_at")
        if type(updated) not in (int, float) or not math.isfinite(updated) or not 0 <= updated <= now + 30 \
                or data.get("automatic_deletion") is not False:
            raise ValueError()
        age = max(0, int(now - updated))
        if "error" in data:
            if set(data) != {"error", "updated_at", "automatic_deletion"} \
                    or data["error"] != "staged_worker_tick_failed":
                raise ValueError()
            return {**unavailable, "state": "error",
                    "updated_at": updated, "age_seconds": age}
        required = {"jobs_added", "rejected_batches", "cloud_results", "observed_uplink", "metered",
                    "network_policy_allows_upload", "updated_at", "destinations", "automatic_deletion", "pending_jobs"}
        if not required <= set(data) or set(data) - required - {"reconciliation_pending"}:
            raise ValueError()
        for field in ("jobs_added", "rejected_batches", "pending_jobs"):
            if type(data[field]) is not int or not 0 <= data[field] <= 10 ** 12:
                raise ValueError()
        destinations, outcomes = data["destinations"], data["cloud_results"]
        if not isinstance(destinations, list) or not 1 <= len(destinations) <= 8 \
                or any(not isinstance(value, str) or not value or len(value) > 512
                       or any(ord(char) < 32 for char in value) for value in destinations) \
                or len(set(destinations)) != len(destinations) or not isinstance(outcomes, list) \
                or len(outcomes) != len(destinations) \
                or any(value not in (None, "verified", "retry", "paused", "failed") for value in outcomes) \
                or data["observed_uplink"] not in ("wifi", "usb_host_ics", "unknown") \
                or any(type(data[field]) is not bool for field in ("metered", "network_policy_allows_upload")) \
                or type(data.get("reconciliation_pending", False)) is not bool:
            raise ValueError()
        return {**unavailable, "state": "stale" if age > 300 else "observed", "updated_at": updated,
                "age_seconds": age, "pending_jobs": data["pending_jobs"], "destination_count": len(destinations),
                "network_policy_allows_upload": data["network_policy_allows_upload"],
                "reconciliation_pending": data.get("reconciliation_pending", False), "outcomes": outcomes}
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return {**unavailable, "state": "invalid"}
    except (OSError, BackupError):
        return unavailable


def create_app(state, *, history=None, worker_state=None, public_origin=None, network_reader=network_status):
    from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for
    from werkzeug.security import check_password_hash, generate_password_hash

    state = Path(state).absolute()
    if state.is_symlink() or state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise ValueError("Dashboard state must be private and owned by the service user")
    auth = _read_auth(state)
    origin = urlsplit(public_origin or "http://localhost")
    if public_origin and (origin.scheme != "https" or not origin.hostname or origin.username
                          or origin.password or origin.query or origin.fragment or origin.path not in ("", "/")):
        raise ValueError("Public origin must be an HTTPS origin without credentials or a path")
    app = Flask(__name__)
    app.config.update(SECRET_KEY=auth["secret"], MAX_CONTENT_LENGTH=102 * 1024 * 1024,
                      MAX_FORM_MEMORY_SIZE=32 * 1024, MAX_FORM_PARTS=8,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_SECURE=bool(public_origin),
                      PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
                      TRUSTED_HOSTS=["localhost", "127.0.0.1", origin.hostname])
    lock = threading.RLock()
    sessions, failures, account_attempts = {}, [], []
    cache = {"at": 0, "data": None}
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))

    def current_user():
        if hasattr(g, "login"):
            return g.login
        try:
            current_auth = _read_auth(state)
        except (OSError, ValueError, TypeError, BackupError):
            with lock:
                sessions.clear()
            g.login = None
            return None
        with lock:
            now = time.time()
            for sid in list(sessions):
                if sessions[sid]["expires"] <= now:
                    del sessions[sid]
            login = sessions.get(session.get("sid"))
            user = current_auth["users"].get(login["username"]) if login else None
            valid = user and current_auth["secret"] == auth["secret"] and login["role"] == user["role"] \
                and secrets.compare_digest(login["credential_tag"], hashlib.sha256(user["hash"].encode()).hexdigest())
            if login and not valid:
                sessions.pop(session.get("sid"), None)
            g.login = login if valid and login["expires"] > now else None
            return g.login

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.before_request
    def protect():
        if request.routing_exception is not None:
            raise request.routing_exception
        if request.method not in ("GET", "HEAD", "POST"):
            abort(405)
        if request.method == "POST":
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            if not isinstance(supplied, str) or not supplied.isascii() or len(supplied) > 256 or not session.get("csrf") \
                    or not secrets.compare_digest(supplied, session["csrf"]):
                abort(403)
        if request.endpoint not in ("login", "static", "manifest", "service_worker"):
            if not current_user():
                if request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for("login"))

    @app.after_request
    def security_headers(response):
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        if public_origin:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    @app.errorhandler(429)
    @app.errorhandler(500)
    @app.errorhandler(503)
    def safe_error(error):
        return jsonify(error={400: "invalid_request", 401: "sign_in_required", 403: "access_denied",
                              404: "not_found", 405: "method_not_allowed", 413: "upload_too_large",
                              429: "try_later", 503: "temporarily_unavailable"}.get(error.code, "request_failed")), error.code

    @app.route("/login", methods=["GET", "POST"])
    def login():
        message = None
        if request.method == "POST":
            now = time.time()
            with lock:
                for sid in list(sessions):
                    if sessions[sid]["expires"] <= now:
                        del sessions[sid]
                failures[:] = [stamp for stamp in failures if stamp > now - 60]
                if len(failures) >= 10 or len(sessions) >= 32:
                    abort(429)
                failures.append(now)
            username, password = request.form.get("username", ""), request.form.get("password", "")
            try:
                current_auth = _read_auth(state)
                if current_auth["secret"] != auth["secret"]:
                    abort(503)
            except (OSError, ValueError, TypeError, BackupError):
                abort(503)
            user = current_auth["users"].get(username) if len(username) <= 64 else None
            valid = len(password) <= 256 and check_password_hash(user["hash"] if user else dummy_hash, password)
            if valid and user:
                old = session.get("sid")
                session.clear()
                sid = secrets.token_urlsafe(32)
                with lock:
                    sessions.pop(old, None)
                    sessions[sid] = {"username": username, "role": user["role"], "expires": now + 1800,
                                     "credential_tag": hashlib.sha256(user["hash"].encode()).hexdigest()}
                session["sid"] = sid
                csrf()
                return redirect(url_for("index"))
            message = "Sign-in details were not accepted."
        return render_template("login.html", csrf=csrf(), message=message)

    @app.post("/logout")
    def logout():
        with lock:
            sessions.pop(session.get("sid"), None)
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        return render_template("dashboard.html", user=current_user(), csrf=csrf(), version=__version__)

    @app.route("/account", methods=["GET", "POST"])
    def account():
        user = current_user()
        message = None
        code = 200
        if request.method == "POST":
            now = time.time()
            with lock:
                account_attempts[:] = [stamp for stamp in account_attempts if stamp > now - 60]
                if len(account_attempts) >= 6:
                    abort(429)
                account_attempts.append(now)
            if set(request.form) != {"csrf", "current_password", "password", "confirmation"} \
                    or any(len(request.form.getlist(key)) != 1 for key in request.form):
                abort(400)
            try:
                change_dashboard_password(state, user["username"], request.form["current_password"],
                                          request.form["password"], request.form["confirmation"])
            except ValueError:
                message = "Use a different password of 12–256 characters and matching confirmation."
                code = 400
            except BackupError as exc:
                if exc.code == "current_password_rejected":
                    message = "The current password was not accepted."
                    code = 400
                else:
                    abort(503)
            except OSError:
                abort(503)
            else:
                with lock:
                    for sid in list(sessions):
                        if sessions[sid]["username"] == user["username"]:
                            del sessions[sid]
                session.clear()
                return redirect(url_for("login"))
        return render_template("account.html", user=user, csrf=csrf(), message=message), code

    @app.get("/api/status")
    def status():
        with lock:
            if cache["data"] is None or time.monotonic() - cache["at"] > 15:
                try:
                    cache["data"] = network_reader()
                except Exception:
                    cache["data"] = {"cached_global_connectivity": "unknown", "interfaces": {}}
                cache["at"] = time.monotonic()
            network = cache["data"]
        disk = shutil.disk_usage(state)
        return jsonify(project="Raspi Cloud Sync Pro", version=__version__, network=network,
                       storage={"free": disk.free, "total": disk.total}, role=current_user()["role"],
                       cloud="not_configured", automatic_deletion=False, remote_access="external_setup_required",
                       worker=worker_snapshot(worker_state))

    @app.get("/api/history")
    def history_view():
        if history is None:
            return jsonify(jobs=[], available=False)
        try:
            return jsonify(jobs=read_history(Path(history), 200), available=True)
        except BackupError:
            return jsonify(jobs=[], available=False)

    def batch_files():
        from .staging import ReadyBatch
        entries = []
        inbox = state / "web-inbox"
        if inbox.is_symlink():
            raise BackupError("unsafe_staging_roots")
        if inbox.exists():
            with os.scandir(inbox) as batches:
                for index, entry in enumerate(batches):
                    if index >= 2000:
                        break
                    if not entry.is_dir(follow_symlinks=False) or not re.fullmatch(r"[0-9a-f]{32}\.ready", entry.name):
                        continue
                    try:
                        batch = ReadyBatch.load(Path(entry.path), verify_contents=False)
                        for item in batch.files:
                            entries.append({"batch": entry.name, "name": item.relative, "size": item.size,
                                            "sha256": item.sha256, "status": "staged", "cloud_verified": False,
                                            "created_at": entry.stat(follow_symlinks=False).st_mtime})
                    except BackupError:
                        continue
        return sorted(entries, key=lambda item: (item["created_at"], item["batch"], item["name"]))

    @app.get("/api/files")
    def files():
        try:
            return jsonify(files=batch_files(), source="completed_web_uploads", limit=2000)
        except (OSError, BackupError):
            abort(503)

    @app.post("/api/files")
    def upload():
        if current_user()["role"] != "owner":
            abort(403)
        incoming = request.files.get("file")
        if incoming is None or not incoming.filename:
            abort(400)
        try:
            batch = stage_upload(incoming.stream, incoming.filename, state / "web-inbox")
        except BackupError as exc:
            abort(413 if exc.code == "web_upload_too_large" else 400 if exc.code in
                  ("unsafe_relative_path", "invalid_web_upload") else 503)
        return jsonify(batch=batch.root.name, name=batch.files[0].relative, status="staged",
                       cloud_verified=False), 201

    def open_file():
        batch = request.args.get("batch", "")
        name = request.args.get("name", "")
        if not re.fullmatch(r"[0-9a-f]{32}\.ready", batch):
            abort(404)
        try:
            relative_path(name)
            if not any(entry["batch"] == batch and entry["name"] == name for entry in batch_files()):
                abort(404)
            manager = open_regular(state / "web-inbox", batch + "/files/" + name)
            stream = manager.__enter__()
            return manager, stream, name
        except (OSError, BackupError):
            abort(404)

    @app.get("/api/download")
    def download():
        manager, stream, name = open_file()
        try:
            response = send_file(stream, download_name=Path(name).name, as_attachment=True,
                                 mimetype="application/octet-stream", conditional=False)
            # Keep the response's close callbacks alive through WSGI iteration.
            response.direct_passthrough = False
            response.content_length = os.fstat(stream.fileno()).st_size
            response.call_on_close(lambda: manager.__exit__(None, None, None))
            return response
        except BaseException:
            manager.__exit__(None, None, None)
            raise

    @app.get("/api/preview")
    def preview():
        manager, stream, name = open_file()
        try:
            data = stream.read(65537)
            if "\0" in data.decode("utf-8", errors="replace"):
                return jsonify(supported=False, name=name)
            return jsonify(supported=True, text=data[:65536].decode("utf-8", errors="replace"),
                           truncated=len(data) > 65536, name=name)
        finally:
            manager.__exit__(None, None, None)

    @app.get("/manifest.webmanifest")
    def manifest():
        return jsonify(name="Raspi Cloud Sync Pro", short_name="Raspi Sync", start_url="/",
                       display="standalone", background_color="#f4f7fb", theme_color="#172c45",
                       icons=[{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}])

    @app.get("/sw.js")
    def service_worker():
        return app.send_static_file("sw.js")

    return app


def serve_dashboard(state, *, history=None, worker_state=None, port=8080, public_origin=None):
    from waitress import serve
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Use an unprivileged dashboard port")
    # Always loopback. A reviewed HTTPS reverse proxy supplies external access.
    app = create_app(state, history=history, worker_state=worker_state, public_origin=public_origin)
    serve(app, host="127.0.0.1", port=port, threads=2, connection_limit=32,
          channel_timeout=30, max_request_body_size=102 * 1024 * 1024,
          clear_untrusted_proxy_headers=True)
