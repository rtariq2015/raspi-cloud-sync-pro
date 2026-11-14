"""Transfer one selected completed web batch, then dispatch it.

Disabled settings refuse before state access. Root opens directory anchors and
bounded private metadata only; ordinary source/backup children stream payloads
directly. Transfer completion/EOF and BOTH zero exits precede the unchanged
guarded upload dispatcher. No scheduler, owner intent, native/proof/clock bypass,
provider implementation, Google credential access, file deletion or permission repair.
Kernel filesystem operations have no hard wall-clock bound.
"""

import os
import signal
import stat
import time
from contextlib import contextmanager

from . import batch_transfer as batches
from . import google_broker as preparation
from . import google_destination as private
from . import google_upload_dispatcher as dispatcher
from .errors import BackupError

_TRANSFER_SECONDS = 300.0
_DESCRIBE_SECONDS = 5.0
_DISPATCH_SECONDS = dispatcher._TIMEOUT  # Fresh internal 420-second budget.
_CLEANUP_SECONDS = 5.0
_ACCOUNTED_SECONDS = _TRANSFER_SECONDS + _DISPATCH_SECONDS + _CLEANUP_SECONDS
_COUNTS = ("files_verified_now", "files_previously_verified", "files_pending", "files_failed")
_FAILURE_CODES = frozenset(("google_web_upload_transfer_timeout", "google_web_upload_description_invalid",
    "google_web_upload_source_changed", "google_web_upload_describe_failed", "google_web_upload_transfer_failed",
    "google_web_upload_cleanup_unconfirmed", "google_web_upload_transfer_unavailable",
    "google_web_upload_dispatch_result_invalid", "google_web_upload_lock_conflict", "google_web_upload_identity_unsafe"))


class GoogleWebUploadPipelineError(BackupError):
    """Only fixed codes. Never attach paths, batch IDs, hashes or raw errors."""


def _fail(code):
    raise GoogleWebUploadPipelineError(code) from None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("google_web_upload_transfer_timeout")
    return remaining


def _report(status, code, counts=None):
    result = {"status": status, "code": code, "uploads_enabled": False,
              "current_worker_state_checked": False, "account_verified": False,
              "automatic_deletion": False}
    result.update({key: 0 if counts is None else counts[key] for key in _COUNTS})
    return result


def _failure_report(error):
    code = error.code if error.code in _FAILURE_CODES else "google_web_upload_unavailable"
    return _report("failed", code)


