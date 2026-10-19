from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import campaign_host as host, campaign_state as state
from embryo_harness.campaign_worker import _layout
from embryo_harness.files import HarnessError, read_json, record, write_json


PUBLIC = {"phase": {"phase": "p2", "split": "val", "data_open": True},
          "boards": {"boards": [{"key": "T2:embryo:val_interp"}]}}


class CampaignHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pilot host ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.ctl = host.control(self.root)
        self.ctl.mkdir(parents=True)
        self.ledger = self.ctl / "pilot-sessions.sqlite"
        state.create(self.ledger, {"max_sessions": 2, "max_active": 1, "allowed_models": [host.MODEL]})
        release = self.root / "release"
        (release / "ai/embryo_harness").mkdir(parents=True)
        (release / "AGENTS.md").write_text("Instructions stay outside the research mount.")
        (release / "ai/source.py").write_text("pass\n")
        for name in ("campaign_host.py", "campaign_worker.py"):
            (release / "ai/embryo_harness" / name).write_text("# Frozen fixture module.\n")
        self.source_names = {"ai/source.py", "ai/embryo_harness/campaign_host.py",
                             "ai/embryo_harness/campaign_worker.py"}
        write_json(release / "release.json", {"revision": "fixture", "files": {
            name: record(release / name) for name in self.source_names | {"AGENTS.md"}}})
        self.jobs = []
        for kind, seconds in (("smoke", 120), ("research", 1800)):
            identifier = uuid.uuid4().hex
            directory = self.root / "worker/ai/campaign/jobs" / identifier
            directory.mkdir(parents=True)
            source_manifest = host.job_source(release, directory)
            (directory / "source/datasets").mkdir()
            (directory / "datasets").mkdir()
            payload = {"id": identifier, "kind": kind, "model": host.MODEL,
                       "wall_time_seconds": seconds, "image_id": "sha256:" + "a" * 64,
                       "source_revision": "fixture", "source_manifest": source_manifest,
                       "inputs": {}, "official_uploads": False}
            write_json(directory / "worker.json", payload)
            self.jobs.append({**payload, "directory": str(directory),
                              "payload_sha256": record(directory / "worker.json")["sha256"]})
        write_json(self.ctl / "pilot.json", {"jobs": self.jobs, "release": str(release),
                                            "worker_workspace": str(self.root / "worker")})
        self.addCleanup(patch.stopall)
        patch.object(host, "host_cli", return_value="sbx").start()
        self.phase = patch.object(host, "phase_check", return_value=PUBLIC).start()
        self.launch = patch.object(host.subprocess, "Popen", side_effect=self.complete).start()

    def complete(self, argv, **kwargs):
        job_dir = Path(argv[argv.index("--job") + 1])
        job = read_json(job_dir / "worker.json")
        write_json(job_dir / "worker-state.json", {
            "job_id": job["id"], "kind": job["kind"], "model": job["model"],
            "image_id": job["image_id"], "status": "completed"})
        write_json(job_dir / "output/report/result.json", {
            "kind": job["kind"], "status": "completed", "usage": {"input_tokens": 7}})
        process = Mock()
        process.wait.return_value = 0
        return process

    def test_two_sequential_sessions_are_not_replayed(self):
        self.assertEqual(host.run(self.root)["status"], "completed")
        self.assertEqual(host.run(self.root)["status"], "completed")
        self.assertEqual(self.launch.call_count, 2)
        snapshot = state.snapshot(self.ledger)
        self.assertEqual(snapshot["sessions_used"], 2)
        self.assertEqual(snapshot["sessions_remaining"], 0)
        self.assertEqual(snapshot["active"], 0)
        self.assertEqual([row["usage"] for row in snapshot["jobs"]], [{"input_tokens": 7}] * 2)

    def test_only_ai_and_dataset_mount_targets_are_exposed(self):
        directory = Path(self.jobs[0]["directory"])
        self.assertEqual({p.name for p in (directory / "source").iterdir()}, {"ai", "datasets"})
        self.assertEqual(set(read_json(directory / "source-manifest.json")["files"]), self.source_names)

    def test_unknown_process_outcome_preserves_admission_and_blocks_replay(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("sbx", 1), 0]
        self.launch.side_effect = None
        self.launch.return_value = process
        with self.assertRaisesRegex(ValueError, "did not complete"):
            host.run(self.root)
        self.assertEqual(state.snapshot(self.ledger)["counts"]["uncertain"], 1)
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            host.run(self.root)
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(state.snapshot(self.ledger)["active"], 1)
        process.kill.assert_called_once_with()

    def test_late_completed_record_allows_next_job_without_replaying_first(self):
        job = self.jobs[0]
        state.reserve(self.ledger, job["id"], job["model"], job["kind"], job["payload_sha256"])
        state.mark_launched(self.ledger, job["id"])
        state.mark_uncertain(self.ledger, job["id"], "Host exited")
        self.complete(["sbx", "--job", job["directory"]])
        host.run(self.root)
        self.assertEqual(self.launch.call_count, 1)
        self.assertIn(self.jobs[1]["directory"], self.launch.call_args.args[0])

    def test_foreign_worker_record_does_not_release_slot(self):
        job = self.jobs[0]
        state.reserve(self.ledger, job["id"], job["model"], job["kind"], job["payload_sha256"])
        state.mark_launched(self.ledger, job["id"])
        self.complete(["sbx", "--job", job["directory"]])
        path = Path(job["directory"]) / "worker-state.json"
        changed = read_json(path)
        changed["job_id"] = uuid.uuid4().hex
        write_json(path, changed)
        self.assertEqual(host.reconcile(self.ledger, job), "uncertain")
        self.assertEqual(state.snapshot(self.ledger)["active"], 1)

    def test_phase_change_blocks_second_session(self):
        self.phase.side_effect = [PUBLIC, {"phase": {"phase": "p3"}, "boards": PUBLIC["boards"]}]
        with self.assertRaisesRegex(ValueError, "current competition phase"):
            host.run(self.root)
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(state.snapshot(self.ledger)["sessions_used"], 1)

    def test_stop_between_jobs_does_not_interrupt_completed_job(self):
        def complete_then_stop(argv, **kwargs):
            result = self.complete(argv, **kwargs)
            (self.ctl / "STOP_AFTER_CURRENT").touch()
            return result
        self.launch.side_effect = complete_then_stop
        with self.assertRaisesRegex(ValueError, "Stop requested"):
            host.run(self.root)
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(state.snapshot(self.ledger)["counts"]["completed"], 1)

    def test_stop_during_phase_validation_prevents_admission(self):
        def stop_during_validation():
            host.stop(self.root)
            return PUBLIC
        self.phase.side_effect = stop_during_validation
        with self.assertRaisesRegex(ValueError, "Stop requested"):
            host.run(self.root)
        self.launch.assert_not_called()
        self.assertEqual(state.snapshot(self.ledger)["sessions_used"], 0)

    def test_stop_serializes_with_creation_but_does_not_wait_for_process_exit(self):
        requested = threading.Event()
        stopped = threading.Event()
        errors = []
        threads = []

        def request_stop():
            requested.set()
            try:
                host.stop(self.root)
            except BaseException as error:
                errors.append(error)
            finally:
                stopped.set()

        def launch(argv, **kwargs):
            thread = threading.Thread(target=request_stop, daemon=True)
            threads.append(thread)
            thread.start()
            self.assertTrue(requested.wait(2))
            self.assertFalse(stopped.wait(0.05), "Stop must serialize with process creation")
            process = Mock()
            def wait(timeout):
                self.assertTrue(stopped.wait(2), "Stop must not wait for the active process")
                self.complete(argv)
                return 0
            process.wait.side_effect = wait
            return process

        self.launch.side_effect = launch
        try:
            with self.assertRaisesRegex(ValueError, "Stop requested"):
                host.run(self.root)
        finally:
            for thread in threads:
                thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(state.snapshot(self.ledger)["counts"]["completed"], 1)

    def test_changed_outer_launcher_or_import_is_rejected_before_admission(self):
        release = Path(read_json(self.ctl / "pilot.json")["release"])
        for name in ("campaign_host.py", "campaign_worker.py"):
            with self.subTest(name=name):
                path = release / "ai/embryo_harness" / name
                original = path.read_bytes()
                path.write_text("# Changed outer code; inner source remains untouched.\n")
                with self.assertRaisesRegex(HarnessError, "Integrity check failed"):
                    host.run(self.root)
                path.write_bytes(original)
        self.launch.assert_not_called()
        self.assertEqual(state.snapshot(self.ledger)["sessions_used"], 0)

    def test_existing_assignment_identity_is_checked_before_recovery_or_skip(self):
        job = self.jobs[0]
        state.reserve(self.ledger, job["id"], job["model"], job["kind"], job["payload_sha256"])
        for status in ("reserved", "launched", "uncertain", "completed"):
            if status == "launched":
                state.mark_launched(self.ledger, job["id"])
            elif status == "uncertain":
                state.mark_uncertain(self.ledger, job["id"], "Fixture interruption")
            elif status == "completed":
                state.settle(self.ledger, job["id"], "completed", None)
            for key, value in (("model", "unapproved-model"), ("kind", "research"),
                               ("payload_sha256", "b" * 64)):
                with self.subTest(status=status, field=key):
                    payload_path = Path(job["directory"]) / "worker.json"
                    original_payload = payload_path.read_bytes()
                    manifest = read_json(self.ctl / "pilot.json")
                    original_manifest = read_json(self.ctl / "pilot.json")
                    payload = read_json(payload_path)
                    if key != "payload_sha256":
                        manifest["jobs"][0][key] = value
                        payload[key] = value
                    payload_path.write_text(json.dumps(payload, sort_keys=True))
                    manifest["jobs"][0]["payload_sha256"] = record(payload_path)["sha256"]
                    write_json(self.ctl / "pilot.json", manifest)
                    with patch.object(host, "reconcile") as reconcile:
                        with self.assertRaisesRegex(ValueError, "assignment"):
                            host.run(self.root)
                        reconcile.assert_not_called()
                    payload_path.write_bytes(original_payload)
                    write_json(self.ctl / "pilot.json", original_manifest)
        self.launch.assert_not_called()
        self.assertEqual(state.snapshot(self.ledger)["sessions_used"], 1)

    def test_unapproved_manifest_model_is_rejected_before_first_admission(self):
        manifest = read_json(self.ctl / "pilot.json")
        manifest["jobs"][0]["model"] = "unapproved-model"
        write_json(self.ctl / "pilot.json", manifest)
        with self.assertRaisesRegex(ValueError, "assignment model"):
            host.run(self.root)
        self.launch.assert_not_called()
        self.assertEqual(state.snapshot(self.ledger)["sessions_used"], 0)

    def test_duplicated_payload_fields_cannot_change_without_the_frozen_assignment(self):
        original = read_json(self.ctl / "pilot.json")
        for key, value in (("wall_time_seconds", 3600), ("image_id", "sha256:" + "b" * 64),
                           ("inputs", {"unexpected.h5ad": {"bytes": 1, "sha256": "b" * 64}})):
            with self.subTest(field=key):
                manifest = read_json(self.ctl / "pilot.json")
                manifest["jobs"][0][key] = value
                write_json(self.ctl / "pilot.json", manifest)
                with self.assertRaisesRegex(ValueError, "assignment changed"):
                    host.run(self.root)
                write_json(self.ctl / "pilot.json", original)
        self.launch.assert_not_called()
        self.assertEqual(state.snapshot(self.ledger)["sessions_used"], 0)

    def test_failed_inner_result_prevents_research_even_with_zero_outer_exit(self):
        def fail_inner(argv, **kwargs):
            result = self.complete(argv, **kwargs)
            job_dir = Path(argv[argv.index("--job") + 1])
            write_json(job_dir / "output/report/result.json", {"status": "failed", "usage": None})
            return result
        self.launch.side_effect = fail_inner
        for _ in range(2):
            with self.assertRaises(ValueError):
                host.run(self.root)
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(state.snapshot(self.ledger)["counts"]["failed"], 1)

    def test_source_manifest_changes_are_rejected_before_admission(self):
        path = Path(self.jobs[0]["directory"]) / "source-manifest.json"
        write_json(path, {"revision": "fixture", "files": {}})
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            host.run(self.root)
        self.launch.assert_not_called()
        self.assertEqual(state.snapshot(self.ledger)["sessions_used"], 0)


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pilot release ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.names = {"ai/embryo_harness/campaign_host.py", "ai/embryo_harness/campaign_inside.py",
                      "ai/embryo_harness/campaign_worker.py", "ai/embryo_harness/campaign_image.py",
                      "ai/embryo_harness/pilot_checks.py", "ai/resources/pilot/Dockerfile", "AGENTS.md"}
        for name in self.names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Synthetic release fixture\n")
        sources = patch.object(host.sandbox_setup, "source_files",
                               side_effect=lambda root: [root / name for name in sorted(self.names)])
        sources.start()
        self.addCleanup(sources.stop)
        self.release = host.source_release(self.root)

    def test_cached_release_manifest_must_match_the_actual_source_inventory(self):
        self.assertEqual(host.source_release(self.root), self.release)
        path = self.release / "release.json"
        original = read_json(path)
        for changed in ({"revision": original["revision"], "files": {}},
                        {"revision": "different", "files": original["files"]}):
            with self.subTest(manifest=changed):
                write_json(path, changed)
                with self.assertRaisesRegex(ValueError, "release manifest changed"):
                    host.source_release(self.root)
                self.assertEqual(read_json(path), changed)
        write_json(path, original)
        self.assertEqual(host.source_release(self.root), self.release)

    def test_existing_worker_release_metadata_is_checked_without_repairing_it(self):
        destination = self.root / "worker-release"
        host.copy_release(self.release, destination)
        host.copy_release(self.release, destination)
        path = destination / "release.json"
        changed = {"revision": self.release.name, "files": {}}
        write_json(path, changed)
        with self.assertRaisesRegex(ValueError, "Worker release manifest changed"):
            host.copy_release(self.release, destination)
        self.assertEqual(read_json(path), changed)

    def test_job_snapshot_copies_only_manifested_source_files(self):
        (self.release / "ai/unlisted-measurement.h5ad").write_bytes(b"Synthetic unlisted data")
        (self.release / "ai/embryo_harness/unlisted.py").write_text("# Unlisted source\n")
        job = self.root / "job"
        job.mkdir()
        host.job_source(self.release, job)
        source = job / "source"
        actual = {str(path.relative_to(source)) for path in source.rglob("*") if path.is_file()}
        expected = {name for name in self.names if name.startswith("ai/")}
        self.assertEqual(actual, expected)
        self.assertEqual(set(read_json(job / "source-manifest.json")["files"]), expected)
        self.assertFalse((source / "AGENTS.md").exists())


class PilotPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pilot preparation ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        release = self.root / "ai/campaign/releases/fixture"
        (release / "ai/embryo_harness").mkdir(parents=True)
        entry = release / "ai/embryo_harness/campaign_inside.py"
        entry.write_text("pass\n")
        (release / "AGENTS.md").write_text("Repository instructions\n")
        write_json(release / "release.json", {"revision": "fixture", "files": {
            "ai/embryo_harness/campaign_inside.py": record(entry),
            "AGENTS.md": record(release / "AGENTS.md")}})
        (self.root / "datasets").mkdir()
        for name in host.INPUTS:
            (self.root / "datasets" / name).write_bytes(name.encode())
        preflight = {"communication_ok": True, "environment_checks_ok": True,
                     "roles": [{"role": name} for name in ("coordinator", "worker-a", "uploader")]}
        self.addCleanup(patch.stopall)
        patch.object(host, "host_cli", return_value="sbx").start()
        patch.object(host, "latest", return_value=preflight).start()
        patch.object(host, "phase_check", return_value=PUBLIC).start()
        patch.object(host, "source_release", return_value=release).start()
        self.image_setup = patch.object(host.subprocess, "run", side_effect=self.setup_image).start()

    def setup_image(self, argv, **kwargs):
        output = Path(argv[argv.index("--output") + 1])
        write_json(output / "image.json", {"image_id": "sha256:" + "a" * 64})
        return subprocess.CompletedProcess(argv, 0)

    def test_preparation_builds_jobs_accepted_by_actual_worker_layout(self):
        manifest = host.prepare(self.root)
        self.assertEqual(host.prepare(self.root), manifest)
        self.assertEqual(self.image_setup.call_count, 1)
        for job in manifest["jobs"]:
            directory = Path(job["directory"])
            self.assertEqual(_layout(directory, job["kind"]), (directory, job["id"]))
            self.assertEqual(record(directory / "source-manifest.json"), job["source_manifest"])
            self.assertFalse((directory / "source/AGENTS.md").exists())
        ledger = state.snapshot(host.control(self.root) / "pilot-sessions.sqlite")
        self.assertEqual(ledger["sessions_used"], 0)
        self.assertEqual(ledger["sessions_remaining"], 2)

    def test_explicit_prepare_retry_retains_failed_setup_and_uses_fresh_attempt(self):
        attempts = []
        def fail_once(argv, **kwargs):
            output = Path(argv[argv.index("--output") + 1])
            attempts.append(output)
            self.assertEqual(list(output.iterdir()), [])
            if len(attempts) == 1:
                (output / "build.log").write_text("Retained fixture setup failure\n")
                raise subprocess.CalledProcessError(1, argv)
            return self.setup_image(argv, **kwargs)
        self.image_setup.side_effect = fail_once
        with self.assertRaises(subprocess.CalledProcessError):
            host.prepare(self.root)
        self.assertFalse((host.control(self.root) / "pilot-sessions.sqlite").exists())
        manifest = host.prepare(self.root)
        self.assertNotEqual(attempts[0], attempts[1])
        self.assertEqual((attempts[0] / "build.log").read_text(), "Retained fixture setup failure\n")
        self.assertTrue((attempts[1] / "image.json").is_file())
        self.assertEqual(host.prepare(self.root), manifest)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(state.snapshot(host.control(self.root) / "pilot-sessions.sqlite")["sessions_used"], 0)

    def test_successful_setup_is_revalidated_after_interrupted_job_preparation(self):
        original = host.job_source
        with patch.object(host, "job_source", side_effect=OSError("Fixture interrupted copy")):
            with self.assertRaisesRegex(OSError, "interrupted copy"):
                host.prepare(self.root)
        first = self.image_setup.call_args.args[0]
        with patch.object(host, "job_source", wraps=original):
            host.prepare(self.root)
        second = self.image_setup.call_args.args[0]
        self.assertEqual(first[first.index("--output") + 1], second[second.index("--output") + 1])
        self.assertEqual(self.image_setup.call_count, 2)


if __name__ == "__main__":
    unittest.main()
