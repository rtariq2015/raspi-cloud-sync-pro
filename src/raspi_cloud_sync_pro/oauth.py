"""Owner-bound OAuth authorization foundation. Does not configure or start backups."""

import base64
import hashlib
import json
import math
import os
import queue
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .accounts import _private_state
from .config import _unique_object
from .errors import BackupError
from .staging import _fsync_directory, open_regular


PROVIDERS = {
    "google_drive": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/drive.file",
        "extra": {"access_type": "offline", "prompt": "consent"},
    },
    "dropbox": {
        "authorize": "https://www.dropbox.com/oauth2/authorize",
        "token": "https://api.dropboxapi.com/oauth2/token",
        "scope": "files.content.read files.content.write files.metadata.read",
        "extra": {"token_access_type": "offline"},
    },
}


class OAuthError(BackupError):
    """Only fixed codes; never include a request, response, client secret or code."""


def _string(value, limit=8192):
    return isinstance(value, str) and 0 < len(value) <= limit and value.isascii() \
        and all(32 <= ord(char) < 127 for char in value)


def _read_private(root, filename, limit=65536):
    root = _private_state(root)
    with open_regular(root, filename) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError()
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError()
    return json.loads(raw, object_pairs_hook=_unique_object)


def _client(root, origin, provider):
    if provider not in PROVIDERS:
        raise OAuthError("oauth_provider_unavailable")
    parsed = urllib.parse.urlsplit(origin or "")
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/") \
            or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OAuthError("oauth_https_setup_required")
    try:
        data = _read_private(root, "oauth-clients.json", 32768)
        if not isinstance(data, dict) or set(data) != {"schema_version", "public_origin", "providers"} \
                or type(data["schema_version"]) is not int or data["schema_version"] != 1 \
                or data["public_origin"] != origin.rstrip("/") or not isinstance(data["providers"], dict) \
                or set(data["providers"]) - set(PROVIDERS):
            raise ValueError()
        client = data["providers"][provider]
        if not isinstance(client, dict) or set(client) != {"client_id", "client_secret"} \
                or not _string(client["client_id"], 4096) or not _string(client["client_secret"], 4096):
            raise ValueError()
        return dict(client)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, BackupError):
        raise OAuthError("oauth_client_setup_required") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exchange_code(provider, fields):
    """Fixed TLS token endpoints, bounded response, no redirects/proxy inheritance/logs."""
    if provider not in PROVIDERS:
        raise OAuthError("oauth_provider_unavailable")
    body = urllib.parse.urlencode(fields).encode("ascii")
    if len(body) > 65536:
        raise OAuthError("oauth_exchange_failed")
    request = urllib.request.Request(PROVIDERS[provider]["token"], data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                                     method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise ValueError()
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, urllib.error.URLError):
        raise OAuthError("oauth_exchange_failed") from None


@dataclass(frozen=True)
class OwnerProof:
    username: str
    session_id: str
    credential_tag: str


