import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch


CORRECTIONS = Path(__file__).resolve().parents[1] / "tools/raspi-corrections.py"
GATE = runpy.run_path(str(CORRECTIONS))
RUNTIME = GATE["execute"].__globals__
OBJECTIVE = "FIXTURE-OBJECTIVE"


class RaspiCorrectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        state = root / "state"
        state.mkdir()
        worker = state / "worker.py"
        worker.write_text("# fixed worker\n")
        check = state / "check.py"
        check.write_text("exit(42)\n")
        fixture = root / "worktree"
        fixture.mkdir()
        self.ledger = state / "ledger.json"
        self.evidence = state / "evidence"
        self.evidence.mkdir()
        self.config = {
            "finding": "Specified acceptance fails",
            "worktree": str(fixture),
            "check_file": str(check),
            "check_sha256": GATE["digest"](check.read_bytes()),
            "worker_sha256": GATE["digest"](worker.read_bytes()),
            "failure_exit": 42,
            "prompts": ["first correction", "second correction"],
        }
        self.ledger.write_text(json.dumps({
            "milestones": {OBJECTIVE: {"correction_gate": self.config}},
            "historical": {"completed": True},
        }))
        runtime = patch.dict(RUNTIME, STATE=state, WORKER=worker)
        runtime.start()
        self.addCleanup(runtime.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def validate(self, ledger, objective, role):
        self.assertEqual(role, "coding")
        return ledger["milestones"][objective]

    def history(self):
        return GATE["snapshot"](self.ledger, OBJECTIVE, self.validate)[1]

    def execute(self):
        return GATE["execute"](self.ledger, OBJECTIVE, self.validate, self.evidence)

    def recover(self, token):
        return GATE["recover"](self.ledger, OBJECTIVE, self.validate, self.evidence, token)

    def test_persists_reservation_before_worker_and_resumes_next_cycle(self):
        def worker(objective, config, cycle, evidence):
            attempt = self.history()[-1]
            self.assertEqual((attempt["status"], attempt["cycle"]), ("reserved", cycle))
            self.assertEqual(attempt["evidence"], str(evidence))
            return 0

        with patch.dict(RUNTIME, run_worker=worker):
            with patch.dict(RUNTIME, run_check=lambda *_: next(checks)):
                checks = iter([42, 42, 42, 0])
                self.assertEqual(self.execute(), 0)
            with patch.dict(RUNTIME, run_check=lambda *_: 0,
                            run_worker=lambda *_: self.fail("duplicate was dispatched")):
                self.assertEqual(self.execute(), 0)
        self.assertEqual([entry["status"] for entry in self.history()], ["failed", "passed"])
        self.assertTrue(json.loads(self.ledger.read_text())["historical"]["completed"])

    def test_exhaustion_survives_relaunch_and_new_task_name(self):
        with patch.dict(RUNTIME, run_check=lambda *_: 42, run_worker=lambda *_: 0):
            with self.assertRaisesRegex(GATE["GateError"], "budget exhausted"):
                self.execute()
        with GATE["transaction"](self.ledger) as ledger:
            ledger["milestones"][OBJECTIVE]["title"] = "Renamed Task"
        with patch.dict(RUNTIME, run_worker=lambda *_: self.fail("third correction")):
            with self.assertRaisesRegex(GATE["GateError"], "budget exhausted"):
                self.execute()
        self.assertEqual(len(self.history()), 2)

    def test_old_result_cannot_settle_current_reservation(self):
        def worker(objective, config, cycle, evidence):
            if cycle == 1:
                saved.append(self.history()[-1])
            else:
                with self.assertRaisesRegex(GATE["GateError"], "Stale reservation"):
                    GATE["settle"](self.ledger, OBJECTIVE, self.validate, saved[0],
                                   {"status": "passed"})
            return 0

        saved = []
        checks = iter([42, 42, 42, 0])
        with patch.dict(RUNTIME, run_check=lambda *_: next(checks), run_worker=worker):
            self.assertEqual(self.execute(), 0)
        self.assertEqual([a["status"] for a in self.history()], ["failed", "passed"])

    def test_interruption_keeps_spent_reservation_until_explicit_recovery(self):
        def interrupt(*_):
            raise KeyboardInterrupt

        with patch.dict(RUNTIME, run_check=lambda *_: 42, run_worker=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
            token = self.history()[-1]["id"]
            with self.assertRaisesRegex(GATE["GateError"], "unsettled"):
                self.execute()
            with self.assertRaisesRegex(GATE["GateError"], "No current"):
                self.recover("earlier")
            self.assertEqual(self.recover(token), 1)
            with self.assertRaisesRegex(GATE["GateError"], "unsettled or blocked"):
                self.execute()
        self.assertEqual(self.history()[-1]["status"], "interrupted")
        self.assertEqual(len(self.history()), 1)

    def test_recovery_can_verify_existing_artifact_without_relaunch(self):
        with patch.dict(RUNTIME, run_check=lambda *_: 42, run_worker=lambda *_: 0):
            with patch.dict(RUNTIME, settle=lambda *_: self.fail("simulated lost result")):
                with self.assertRaises(AssertionError):
                    self.execute()
        with patch.dict(RUNTIME, run_check=lambda *_: 0,
                        run_worker=lambda *_: self.fail("recovery launched another model")):
            self.assertEqual(self.recover(self.history()[-1]["id"]), 0)
            self.assertEqual(self.execute(), 0)
        self.assertIsNone(self.history()[-1]["worker_exit"])
        self.assertEqual(self.history()[-1]["status"], "recovered_verified")

    def test_worker_error_stops_with_evidence(self):
        with patch.dict(RUNTIME, run_worker=lambda *_: 17, run_check=lambda *_: 42):
            with self.assertRaisesRegex(GATE["GateError"], "ended unexpectedly"):
                self.execute()
        attempt = self.history()[-1]
        self.assertEqual((attempt["status"], attempt["worker_exit"], attempt["check_exit"]),
                         ("blocked", 17, None))

    def test_drift_or_unexpected_preflight_does_not_reserve(self):
        with patch.dict(RUNTIME, run_worker=lambda *_: self.fail("unexpected dispatch"),
                        run_check=lambda *_: 127):
            with self.assertRaisesRegex(GATE["GateError"], "Unexpected preflight exit 127"):
                self.execute()
        self.assertEqual(self.history(), [])
        Path(self.config["check_file"]).write_text("exit(0)\n")
        with self.assertRaisesRegex(GATE["GateError"], "verifier is missing or changed"):
            self.execute()

    def test_verified_result_requires_current_configuration_and_artifact(self):
        checks = iter([42, 0])
        with patch.dict(RUNTIME, run_check=lambda *_: next(checks), run_worker=lambda *_: 0):
            self.assertEqual(self.execute(), 0)
        with patch.dict(RUNTIME, run_check=lambda *_: 42,
                        run_worker=lambda *_: self.fail("verified objective relaunched")):
            with self.assertRaisesRegex(GATE["GateError"], "artifacts now fail"):
                self.execute()
        with GATE["transaction"](self.ledger) as ledger:
            ledger["milestones"][OBJECTIVE]["correction_gate"]["prompts"][0] = "changed plan"
        with patch.dict(RUNTIME, run_check=lambda *_: self.fail("ran stale verification")):
            with self.assertRaisesRegex(GATE["GateError"], "configuration changed"):
                self.execute()
        self.assertEqual(len(self.history()), 1)

    def test_empty_recovery_id_cannot_select_execution(self):
        with patch.object(sys, "argv", [str(CORRECTIONS), "recover", OBJECTIVE, ""]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    GATE["main"]()
        self.assertEqual(caught.exception.code, 2)

    def test_two_preflights_cannot_reserve_the_same_cycle(self):
        first = GATE["snapshot"](self.ledger, OBJECTIVE, self.validate)
        reservation = {"id": "first", "cycle": 1, "status": "reserved",
                       "configuration_sha256": GATE["fingerprint"](self.config)}
        GATE["reserve"](self.ledger, OBJECTIVE, self.validate, first, reservation)
        with self.assertRaisesRegex(GATE["GateError"], "history changed"):
            GATE["reserve"](self.ledger, OBJECTIVE, self.validate, first,
                            {**reservation, "id": "duplicate"})
        self.assertEqual(self.history(), [reservation])


if __name__ == "__main__":
    unittest.main()
