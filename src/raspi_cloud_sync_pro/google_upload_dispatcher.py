"""One selected, already-transferred Google batch; disabled by default.

The trusted caller must separately establish the canonical manifest digest and,
for web transfers, final frame/EOF and BOTH child exits before calling this API.
This module transfers nothing, schedules nothing, and publishes no browser state.
Root reads selected manifest metadata, never payloads or Google credentials.
Every release needs fresh ordinary-source admission and actual live confinement;
no saved QA report authorizes anything. Source Disable denies the next admission;
an already admitted batch can finish. Kernel I/O cannot be given a hard wall bound.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import selectors
import secrets
import socket
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import batch_transfer as batches
from . import google_broker as preparation
from . import google_control as control
from . import google_destination as destination
from . import google_operational as operational
from . import google_test_broker as trusted
from . import google_upload as upload
from .config import _unique_object
from .errors import BackupError

_APPROVAL = {"schema_version": 1, "google_upload_dispatcher_approved": True}
_FIELDS = set(trusted._FIELDS) | {"staging"}
_PATHS = trusted._PATHS + ("staging",)
_COUNTS = ("files_verified_now", "files_previously_verified", "files_pending", "files_failed")
_RECEIPT_FIELDS = {"schema_version", "provider", "batch_state", "batch_id", "manifest_sha256",
                   "control_request_id", "preparation", "test", "destination_id", "completed_at",
                   *_COUNTS, "automatic_deletion"}
_TIMEOUT = 420.0
_WORKER_TIMEOUT = 300
_RUNTIME_TIMEOUT = 390


class GoogleUploadDispatcherError(BackupError):
    """Fixed codes only, with no private data or exception context."""


def _fail(code):
    raise GoogleUploadDispatcherError(code) from None


def _result(status, code, receipt=None):
    result = {"status": status, "code": code, "uploads_enabled": False,
              "current_worker_state_checked": False, "account_verified": False, "automatic_deletion": False}
    result.update({name: receipt[name] if receipt is not None else 0 for name in _COUNTS})
    return result


@dataclass(frozen=True, repr=False)
class GoogleUploadDispatcherSettings(trusted.GoogleTestBrokerSettings):
    staging: Path = None

    def __repr__(self):
        return "<GoogleUploadDispatcherSettings protected>"

    @classmethod
    def load(cls, root_config_path):
        try:
            path = trusted._path(root_config_path)
            data, identity = preparation._root_json(path, private=True)
            if (not isinstance(data, dict) or set(data) - _FIELDS
                    or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                    or type(data.get("enabled", False)) is not bool):
                _fail("google_upload_dispatcher_configuration_invalid")
            if not data.get("enabled", False):
                return cls(config_path=path, _identity=identity)
            if (set(data) != _FIELDS or data["backup_user"] != "raspi-backup"
                    or type(data["allow_metered"]) is not bool
                    or not isinstance(data["wifi_profile_uuid"], str)
                    or trusted._UUID.fullmatch(data["wifi_profile_uuid"]) is None
                    or not control._hash(data["rclone_sha256"])):
                _fail("google_upload_dispatcher_configuration_invalid")
            values = {name: trusted._path(data[name]) for name in _PATHS}
            roots = [values[name] for name in ("source_state", "backup_state", "operational_state", "staging")]
            if any(a == b or a in b.parents or b in a.parents for index, a in enumerate(roots) for b in roots[index + 1:]):
                _fail("google_upload_dispatcher_configuration_invalid")
            if (any(not isinstance(data[name], str) or preparation._USER.fullmatch(data[name]) is None
                    for name in ("dashboard_user", "backup_user")) or data["dashboard_user"] == data["backup_user"]):
                _fail("google_upload_dispatcher_configuration_invalid")
            origin = destination._origin(data["public_origin"])
            dns = ipaddress.IPv4Address(data["dns_ipv4"])
            if (origin != data["public_origin"] or str(dns) != data["dns_ipv4"]
                    or dns.is_loopback or dns.is_unspecified or dns.is_multicast or dns.is_link_local
                    or values["lock_file"].parent != values["runtime_directory"]):
                _fail("google_upload_dispatcher_configuration_invalid")
            for name in ("dashboard_user", "backup_user", "rclone_sha256", "wifi_profile_uuid", "allow_metered"):
                values[name] = data[name]
            return cls(enabled=True, config_path=path, _identity=identity, public_origin=origin,
                       dns_ipv4=str(dns), **values)
        except GoogleUploadDispatcherError:
            raise
        except Exception:
            _fail("google_upload_dispatcher_configuration_invalid")


def _configuration_current(settings):
    if settings.config_path is None or GoogleUploadDispatcherSettings.load(settings.config_path) != settings:
        _fail("google_upload_dispatcher_configuration_changed")
    marker, _ = preparation._root_json(settings.approval_file, private=True)
    if (marker != _APPROVAL or type(marker.get("schema_version")) is not int
            or marker.get("google_upload_dispatcher_approved") is not True):
        _fail("google_upload_dispatcher_approval_required")


def _gates(settings):
    preparation._platform_gate()
    if threading.active_count() != 1:
        _fail("google_upload_dispatcher_single_thread_required")
    if os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"):
        _fail("google_upload_dispatcher_namespace_invalid")
    _configuration_current(settings)
    trusted._python_environment(settings)
    preparation._installed_package_gate()
    for executable in (settings.interpreter, settings.rclone, Path("/usr/bin/systemd-run"),
                       Path("/usr/bin/systemctl"), Path("/usr/bin/nmcli"), Path("/usr/bin/ip")):
        with preparation._root_file(executable, executable=True) as (fd, info):
            if stat.S_IMODE(info.st_mode) != 0o755:
                _fail("google_upload_dispatcher_installation_unsafe")
            if executable == settings.rclone:
                digest = hashlib.sha256()
                if not 0 < info.st_size <= 128 * 1024 * 1024:
                    _fail("google_upload_dispatcher_installation_unsafe")
                while True:
                    block = os.read(fd, 65536)
                    if not block:
                        break
                    digest.update(block)
                if (digest.hexdigest() != settings.rclone_sha256
                        or destination._file_identity(os.fstat(fd)) != destination._file_identity(info)):
                    _fail("google_upload_dispatcher_installation_unsafe")
    code, raw = trusted._command(["/usr/bin/systemctl", "--version"], time.monotonic() + 3, limit=4096)
    if code or re.fullmatch(rb"systemd 257 \(257\.13[^\n]*\)", raw.splitlines()[0]) is None:
        _fail("google_upload_dispatcher_manager_unapproved")
    if not hasattr(os, "pidfd_open"):
        _fail("google_upload_dispatcher_pidfd_required")


class _Source:
    def __init__(self, settings, account):
        self.settings, self.account = settings, account

    def admission(self, deadline):
        trusted._remaining(deadline)
        incoming, outgoing = preparation._private_pipe()
        child = None
        try:
            child = preparation._spawn(self.settings, self.account,
                ["google-control-admission", "--state", str(self.settings.source_state),
                 "--public-origin", self.settings.public_origin, "--output-fd", str(outgoing)], (outgoing,))
            os.close(outgoing)
            outgoing = None
            owned, incoming = incoming, None
            value = preparation._receive(owned, time.monotonic() + trusted._remaining(deadline), 16384)
            if preparation._wait_child(child, time.monotonic() + trusted._remaining(deadline)):
                _fail("google_upload_dispatcher_source_refused")
            return value
        finally:
            for fd in (incoming, outgoing):
                if fd is not None:
                    os.close(fd)
            if child is not None:
                preparation._stop_children([child])


def _admission(value):
    """Snapshot and validate actual wall-time once, at this observation."""
    try:
        raw = destination._canonical(value)
        if len(raw) > 16384:
            _fail("google_upload_dispatcher_admission_invalid")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (isinstance(value, dict) and set(value) == upload._ADMISSION_FIELDS
                and control.setup._valid_header(value) and value["admission_authorized"] is False
                and value["single_admission"] is True and value["control_request_id"] is None
                and value["preparation"] is None and value["test"] is None
                and control.setup._timestamp(value["checked_at"])
                and value["checked_at"] <= time.time() < value["checked_at"] + 30):
            return None
        return upload._admission(value, time.time())[0]
    except GoogleUploadDispatcherError:
        raise
    except Exception:
        _fail("google_upload_dispatcher_admission_invalid")


def _stable(value):
    return destination._canonical({key: item for key, item in value.items() if key != "checked_at"})


@contextmanager
def _selected_batch(settings, batch_id, expected_digest, staging_fd, owner):
    """Root reads private metadata only; worker independently checks every byte."""
    batch = os.open(batch_id + ".ready", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=staging_fd)
    try:
        preparation._directory_info(batch, owner, private=True)
        identity = os.fstat(batch).st_dev, os.fstat(batch).st_ino
        raw = preparation._read_private_bytes(batch, "manifest.json", owner, batches.MAX_METADATA_BYTES)
        manifest, _, digest, total = batches._manifest(json.loads(raw, object_pairs_hook=_unique_object))
        if digest != expected_digest or not manifest["files"]:
            _fail("google_upload_dispatcher_batch_invalid")
        metadata = {"schema_version": 1, "batch_id": batch_id, "manifest_sha256": digest,
                    "file_count": len(manifest["files"]), "total_bytes": total}
        def recheck():
            with preparation._state_directory(settings.staging, owner) as named:
                before, after = os.fstat(staging_fd), os.fstat(named)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    _fail("google_upload_dispatcher_batch_changed")
            current = os.stat(batch_id + ".ready", dir_fd=staging_fd, follow_symlinks=False)
            if ((current.st_dev, current.st_ino) != identity or not stat.S_ISDIR(current.st_mode)
                    or current.st_uid != owner or stat.S_IMODE(current.st_mode) != 0o700
                    or preparation._read_private_bytes(batch, "manifest.json", owner, batches.MAX_METADATA_BYTES) != raw):
                _fail("google_upload_dispatcher_batch_changed")
        recheck()
        yield metadata, recheck
    finally:
        os.close(batch)


def _argv(settings, admission, batch_id, manifest_sha256):
    return [str(settings.interpreter), "-I", "-B", "-m", "raspi_cloud_sync_pro.google_upload_executor",
            "--prepared-state", str(settings.backup_state / admission["preparation"]["request_id"]),
            "--operational-state", str(settings.operational_state), "--staging", str(settings.staging),
            "--batch-id", batch_id, "--manifest-sha256", manifest_sha256,
            "--control-request-id", admission["control_request_id"], "--rclone", str(settings.rclone),
            "--rclone-sha256", settings.rclone_sha256, "--timeout", str(_WORKER_TIMEOUT)]


def _properties(settings, backup, path, resolver):
    values = trusted._unit_properties(settings, backup, path, resolver)
    values.update(RuntimeMaxSec=str(_RUNTIME_TIMEOUT), ReadOnlyPaths=str(settings.backup_state) + " " + str(settings.staging))
    return values


class _Manager(trusted._Systemd):
    def __init__(self, settings, backup, deadline, staging_identity):
        super().__init__(settings, backup, deadline)
        self.staging_identity = staging_identity

    def _staging(self, pid):
        path = "/proc/" + str(pid) + "/root" + str(self.settings.staging)
        info = os.stat(path, follow_symlinks=False)
        if ((info.st_dev, info.st_ino) != self.staging_identity or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != self.backup.pw_uid or stat.S_IMODE(info.st_mode) != 0o700
                or not os.statvfs(path).f_flag & os.ST_RDONLY):
            _fail("google_upload_dispatcher_staging_mount_invalid")

    def authenticate(self, unit, description, invocation, arguments):
        worker = super().authenticate(unit, description, invocation, arguments)
        try:
            self._staging(worker.pid)
            return worker
        except BaseException:
            worker.close()
            raise

    def recheck(self, unit, description, invocation, worker, arguments):
        super().recheck(unit, description, invocation, worker, arguments)
        self._staging(worker.pid)

    def wait_exit(self, unit, description, invocation, worker, deadline, expected_code):
        if expected_code not in (0, 2):
            _fail("google_upload_dispatcher_worker_failed")
        with selectors.DefaultSelector() as selector:
            selector.register(worker.pidfd, selectors.EVENT_READ)
            if not selector.select(trusted._remaining(deadline, 10)):
                _fail("google_upload_dispatcher_timeout")
        value = self.show(unit)
        if (self.capture(unit, description) != invocation or value.get("MainPID") != "0"
                or value.get("ExecMainCode") != "1" or value.get("ExecMainStatus") != str(expected_code)
                or value.get("Result") != ("success" if expected_code == 0 else "exit-code")
                or trusted._cgroup_pids(worker.control_group, missing=True)):
            _fail("google_upload_dispatcher_worker_failed")


def _receipt(value, admission, metadata, released_at):
    """Validate private evidence before completion ACK, never infer global enable."""
    if (not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS or not control.setup._valid_header(value)
            or value["batch_id"] != metadata["batch_id"] or value["manifest_sha256"] != metadata["manifest_sha256"]
            or value["control_request_id"] != admission["control_request_id"]
            or any(destination._canonical(value[key]) != destination._canonical(admission[key]) for key in ("preparation", "test"))
            or value["automatic_deletion"] is not False
            or not control.setup._timestamp(value["completed_at"])
            or not released_at <= value["completed_at"] <= time.time()
            or any(type(value[name]) is not int or not 0 <= value[name] <= batches.MAX_FILES for name in _COUNTS)
            or sum(value[name] for name in _COUNTS) != metadata["file_count"]):
        _fail("google_upload_dispatcher_receipt_invalid")
    prepared = admission["preparation"]
    binding = operational._binding(prepared["request_id"], prepared["folder_name"],
                                   prepared["authorization_sha256"], prepared["rclone_config_sha256"])
    if value["destination_id"] != operational._destination_id(admission["test"]["generation_id"], binding):
        _fail("google_upload_dispatcher_receipt_invalid")
    state = value["batch_state"]
    if ((state == "verified" and (not value["files_verified_now"] or value["files_pending"] or value["files_failed"]))
            or (state == "historical" and (value["files_verified_now"] or not value["files_previously_verified"] or value["files_pending"] or value["files_failed"]))
            or (state == "pending" and (not value["files_pending"] or value["files_failed"]))
            or (state == "failed" and not value["files_failed"]) or state not in ("verified", "historical", "pending", "failed")):
        _fail("google_upload_dispatcher_receipt_invalid")
    return value, 0 if state in ("verified", "historical") else 2


def _release(peers, admission, metadata, worker, deadline):
    # Revalidate only the latest frame at actual release. The initial snapshot's
    # age is irrelevant after stable authority matches this freshly exported one.
    current = _admission(admission)
    if current is None:
        _fail("google_upload_dispatcher_authority_changed")
    released_at = time.time()
    destination._write_frame(peers["request"].fileno(), destination._canonical(current),
                             time.monotonic() + trusted._remaining(deadline))
    peers["request"].shutdown(socket.SHUT_WR)
    release = {"schema_version": 1, "provider": "google_drive", "purpose": upload._PURPOSE,
               "control_request_id": current["control_request_id"],
               "admission_sha256": hashlib.sha256(destination._canonical(current)).hexdigest(),
               "batch_id": metadata["batch_id"], "manifest_sha256": metadata["manifest_sha256"],
               "worker_pid": worker.pid, "expires_at": time.time() + 20}
    destination._write_frame(peers["gate"].fileno(), destination._canonical(release),
                             time.monotonic() + trusted._remaining(deadline))
    peers["gate"].shutdown(socket.SHUT_WR)
    return released_at


def _complete(peer, binding, metadata, deadline):
    value = {"schema_version": 1, "provider": "google_drive", "phase": "upload_complete",
             "worker_pid": binding["worker_pid"], "control_request_id": binding["request_id"],
             "batch_id": metadata["batch_id"], "manifest_sha256": metadata["manifest_sha256"], "nonce": binding["nonce"]}
    destination._write_frame(peer.fileno(), destination._canonical(value), time.monotonic() + trusted._remaining(deadline))
    peer.shutdown(socket.SHUT_WR)


def _dispatch(settings, backup, metadata, batch_recheck, deadline, manager, source_transport):
    admission = _admission(source_transport.admission(deadline))
    if admission is None:
        return _result("denied", "google_upload_dispatcher_admission_denied")
    network = trusted._network(settings, deadline)
    with trusted._runtime(settings) as (path, directory, identifier):
        unit = "raspi-cloud-google-test-" + identifier + ".service"  # Private inspector/bootstrap ABI only.
        description = "Private Google selected-batch upload " + identifier
        resolver = path / "resolv.conf"
        trusted._write_private(directory, "resolv.conf", ("nameserver " + settings.dns_ipv4 + "\noptions timeout:2 attempts:1\n").encode("ascii"), mode=0o644)
        arguments = _argv(settings, admission, metadata["batch_id"], metadata["manifest_sha256"])
        properties = _properties(settings, backup, path, resolver)
        trusted._write_private(directory, "plan.json", destination._canonical({"schema_version": 1, "unit": unit,
            "description": description, "arguments": arguments, "properties": properties, "selected_batch": metadata,
            "cleanup": "stop only matching description and captured InvocationID; retain all state"}))
        invocation, worker = None, None
        started, cleanup_ok = False, True
        peers = {}
        result = _result("failed", "google_upload_dispatcher_unavailable")
        try:
            with trusted._listeners(path) as listeners, trusted._Loopback(secrets.token_hex(16)) as loopback:
                loopback.positive()
                started = True
                manager.start(unit, description, properties, arguments)
                invocation = manager.capture(unit, description)
                peers = trusted._accept(listeners, min(deadline, time.monotonic() + 10))
                worker = manager.authenticate(unit, description, invocation, arguments)
                canary_deadline = min(deadline, time.monotonic() + trusted._CANARY_TIMEOUT)
                trusted._canary_ready(peers["canary"], worker, canary_deadline)
                from .wifi_confinement import hold_wifi_confinement
                with hold_wifi_confinement(worker.control_group, network["interface_index"]) as proof:
                    proof.recheck()
                    manager.recheck(unit, description, invocation, worker, arguments)
                    binding = {"schema_version": 1, "provider": "google_drive", "worker_pid": worker.pid,
                               "request_id": admission["control_request_id"], "nonce": loopback.nonce.decode("ascii"),
                               "dns_ipv4": settings.dns_ipv4, "loopback_port": loopback.port}
                    trusted._canary(peers["canary"], binding, loopback, canary_deadline)
                    proof.recheck()
                    loopback.positive()
                    latest = _admission(source_transport.admission(deadline))
                    if (latest is None or not hmac.compare_digest(_stable(latest), _stable(admission))
                            or trusted._network(settings, deadline) != network):
                        _fail("google_upload_dispatcher_authority_changed")
                    _configuration_current(settings)
                    batch_recheck()
                    proof.recheck()
                    manager.recheck(unit, description, invocation, worker, arguments)
                    trusted._ack(peers["canary"], binding, canary_deadline)
                    released_at = _release(peers, latest, metadata, worker, deadline)
                    received = preparation._receive(os.dup(peers["receipt"].fileno()),
                                                    min(deadline, time.monotonic() + _WORKER_TIMEOUT + 10), 65536)
                    receipt, expected_code = _receipt(received, latest, metadata, released_at)
                    # Persist even a valid pending/failed receipt before any
                    # later proof/cleanup failure; it is private historical data.
                    trusted._write_private(directory, "receipt.json", destination._canonical(receipt))
                    loopback.assert_quiet()
                    loopback.positive()
                    _configuration_current(settings)
                    batch_recheck()
                    if trusted._network(settings, deadline) != network:
                        _fail("google_upload_dispatcher_network_changed")
                    manager.complete_recheck(unit, description, invocation, worker, arguments)
                    proof.recheck()
                    _complete(peers["receipt"], binding, metadata, deadline)
                    manager.wait_exit(unit, description, invocation, worker, deadline, expected_code)
                    result = _result("completed", "google_upload_dispatcher_batch_" + receipt["batch_state"], receipt)
        except GoogleUploadDispatcherError as error:
            result = _result("failed", error.code)
        except Exception:
            result = _result("failed", "google_upload_dispatcher_unavailable")
        finally:
            for peer in peers.values():
                peer.close()
            if worker is not None:
                worker.close()
            if started:
                try:
                    manager.stop(unit, description, invocation)
                except Exception:
                    cleanup_ok = False
            if not cleanup_ok:
                result = _result("failed", "google_upload_dispatcher_cleanup_unconfirmed")
            trusted._write_private(directory, "result.json", destination._canonical(result))
        return result


def run_google_upload_dispatcher(settings, batch_id, expected_manifest_sha256):
    """One explicit selected batch. No public proof/clock/native bypass inputs."""
    if not isinstance(settings, GoogleUploadDispatcherSettings):
        return _result("failed", "google_upload_dispatcher_configuration_invalid")
    if not settings.enabled:
        return _result("disabled", "google_upload_dispatcher_disabled")
    try:
        if not control._id(batch_id) or not control._hash(expected_manifest_sha256):
            _fail("google_upload_dispatcher_batch_invalid")
        _gates(settings)
        source = preparation._service_identity(settings.dashboard_user)
        backup = preparation._service_identity(settings.backup_user)
        if source.pw_uid == backup.pw_uid or source.pw_gid == backup.pw_gid:
            _fail("google_upload_dispatcher_identity_unsafe")
        deadline = time.monotonic() + _TIMEOUT
        with preparation._state_directory(settings.source_state, source.pw_uid), \
                preparation._state_directory(settings.backup_state, backup.pw_uid), \
                preparation._state_directory(settings.operational_state, backup.pw_uid), \
                preparation._state_directory(settings.staging, backup.pw_uid) as staging, \
                preparation._root_directory(settings.runtime_directory) as runtime:
            preparation._directory_info(runtime, 0, private=True)
            with preparation._broker_lock(settings.lock_file), \
                    _selected_batch(settings, batch_id, expected_manifest_sha256, staging, backup.pw_uid) as (metadata, recheck):
                info = os.fstat(staging)
                identity = info.st_dev, info.st_ino
                return _dispatch(settings, backup, metadata, recheck, deadline,
                                 _Manager(settings, backup, deadline, identity), _Source(settings, source))
    except GoogleUploadDispatcherError as error:
        return _result("failed", error.code)
    except Exception:
        return _result("failed", "google_upload_dispatcher_unavailable")