def _metadata(value, batch_id):
    if (not isinstance(value, dict) or set(value) != {
            "schema_version", "batch_id", "manifest_sha256", "file_count", "total_bytes"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["batch_id"] != batch_id or not dispatcher.control._hash(value["manifest_sha256"])
            or type(value["file_count"]) is not int or not 1 <= value["file_count"] <= batches.MAX_FILES
            or type(value["total_bytes"]) is not int or not 0 <= value["total_bytes"] <= batches.MAX_TOTAL_BYTES):
        _fail("google_web_upload_description_invalid")
    return dict(value)


def _same_web_anchor(source_fd, web_fd, owner):
    before = preparation._directory_info(web_fd, owner, private=True)
    after = os.stat("web-inbox", dir_fd=source_fd, follow_symlinks=False)
    if (not stat.S_ISDIR(after.st_mode) or after.st_uid != owner or stat.S_IMODE(after.st_mode) != 0o700
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
        _fail("google_web_upload_source_changed")


@contextmanager
def _web_anchor(source_fd, owner):
    # _state_directory(source/web-inbox) would incorrectly require the
    # dashboard-owned source parent to be root-owned. Stay relative to the
    # already validated source root, and never accept a caller-supplied path.
    fd = os.open("web-inbox", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source_fd)
    try:
        _same_web_anchor(source_fd, fd, owner)
        yield fd
    finally:
        os.close(fd)


def _start(settings, account, arguments, fds, children, deadline):
    _remaining(deadline)  # Never launch after expiry.
    # Reserve tracking storage before Popen; assignment after launch allocates
    # no new list entry and cleanup can see the returned child immediately.
    slot = [None]
    children.append(slot)
    slot[0] = preparation._spawn(settings, account, arguments, tuple(fds))
    return slot[0]


def _cleanup(children):
    deadline = time.monotonic() + _CLEANUP_SECONDS
    complete = True
    for slot in reversed(children):
        child = slot[0]
        if child is None:
            continue
        try:
            # These protected batch CLIs spawn no descendants. Never signal an
            # already reaped PID/group that might have been reused while the
            # other stream child was running. A still-running child owns its
            # new tracked session until it is killed and reaped here.
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                complete = False
            else:
                child.wait(timeout=remaining)
        except Exception:
            complete = False
    return complete


def _bridge(settings, source, backup, source_fd, web_fd, staging_fd, batch_id, record_fd):
    deadline = time.monotonic() + _TRANSFER_SECONDS
    opened, children = set(), []
    metadata = None
    failure = None

    def pipe():
        ends = preparation._private_pipe()
        try:
            opened.update(ends)
            return ends
        except BaseException:
            for fd in ends:
                os.close(fd)
            raise

    def close(fd):
        opened.remove(fd)
        os.close(fd)

    try:
        _same_web_anchor(source_fd, web_fd, source.pw_uid)
        incoming, outgoing = pipe()
        describe_deadline = min(deadline, time.monotonic() + _DESCRIBE_SECONDS)
        describer = _start(settings, source, ["web-batch-describe", "--state", str(settings.source_state / "web-inbox"),
            "--batch-id", batch_id, "--root-fd", str(web_fd), "--output-fd", str(outgoing),
            "--timeout", str(_remaining(describe_deadline))], (web_fd, outgoing), children, describe_deadline)
        close(outgoing)
        opened.remove(incoming)  # _receive consumes the accepted private FD.
        value = preparation._receive(incoming, describe_deadline, 4096)
        if preparation._wait_child(describer, describe_deadline) != 0:
            _fail("google_web_upload_describe_failed")
        metadata = _metadata(value, batch_id)
        _remaining(deadline)
        dispatcher.trusted._write_private(record_fd, "description.json", private._canonical(metadata))
        _same_web_anchor(source_fd, web_fd, source.pw_uid)
        incoming, outgoing = pipe()
        common = ["--batch-id", batch_id, "--expected-manifest-sha256", metadata["manifest_sha256"]]
        importer = _start(settings, backup, ["web-batch-import", "--state", str(settings.staging),
            "--root-fd", str(staging_fd), "--input-fd", str(incoming), "--timeout", str(_remaining(deadline))] + common,
            (staging_fd, incoming), children, deadline)
        close(incoming)
        exporter = _start(settings, source, ["web-batch-export", "--state", str(settings.source_state / "web-inbox"),
            "--root-fd", str(web_fd), "--output-fd", str(outgoing), "--timeout", str(_remaining(deadline))] + common,
            (web_fd, outgoing), children, deadline)
        close(outgoing)
        # Importer zero proves final completion frame+EOF, independent stream
        # and disk hashes, durable publication and final configured-root check.
        # It cannot prove Source's later independent exit; BOTH must be zero.
        importer_code = preparation._wait_child(importer, deadline)
        exporter_code = preparation._wait_child(exporter, deadline)
        if importer_code != 0 or exporter_code != 0:
            _fail("google_web_upload_transfer_failed")
        _remaining(deadline)
    except Exception as error:
        failure = error if isinstance(error, GoogleWebUploadPipelineError) else None
        if failure is None:
            failure = GoogleWebUploadPipelineError("google_web_upload_transfer_unavailable")
    finally:
        close_ok = True
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                close_ok = False
        if not _cleanup(children) or not close_ok:
            failure = GoogleWebUploadPipelineError("google_web_upload_cleanup_unconfirmed")
    if failure is not None:
        raise failure from None
    _remaining(deadline)
    dispatcher.trusted._write_private(record_fd, "transfer.json", private._canonical({
        "schema_version": 1, "selected_batch": metadata, "final_frame_and_eof_verified_by_importer": True,
        "source_export_exit_zero": True, "backup_import_exit_zero": True,
        "source_retained": True, "incomplete_and_ready_data_retained": True}))
    _remaining(deadline)
    return metadata


def _dispatch_report(value, file_count):
    fields = {"status", "code", "uploads_enabled", "current_worker_state_checked", "account_verified",
              "automatic_deletion", *_COUNTS}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value["status"]) is not str or type(value["code"]) is not str
            or any(value[key] is not False for key in
                   ("uploads_enabled", "current_worker_state_checked", "account_verified", "automatic_deletion"))
            or any(type(value[key]) is not int or not 0 <= value[key] <= batches.MAX_FILES for key in _COUNTS)):
        _fail("google_web_upload_dispatch_result_invalid")
    status, code = value["status"], value["code"]
    if status == "completed":
        pairs = {"google_upload_dispatcher_batch_verified": "verified", "google_upload_dispatcher_batch_historical": "historical",
                 "google_upload_dispatcher_batch_pending": "pending", "google_upload_dispatcher_batch_failed": "failed"}
        if code not in pairs or sum(value[key] for key in _COUNTS) != file_count:
            _fail("google_web_upload_dispatch_result_invalid")
        state = pairs[code]
        if ((state == "verified" and (value["files_verified_now"] == 0 or value["files_pending"] or value["files_failed"]))
                or (state == "historical" and (value["files_verified_now"] or value["files_previously_verified"] == 0
                                               or value["files_pending"] or value["files_failed"]))
                or (state == "pending" and (value["files_pending"] == 0 or value["files_failed"]))
                or (state == "failed" and value["files_failed"] == 0)):
            _fail("google_web_upload_dispatch_result_invalid")
        return _report(state, "google_web_upload_batch_" + state, value)
    if any(value[key] for key in _COUNTS):
        _fail("google_web_upload_dispatch_result_invalid")
    if status == "denied" and code == "google_upload_dispatcher_admission_denied":
        return _report("staged", "google_web_upload_waiting_for_admission")
    if status == "disabled" and code == "google_upload_dispatcher_disabled":
        return _report("staged", "google_web_upload_dispatch_disabled")
    if status == "failed":
        # Do not forward arbitrary exception/code strings through this boundary.
        return _report("failed", "google_web_upload_dispatch_failed")
    _fail("google_web_upload_dispatch_result_invalid")