class OAuthManager:
    def __init__(self, state, origin, *, transport=None, clock=time.monotonic, exchange_timeout=20):
        self.state = Path(state)
        self.origin = (origin or "").rstrip("/")
        self.transport = exchange_code if transport is None else transport
        self.clock = clock
        self.flows, self.handoffs = {}, {}
        self.lock = threading.Lock()
        self.exchange_slots = threading.BoundedSemaphore(2)
        self.exchange_timeout = exchange_timeout

    def _purge(self):
        now = self.clock()
        for collection in (self.flows, self.handoffs):
            for key in list(collection):
                if collection[key]["expires"] <= now:
                    del collection[key]

    def readiness(self):
        result = []
        for provider in PROVIDERS:
            try:
                _client(self.state, self.origin, provider)
                ready = True
                reason = None
            except OAuthError as error:
                ready, reason = False, error.code
            saved = self._authorization_saved(provider)
            result.append({"provider": provider, "client_ready": ready, "setup_required": reason,
                           "authorization": "authorization_saved_untested" if saved else "not_authorized", "refresh_available": saved,
                           "folder_selected": False, "test_verified": False, "uploads_enabled": False})
        return result

    def _authorization_saved(self, provider):
        try:
            data = _read_private(self.state, "oauth-" + provider + ".json")
            client = _client(self.state, self.origin, provider)
            return isinstance(data, dict) and set(data) == {
                "schema_version", "provider", "client_id_sha256", "received_at", "expires_at", "token_type",
                "access_token", "refresh_token", "scopes"} and type(data["schema_version"]) is int and data["schema_version"] == 1 \
                and data["provider"] == provider and _string(data["access_token"], 32768) \
                and _string(data["refresh_token"], 16384) and data["token_type"] == "Bearer" \
                and data["client_id_sha256"] == hashlib.sha256(client["client_id"].encode()).hexdigest() \
                and type(data["received_at"]) in (int, float) and type(data["expires_at"]) in (int, float) \
                and math.isfinite(data["received_at"]) and math.isfinite(data["expires_at"]) \
                and 0 <= data["received_at"] < data["expires_at"] \
                and isinstance(data["scopes"], list) and all(isinstance(item, str) for item in data["scopes"]) \
                and set(data["scopes"]) == set(PROVIDERS[provider]["scope"].split())
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError, BackupError):
            return False

    def start(self, provider, proof):
        client = _client(self.state, self.origin, provider)
        destination = self.state / ("oauth-" + provider + ".json")
        if destination.exists() or destination.is_symlink():
            raise OAuthError("oauth_authorization_already_saved")
        verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        redirect = self.origin + "/oauth/callback/" + provider
        with self.lock:
            self._purge()
            if len(self.flows) + len(self.handoffs) >= 16:
                raise OAuthError("oauth_try_later")
            self.flows[state] = {"provider": provider, "proof": proof, "client": client,
                                 "verifier": verifier, "redirect_uri": redirect, "expires": self.clock() + 300}
        query = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
                 "scope": PROVIDERS[provider]["scope"], "state": state, "code_challenge": challenge,
                 "code_challenge_method": "S256", **PROVIDERS[provider]["extra"]}
        return {"authorization_url": PROVIDERS[provider]["authorize"] + "?" + urllib.parse.urlencode(query),
                "expires_in": 300, "uploads_enabled": False}

    def callback(self, provider, state, code=None, error=None):
        if not _string(state, 128):
            raise OAuthError("oauth_return_invalid")
        with self.lock:
            self._purge()
            flow = self.flows.pop(state, None)
            if flow is None or flow["provider"] != provider:
                raise OAuthError("oauth_return_invalid")
            if bool(code) == bool(error) or (code is not None and not _string(code, 8192)) \
                    or (error is not None and not _string(error, 256)):
                raise OAuthError("oauth_return_invalid")
            ticket = secrets.token_urlsafe(32)
            self.handoffs[ticket] = {"flow": flow, "code": code,
                                     "declined": bool(error), "expires": min(flow["expires"], self.clock() + 90)}
        return ticket

    def complete(self, ticket, proof, guard):
        if not _string(ticket, 128):
            raise OAuthError("oauth_return_invalid")
        with self.lock:
            self._purge()
            handoff = self.handoffs.pop(ticket, None)
        if handoff is None or handoff["flow"]["proof"] != proof:
            raise OAuthError("oauth_return_invalid")
        if handoff["declined"]:
            raise OAuthError("oauth_authorization_declined")
        return self._complete(handoff, proof, guard)

    def _exchange(self, provider, fields):
        # DNS and a dribbling TLS response can outlast an individual socket
        # timeout. Bound the caller's total wait as well. A timed-out transport
        # retains its slot until it exits, so at most two threads can be stuck;
        # late results never publish credentials or authorize uploads.
        if not self.exchange_slots.acquire(blocking=False):
            raise OAuthError("oauth_try_later")
        result = queue.Queue(maxsize=1)

        def run():
            try:
                result.put((True, self.transport(provider, fields)))
            except Exception:
                result.put((False, None))
            finally:
                self.exchange_slots.release()

        worker = threading.Thread(target=run, daemon=True, name="raspi-oauth-exchange")
        try:
            worker.start()
        except RuntimeError:
            self.exchange_slots.release()
            raise OAuthError("oauth_exchange_failed") from None
        try:
            okay, response = result.get(timeout=self.exchange_timeout)
        except queue.Empty:
            raise OAuthError("oauth_exchange_failed") from None
        if not okay:
            raise OAuthError("oauth_exchange_failed")
        return response

    def _complete(self, handoff, proof, guard):
        flow = handoff["flow"]
        provider = flow["provider"]
        with guard(proof):
            if _client(self.state, self.origin, provider) != flow["client"]:
                raise OAuthError("oauth_client_setup_required")
        fields = {**flow["client"], "grant_type": "authorization_code", "code": handoff["code"],
                  "redirect_uri": flow["redirect_uri"], "code_verifier": flow["verifier"]}
        tokens = self._exchange(provider, fields)
        record = self._token_record(provider, flow["client"], tokens)
        with guard(proof):
            if _client(self.state, self.origin, provider) != flow["client"]:
                raise OAuthError("oauth_client_setup_required")
            self._store(provider, record)
        return {"provider": provider, "authorization": "authorization_saved_untested", "refresh_available": True, "folder_selected": False,
                "test_verified": False, "uploads_enabled": False}

    def _token_record(self, provider, client, tokens):
        if not isinstance(tokens, dict) or not _string(tokens.get("access_token"), 32768) \
                or not _string(tokens.get("refresh_token"), 16384) \
                or not isinstance(tokens.get("token_type"), str) or tokens["token_type"].lower() != "bearer" \
                or type(tokens.get("expires_in")) not in (int, float):
            raise OAuthError("oauth_offline_authorization_incomplete")
        expiry = tokens["expires_in"]
        try:
            valid_expiry = math.isfinite(expiry) and 0 < expiry <= 31622400
        except OverflowError:
            valid_expiry = False
        if not valid_expiry:
            raise OAuthError("oauth_offline_authorization_incomplete")
        scopes = tokens.get("scope", "")
        if not isinstance(scopes, str) or len(scopes) > 4096 \
                or set(scopes.split()) != set(PROVIDERS[provider]["scope"].split()):
            raise OAuthError("oauth_scope_not_granted")
        now = time.time()
        return {"schema_version": 1, "provider": provider,
                "client_id_sha256": hashlib.sha256(client["client_id"].encode()).hexdigest(),
                "received_at": now, "expires_at": now + expiry, "token_type": "Bearer",
                "access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"], "scopes": scopes.split()}

    def _store(self, provider, record):
        root = _private_state(self.state)
        destination = root / ("oauth-" + provider + ".json")
        # Do not silently replace an existing authorization without a separately
        # designed reconnect/revoke workflow.
        if destination.exists() or destination.is_symlink():
            raise OAuthError("oauth_authorization_already_saved")
        encoded = json.dumps(record).encode("utf-8")
        if len(encoded) > 65536:
            raise OAuthError("oauth_private_storage_unavailable")
        temporary = root / ("oauth-" + secrets.token_hex(16) + ".tmp")
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link publication refuses replacement even across concurrent apps.
            os.link(temporary, destination, follow_symlinks=False)
            _fsync_directory(root)
        except (OSError, ValueError):
            raise OAuthError("oauth_private_storage_unavailable") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # A retained private temporary credential file must not cause
                # raw filesystem/provider details to escape to the browser.
                pass
