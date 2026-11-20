"""Reserve and verify bounded corrections through the existing specialist scope."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid


STATE = Path.home() / ".local/state/raspi-setup"
LEDGER = STATE / "COORDINATOR-LEDGER.json"
WORKER = Path.home() / ".local/bin/raspi-agent"
CODEX = "/usr/local/bin/codex"
MAX_CYCLES = 2


class GateError(Exception):
    """An approval, evidence or state check prevents further execution."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def transaction(path):
    """Serialize gate writers and replace the ledger atomically on commit."""
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = path.read_bytes()
        ledger = json.loads(original)
        yield ledger
        encoded = (json.dumps(ledger, indent=2) + "\n").encode()
        if json.loads(original) == ledger:
            return
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
            name = Path(staged.name)
            try:
                os.fchmod(staged.fileno(), stat.S_IMODE(path.stat().st_mode))
                staged.write(encoded)
                staged.flush()
                os.fsync(staged.fileno())
                if path.read_bytes() != original:
                    raise GateError("Ledger changed outside the gate lock; reconcile before proceeding")
                name.replace(path)
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                name.unlink(missing_ok=True)


def get_record(ledger, objective, validate):
    # This route has one objective per owner-approved milestone. Task titles and
    # Orca dispatch IDs cannot select a new retry history.
    milestone = validate(ledger, objective, "coding")
    config = milestone.get("correction_gate")
    worktree = Path(config.get("worktree", "")) if isinstance(config, dict) else Path()
    if not worktree.is_absolute() or not worktree.is_dir() or worktree.resolve() != worktree:
        raise GateError("Missing or invalid owner-approved correction worktree")
    check = Path(config.get("check_file", ""))
    if (
        not check.is_absolute() or check.resolve() != check or not check.is_file()
        or not check.is_relative_to(STATE) or check.is_relative_to(worktree)
        or config.get("check_sha256") != digest(check.read_bytes())
    ):
        raise GateError("Owner-approved read-only verifier is missing or changed")
    prompts = config.get("prompts")
    failure_exit = config.get("failure_exit")
    if (
        type(failure_exit) is not int or not 1 <= failure_exit <= 125
        or not isinstance(prompts, list) or not 1 <= len(prompts) <= MAX_CYCLES
        or any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts)
        or not config.get("finding")
        or config.get("worker_sha256") != digest(WORKER.read_bytes())
    ):
        raise GateError("Incomplete or changed owner-approved correction configuration")
    history = milestone.get("correction_attempts", [])
    if not isinstance(history, list) or len(history) > len(prompts):
        raise GateError("Invalid correction history")
    for cycle, attempt in enumerate(history, 1):
        if (
            not isinstance(attempt, dict) or attempt.get("cycle") != cycle
            or attempt.get("status") not in {
                "reserved", "passed", "failed", "blocked", "recovered_verified", "interrupted"
            }
            or (cycle < len(history) and attempt["status"] != "failed")
        ):
            raise GateError("Invalid correction attempt sequence")
    return config, history


def fingerprint(config):
    return digest(json.dumps(config, sort_keys=True).encode())


def run_check(config, output):
    with output.open("xb") as log:
        result = subprocess.run(
            [CODEX, "sandbox", "-P", ":read-only", "--sandbox-state-disable-network",
             "--", "/usr/bin/python3", "-I", "-B", config["check_file"]],
            cwd=config["worktree"], stdout=log, stderr=subprocess.STDOUT, check=False,
        )
    return result.returncode


def run_worker(objective, config, cycle, evidence):
    prompt = evidence / "task.txt"
    prompt.write_text(config["prompts"][cycle - 1] + "\n")
    with (evidence / "worker.log").open("xb") as log:
        result = subprocess.run(
            [str(WORKER), "coding", "--milestone", objective, "--task-file", str(prompt)],
            cwd=config["worktree"], stdout=log, stderr=subprocess.STDOUT, check=False,
        )
    return result.returncode


def snapshot(ledger_path, objective, validate):
    with transaction(ledger_path) as ledger:
        config, history = get_record(ledger, objective, validate)
    return config, history


def reserve(ledger_path, objective, validate, before, reservation):
    with transaction(ledger_path) as ledger:
        config, history = get_record(ledger, objective, validate)
        if (config, history) != before:
            raise GateError("Correction configuration or history changed during preflight")
        if len(history) >= len(config["prompts"]):
            raise GateError("Correction budget exhausted")
        if history and history[-1]["status"] != "failed":
            raise GateError("Latest attempt does not permit an automatic correction")
        history.append(reservation)
        ledger["milestones"][objective]["correction_attempts"] = history


def settle(ledger_path, objective, validate, original, result):
    with transaction(ledger_path) as ledger:
        config, history = get_record(ledger, objective, validate)
        if (
            not history or history[-1] != original or original["status"] != "reserved"
            or fingerprint(config) != original["configuration_sha256"]
        ):
            raise GateError("Stale reservation or changed configuration; result rejected")
        history[-1] = {**original, **result}


