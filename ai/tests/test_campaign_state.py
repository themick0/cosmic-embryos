from __future__ import annotations

import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import campaign_state as state


def _contender(path, identifier, start, results):
    start.wait(5)
    try:
        state.reserve(Path(path), identifier, "test-model", "research", "a" * 64)
        results.put("reserved")
    except state.AdmissionError:
        results.put("denied")


class CampaignStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="campaign ledger ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "ai/campaign/control/sessions.sqlite3"
        self.policy = {"max_sessions": 3, "max_active": 1, "allowed_models": ["test-model", "other-model"]}

    def reserve(self, identifier=None, **changes):
        args = {"assignment_id": identifier or uuid.uuid4().hex, "model": "test-model",
                "kind": "research", "payload_sha256": "a" * 64}
        args.update(changes)
        return state.reserve(self.path, **args)

    def test_creation_refuses_overwrite_and_wrong_location(self):
        initial = state.create(self.path, self.policy)
        with self.assertRaisesRegex(state.StateError, "already exists"):
            state.create(self.path, dict(self.policy, max_sessions=100))
        self.assertEqual(state.snapshot(self.path), initial)
        with self.assertRaisesRegex(state.StateError, "ai/campaign/control"):
            state.create(self.root / "unsafe.sqlite3", self.policy)

    def test_reservation_replay_is_bound_to_model_kind_and_payload(self):
        state.create(self.path, self.policy)
        identifier = uuid.uuid4().hex
        initial = self.reserve(identifier)
        self.assertEqual(self.reserve(identifier), initial)
        for changes in ({"model": "other-model"}, {"kind": "smoke"}, {"payload_sha256": "b" * 64}):
            with self.assertRaisesRegex(state.StateError, "already binds"):
                self.reserve(identifier, **changes)
        self.assertEqual(state.snapshot(self.path)["sessions_used"], 1)

    def test_failures_spend_admissions_and_terminal_outcomes_are_immutable(self):
        state.create(self.path, dict(self.policy, max_sessions=1))
        job = self.reserve()
        identifier = job["assignment_id"]
        launched = state.mark_launched(self.path, identifier)
        self.assertEqual(state.mark_launched(self.path, identifier), launched)
        settled = state.settle(self.path, identifier, "failed", None)
        self.assertIsNone(settled["usage"])
        self.assertEqual(state.settle(self.path, identifier, "failed", None), settled)
        for status, usage in (("completed", None), ("failed", {})):
            with self.assertRaisesRegex(state.StateError, "immutable"):
                state.settle(self.path, identifier, status, usage)
        with self.assertRaises(state.StateError):
            state.mark_launched(self.path, identifier)
        with self.assertRaisesRegex(state.AdmissionError, "session limit exhausted"):
            self.reserve()
        self.assertEqual(state.snapshot(self.path)["active"], 0)

    def test_uncertainty_retains_slot_until_explicit_settlement(self):
        state.create(self.path, self.policy)
        identifier = self.reserve()["assignment_id"]
        uncertain = state.mark_uncertain(self.path, identifier, "Process identity is not reconciled")
        self.assertEqual(state.mark_uncertain(self.path, identifier, uncertain["uncertain_reason"]), uncertain)
        with self.assertRaisesRegex(state.AdmissionError, "Active session limit"):
            self.reserve()
        with self.assertRaises(state.StateError):
            state.mark_launched(self.path, identifier)
        state.settle(self.path, identifier, "failed")
        self.reserve()
        self.assertEqual(state.snapshot(self.path)["sessions_used"], 2)

    def test_two_processes_cannot_overreserve_one_slot(self):
        state.create(self.path, dict(self.policy, max_sessions=1))
        context = multiprocessing.get_context("spawn")
        start, results = context.Event(), context.Queue()
        processes = [context.Process(target=_contender, args=(str(self.path), uuid.uuid4().hex, start, results))
                     for _ in range(2)]
        try:
            for process in processes:
                process.start()
            start.set()
            self.assertEqual(sorted(results.get(timeout=10) for _ in processes), ["denied", "reserved"])
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(5)
            results.close()
        self.assertEqual(state.snapshot(self.path)["sessions_used"], 1)

    def test_subprocess_crash_does_not_refund_reservation(self):
        state.create(self.path, self.policy)
        identifier = uuid.uuid4().hex
        code = ("import os,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from embryo_harness import campaign_state as s; "
                "s.reserve(Path(sys.argv[2]),sys.argv[3],'test-model','smoke','a'*64); os._exit(17)")
        result = subprocess.run([sys.executable, "-c", code, str(Path(state.__file__).resolve().parents[1]),
                                 str(self.path), identifier], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr.decode())
        snapshot = state.snapshot(self.path)
        self.assertEqual(snapshot["counts"]["reserved"], 1)
        self.assertEqual(snapshot["active"], 1)
        self.assertEqual(snapshot["sessions_remaining"], 2)
        with self.assertRaises(state.AdmissionError):
            self.reserve()

    def test_strict_policy_usage_and_symlink_rejection(self):
        with self.assertRaises(state.StateError):
            state.create(self.path, dict(self.policy, max_sessions=True))
        state.create(self.path, self.policy)
        with self.assertRaises(state.AdmissionError):
            self.reserve(model="not-allowed")
        job = self.reserve()
        with self.assertRaises(state.StateError):
            state.settle(self.path, job["assignment_id"], "completed")
        state.mark_launched(self.path, job["assignment_id"])
        for usage in ({"tokens": float("nan")}, {1: "non-string-key"}, {"tokens": object()}):
            with self.assertRaises(state.StateError):
                state.settle(self.path, job["assignment_id"], "completed", usage)
        final = state.settle(self.path, job["assignment_id"], "completed", {"input_tokens": 10})
        self.assertEqual(final["usage"], {"input_tokens": 10})
        linked = self.path.with_name("linked.sqlite3")
        linked.symlink_to(self.path)
        with self.assertRaisesRegex(state.StateError, "symlinks"):
            state.snapshot(linked)


if __name__ == "__main__":
    unittest.main()
