"""Opt-in root trigger for current owner controls; eligibility grants no authority.

Completed/expired intents do not invoke the Test broker. Temporary eligibility
or pre-worker Wi-Fi failures can be retried by an explicitly installed service.
The Test broker owns actual exports, release, completion and cleanup.
"""

import os
import time

from . import google_broker as preparation
from . import google_control as control
from . import google_test_broker as tester


def _result(status, code):
    return {"status": status, "code": code, "uploads_enabled": False,
            "account_verified": False, "current_worker_state_checked": False}


def _pending(settings, account):
    incoming, outgoing = preparation._private_pipe()
    child = None
    try:
        child = preparation._spawn(settings, account, ["google-control-pending", "--state", str(settings.source_state),
            "--public-origin", settings.public_origin, "--output-fd", str(outgoing)], (outgoing,))
        os.close(outgoing)
        outgoing = None
        owned, incoming = incoming, None
        value = preparation._receive(owned, time.monotonic() + 5, 1024)
        if preparation._wait_child(child, time.monotonic() + 2):
            raise ValueError("Source eligibility unavailable")
        if (not isinstance(value, dict) or set(value) != {"schema_version", "provider", "control_state", "pending", "checked_at"}
                or not control.setup._valid_header(value) or value["control_state"] not in control.CONTROL_STATES
                or type(value["pending"]) is not bool or value["pending"] != (value["control_state"] in control.PENDING_STATES)
                or not control.setup._timestamp(value["checked_at"])
                or not value["checked_at"] <= time.time() < value["checked_at"] + 30):
            raise ValueError("Source eligibility unavailable")
        return value
    finally:
        for fd in (incoming, outgoing):
            if fd is not None:
                os.close(fd)
        if child is not None:
            preparation._stop_children([child])


def run_google_control_runner(settings):
    """One pending check and at most one separately gated broker invocation.

    No timer, service operation, owner intent or permission is created here.
    Exit handling belongs to the CLI: deferred is temporary; failure is a
    deliberate stop requiring a new event or operator recovery.
    """
    if not isinstance(settings, tester.GoogleTestBrokerSettings):
        return _result("failed", "google_control_runner_configuration_invalid")
    if not settings.enabled:
        return _result("disabled", "google_control_runner_disabled")
    try:
        tester._gates(settings)
        source = preparation._service_identity(settings.dashboard_user)
        try:
            pending = _pending(settings, source)
        except Exception:
            return _result("deferred", "google_control_runner_source_unavailable")
        if pending["control_state"] == "unavailable":
            return _result("deferred", "google_control_runner_source_unavailable")
        if pending["control_state"] == "invalid":
            return _result("failed", "google_control_runner_source_invalid")
        if not pending["pending"]:
            return _result("idle", "google_control_runner_no_pending_request")
        # This metadata is never admission: the broker rechecks configuration,
        # the current intent, owner/source, worker and kernel policy itself.
        result = tester.run_google_test_broker(settings)
        if result.get("status") == "failed" and result.get("code") in (
                "google_test_broker_wifi_unavailable_before_worker",
                "google_test_broker_source_unavailable_before_worker"):
            return _result("deferred", "google_control_runner_request_deferred")
        return result
    except Exception:
        return _result("failed", "google_control_runner_unavailable")