def execute(ledger_path, objective, validate, evidence_root):
    while True:
        before = snapshot(ledger_path, objective, validate)
        config, history = before
        already_verified = False
        if history:
            previous = history[-1]
            if previous["configuration_sha256"] != fingerprint(config):
                raise GateError("Correction configuration changed; reassessment required")
            already_verified = previous["status"] in {"passed", "recovered_verified"}
            if not already_verified and previous["status"] != "failed":
                raise GateError("Latest correction is unsettled or blocked; inspect its evidence")
        if not already_verified and len(history) >= len(config["prompts"]):
            raise GateError("Correction budget exhausted")
        token = uuid.uuid4().hex
        evidence = evidence_root / token
        evidence.mkdir(mode=0o700)
        preflight = run_check(config, evidence / "before.log")
        if preflight == 0:
            if snapshot(ledger_path, objective, validate) != before:
                raise GateError("Correction configuration or history changed during verification")
            print(f"Acceptance check passed; no correction reserved. Evidence: {evidence}")
            return 0
        if already_verified:
            raise GateError(f"Previously verified artifacts now fail with exit {preflight}; see {evidence}")
        if preflight != config["failure_exit"]:
            raise GateError(f"Unexpected preflight exit {preflight}; see {evidence}")

        attempt = {
            "id": token,
            "cycle": len(history) + 1,
            "status": "reserved",
            "evidence": str(evidence),
            "preflight_exit": preflight,
            "configuration_sha256": fingerprint(config),
        }
        reserve(ledger_path, objective, validate, before, attempt)
        print(f"Reserved cycle {attempt['cycle']}/{len(config['prompts'])} ({token}); {evidence}", flush=True)
        worker_exit = run_worker(objective, config, attempt["cycle"], evidence)
        check_exit = run_check(config, evidence / "after.log") if worker_exit == 0 else None
        status = "blocked"
        if worker_exit == 0 and check_exit == 0:
            status = "passed"
        elif worker_exit == 0 and check_exit == config["failure_exit"]:
            status = "failed"
        settle(ledger_path, objective, validate, attempt, {
            "status": status, "worker_exit": worker_exit, "check_exit": check_exit,
        })
        print(f"Cycle {attempt['cycle']} {status}; worker exit {worker_exit}, check exit {check_exit}",
              flush=True)
        if status == "passed":
            return 0
        if status == "blocked":
            raise GateError("Worker or acceptance check ended unexpectedly; reassessment required")


def recover(ledger_path, objective, validate, evidence_root, token):
    # The shell entry enters the same exclusive scope. The original scope must
    # release all of its processes before this invocation can acquire it.
    config, history = snapshot(ledger_path, objective, validate)
    if not history or history[-1]["id"] != token or history[-1]["status"] != "reserved":
        raise GateError("No current interrupted reservation matches that ID")
    attempt = history[-1]
    if fingerprint(config) != attempt["configuration_sha256"]:
        raise GateError("Correction configuration changed; reassessment required")
    # Keep each recovery check, including evidence from interrupted reconciliations.
    evidence = Path(tempfile.mkdtemp(prefix=token + "-recovery-", dir=evidence_root))
    check_exit = run_check(config, evidence / "recovery.log")
    status = "recovered_verified" if check_exit == 0 else "interrupted"
    settle(ledger_path, objective, validate, attempt, {
        "status": status, "worker_exit": None, "check_exit": check_exit,
        "recovery_evidence": str(evidence),
    })
    print(f"Recovery recorded {status}; acceptance check exit {check_exit}; {evidence}")
    return 0 if check_exit == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("run").add_argument("objective")
    recovery = actions.add_parser("recover")
    recovery.add_argument("objective")
    recovery.add_argument("reservation_id")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,79}", args.objective):
        parser.error("Expected the approved objective ID")
    if args.action == "recover" and not re.fullmatch(r"[a-f0-9]{32}", args.reservation_id):
        parser.error("Expected the current reservation ID")
    # Reuse milestone validation from the installed, fixed launcher without
    # starting a worker or introducing an independent authorization policy.
    try:
        uid = os.getuid()
        prefix = f"0::/user.slice/user-{uid}.slice/user@{uid}.service/"
        groups = Path("/proc/self/cgroup").read_text().splitlines()
        if not any(group.startswith(prefix) and group.endswith("/raspi-omx-specialist.scope")
                   for group in groups):
            raise GateError("Use the exclusive specialist scope through tools/raspi-ai-team")
        import runpy
        validate = runpy.run_path(str(WORKER))["validate_milestone"]
        evidence = STATE / "correction-evidence"
        evidence.mkdir(mode=0o700, exist_ok=True)
        if args.action == "recover":
            return recover(LEDGER, args.objective, validate, evidence, args.reservation_id)
        return execute(LEDGER, args.objective, validate, evidence)
    except (GateError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Correction gate stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