def run_google_web_upload_pipeline(settings, selected_batch_id):
    """One configured selected batch; no auto-selection, proof or clock input.

    Transfer has one 300-second work deadline. Only after both child exits and
    confirmed cleanup does the existing dispatcher create its fresh 420-second
    deadline and obtain its own fresh Source admission/live confinement proof.
    Root/gate/configuration checks remain mandatory; disabled is the default.
    """
    if not isinstance(settings, dispatcher.GoogleUploadDispatcherSettings):
        return _report("failed", "google_web_upload_configuration_invalid")
    if not settings.enabled:
        return _report("disabled", "google_web_upload_disabled")
    if not dispatcher.control._id(selected_batch_id):
        return _report("failed", "google_web_upload_batch_invalid")
    try:
        dispatcher._gates(settings)  # Protected root/config/approval/code gates BEFORE Source.
        pipeline_lock = settings.runtime_directory / "pipeline.lock"
        if pipeline_lock == settings.lock_file:
            _fail("google_web_upload_lock_conflict")
        source = preparation._service_identity(settings.dashboard_user)
        backup = preparation._service_identity(settings.backup_user)
        if source.pw_uid == backup.pw_uid or source.pw_gid == backup.pw_gid:
            _fail("google_web_upload_identity_unsafe")
        with preparation._state_directory(settings.source_state, source.pw_uid) as source_fd, \
                _web_anchor(source_fd, source.pw_uid) as web_fd, \
                preparation._state_directory(settings.staging, backup.pw_uid) as staging_fd, \
                preparation._root_directory(settings.runtime_directory) as runtime:
            preparation._directory_info(runtime, 0, private=True)
            with preparation._broker_lock(pipeline_lock), dispatcher.trusted._runtime(settings) as (_, record_fd, _):
                dispatcher.trusted._write_private(record_fd, "pipeline-plan.json", private._canonical({
                    "schema_version": 1, "selected_batch_id": selected_batch_id,
                    "transfer_seconds": _TRANSFER_SECONDS, "dispatch_seconds": _DISPATCH_SECONDS,
                    "cleanup_seconds": _CLEANUP_SECONDS, "scope": "one selected completed web batch",
                    "retention": "all source, incomplete, ready and run records retained; no control intent changes"}))
                try:
                    metadata = _bridge(settings, source, backup, source_fd, web_fd, staging_fd,
                                       selected_batch_id, record_fd)
                    # The unchanged dispatcher revalidates protected settings,
                    # target binding, Source admission and actual live policy.
                    outcome = dispatcher.run_google_upload_dispatcher(settings, selected_batch_id,
                                                                       metadata["manifest_sha256"])
                    report = _dispatch_report(outcome, metadata["file_count"])
                except GoogleWebUploadPipelineError as error:
                    report = _failure_report(error)
                except Exception:
                    report = _report("failed", "google_web_upload_unavailable")
                dispatcher.trusted._write_private(record_fd, "pipeline-result.json", private._canonical(report))
                return report
    except GoogleWebUploadPipelineError as error:
        return _failure_report(error)
    except Exception:
        return _report("failed", "google_web_upload_unavailable")
