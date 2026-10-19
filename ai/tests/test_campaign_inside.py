from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import anndata as ad
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import campaign_inside as inside, codex, core, submissions
from embryo_harness.files import HarnessError, read_json, record, write_json


class CampaignInsideTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="inside pilot ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "research"
        self.home = self.directory / "fresh-home"
        self.home.mkdir()
        self.report = self.directory / "report/result.json"
        self.report.parent.mkdir()
        self.ai = self.root / "ai"
        for name in ("checks", "runs", "exports", "configs", "prompts", "resources/panels"):
            (self.ai / name).mkdir(parents=True)
        for name in ("competition", "common", "t2_pilot"):
            (self.ai / "prompts" / (name + ".md")).write_text("Synthetic test fixture instructions.\n")
        (self.ai / "harness.py").write_text("# Synthetic harness fixture, never a competition entry.\n")
        panels = self.ai / "resources/panels"
        write_json(panels / "index.json", {"T2:embryo:val_interp": {
            "task": "T2", "n_genes": 2, "min_cells": 2, "max_cells": 4,
            "needs_coords": True, "genes_file": "genes.txt"}})
        (panels / "genes.txt").write_text("GeneA\nGeneB\n")
        write_json(panels / "provenance.json", {"files": {
            name: record(panels / name) for name in ("index.json", "genes.txt")}})
        self.config_path = self.ai / "configs/t2_pilot.json"
        self.config = read_json(Path(__file__).resolve().parents[1] / "configs/t2_pilot.json")
        write_json(self.config_path, self.config)
        (self.root / "datasets").mkdir()
        data = ad.AnnData(X=np.ones((3, 2), dtype="float32"))
        data.var_names = ["GeneA", "GeneB"]
        data.obs_names = ["fixture_a", "fixture_b", "fixture_c"]
        data.obsm["spatial_3D"] = np.array([[0, 0, 0], [1, 1, 1], [2, 0, 1]], dtype="float32")
        for path in inside.PILOT_INPUTS:
            data.write_h5ad(self.root / path)
        self.usage = {"input_tokens": 11, "cached_input_tokens": 3, "output_tokens": 5}
        self.mode = "success"
        self.runtime = {"path": "/mock/codex", "version": "test-fixture",
                        "entrypoint": {"bytes": 1, "sha256": "a" * 64}}
        self.patches = []
        self.addCleanup(self._stop_patches)
        self._patch(patch.dict(os.environ, {}, clear=True))
        self._patch(patch.object(Path, "home", return_value=self.home))
        self._patch(patch.object(inside, "ROOT", self.root))
        self._patch(patch.object(inside, "RESULT_PATH", self.report))
        self.probe = self._patch(patch.object(codex, "probe", return_value=self.runtime))
        self.model_call = self._patch(patch.object(codex, "execute", side_effect=self._fake_model))
        self._patch(patch.object(core, "python_packages", return_value={"fixture": "1"}))
        self.upload = self._patch(patch.object(submissions, "submit", side_effect=AssertionError("No uploads")))
        self.format_upload = self._patch(patch.object(submissions, "format_check", side_effect=AssertionError("No uploads")))
        self._patch(patch("socket.create_connection", side_effect=AssertionError("No network in tests")))

    def _patch(self, patcher):
        self.patches.append(patcher)
        return patcher.start()

    def _stop_patches(self):
        self.upload.assert_not_called()
        self.format_upload.assert_not_called()
        for patcher in reversed(self.patches):
            patcher.stop()

    def _fake_model(self, argv, prompt, run, seconds):
        self.assertIn("model_provider=\"sandboxd\"", argv)
        self.assertIn("sandbox_workspace_write.network_access=false", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
        self.assertTrue(prompt.is_file())
        events = [{"type": "thread.started", "thread_id": "synthetic-fixture"},
                  {"type": "turn.completed", "usage": self.usage}]
        (run / "trajectory.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
        (run / "stderr.log").write_text("")
        if run.parent.name == "checks":
            self.assertEqual(seconds, 120)
            (run / "work/smoke.txt").write_text("HARNESS_OK\n" if self.mode == "success" else "WRONG\n")
            write_json(run / "final.json", {"ok": True})
        else:
            self.assertEqual(seconds, 1800)
            self.assertTrue((run / "started.json").exists())
            context = read_json(run / "work/context.json")
            self.assertFalse(context["official_submissions_allowed"])
            self.assertEqual([item["path"] for item in context["inputs"]],
                             [str(self.root / path) for path in inside.PILOT_INPUTS])
            work = run / "work"
            shutil.copyfile(self.root / inside.PILOT_INPUTS[0], work / "prediction.h5ad")
            (work / "method.md").write_text("Synthetic test output, never an official submission.\n")
            (work / "sources.json").write_text("[]\n")
            (work / "reproduce.py").write_text("# Synthetic test reproduction fixture.\n")
            write_json(run / "final.json", {"status": "completed", "summary": "fixture",
                       "predictions": [{"board": "T2:embryo:val_interp", "path": "prediction.h5ad"}],
                       "method_summary": "method.md", "sources": "sources.json",
                       "reproduction_files": ["reproduce.py"]})
            if self.mode == "alter-frozen":
                with (run / "frozen/config.json").open("a") as stream:
                    stream.write("\n")
        if self.mode == "model-failure":
            with (run / "trajectory.jsonl").open("a") as stream:
                stream.write('{"type":"turn.failed"}\n')
            return 2, False
        return 0, False

    def execute(self, kind):
        return inside.execute(kind, "gpt-6-astra", 120 if kind == "smoke" else 1800, self.report)

    def test_smoke_uses_real_adapter_and_preserves_usage_without_research(self):
        result = self.execute("smoke")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["usage"], self.usage)
        self.assertTrue(result["usage_complete"])
        self.assertFalse(result["official_uploads"])
        self.assertEqual(list((self.ai / "runs").iterdir()), [])
        check = Path(result["check_directory"])
        self.assertEqual(read_json(check / "result.json")["codex_provider"], "docker-sandbox-oauth")
        self.assertEqual(read_json(self.report), result)
        auth = self.home / ".codex/auth.json"
        self.assertEqual(read_json(auth), {"OPENAI_API_KEY": "proxy-managed"})
        self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o600)
        self.model_call.assert_called_once()

    def test_failed_smoke_preserves_observed_usage_without_claiming_completeness(self):
        self.mode = "model-failure"
        with self.assertRaises(HarnessError):
            self.execute("smoke")
        result = read_json(self.report)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["usage"], self.usage)
        self.assertFalse(result["usage_complete"])
        self.assertTrue((Path(result["check_directory"]) / "trajectory.jsonl").exists())
        self.assertFalse(result["official_uploads"])

    def test_smoke_failure_before_any_trace_keeps_usage_unknown(self):
        self.probe.side_effect = HarnessError("Unsupported local executable")
        with self.assertRaises(HarnessError):
            self.execute("smoke")
        self.assertIsNone(read_json(self.report)["usage"])
        self.model_call.assert_not_called()

    def test_research_runs_real_evidence_lifecycle_with_only_model_mocked(self):
        source_before = {str(path): record(path) for path in core.source_files(self.ai)}
        with patch.object(core, "prepare", wraps=core.prepare) as prepare, \
                patch.object(core, "execute", wraps=core.execute) as execute, \
                patch.object(core, "verify", wraps=core.verify) as verify, \
                patch.object(core, "bundle", wraps=core.bundle) as bundle:
            result = self.execute("research")
        run, export = Path(result["run_directory"]), Path(result["bundle_directory"])
        prepare.assert_called_once_with(self.root, self.config_path)
        execute.assert_called_once_with(run)
        bundle.assert_called_once_with(run)
        self.assertGreaterEqual(verify.call_count, 3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["usage"], self.usage)
        self.assertTrue(result["usage_complete"])
        self.assertEqual(result["evidence"], {"lock": record(run / "lock.json"),
                                             "receipt": record(run / "receipt.json"),
                                             "manifest": record(export / "manifest.json")})
        self.assertEqual(source_before, {str(path): record(path) for path in core.source_files(self.ai)})
        self.assertEqual(record(export / "prediction-1.h5ad"), record(run / "work/prediction.h5ad"))
        self.assertEqual(core.verify(run)["config"]["codex_provider"], "docker-sandbox-oauth")
        manifest = read_json(export / "manifest.json")
        self.assertFalse(manifest["uploaded"])
        self.assertEqual(set(manifest["evidence"]), {"trajectory.tar.gz", "harness.tar.gz", "prompts.tar.gz"})
        for name, expected in manifest["evidence"].items():
            self.assertEqual(record(export / name), expected)
            with tarfile.open(export / name) as archive:
                self.assertFalse(any("auth.json" in item for item in archive.getnames()))
        self.model_call.assert_called_once()

    def test_frozen_mutation_fails_without_bundle_and_retains_observed_usage(self):
        self.mode = "alter-frozen"
        with patch.object(core, "bundle", wraps=core.bundle) as bundle:
            with self.assertRaisesRegex(HarnessError, "Integrity check"):
                self.execute("research")
            bundle.assert_not_called()
        result = read_json(self.report)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["usage"], self.usage)
        self.assertFalse(result["usage_complete"])
        self.assertEqual(list((self.ai / "exports").iterdir()), [])
        self.assertTrue((Path(result["run_directory"]) / "trajectory.jsonl").is_file())

    def test_bundle_failure_keeps_usage_from_completed_model_call(self):
        with patch.object(core, "bundle", side_effect=HarnessError("Simulated packaging failure")):
            with self.assertRaises(HarnessError):
                self.execute("research")
        result = read_json(self.report)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["usage"], self.usage)
        self.assertTrue(result["usage_complete"])
        self.assertTrue((Path(result["run_directory"]) / "receipt.sha256").is_file())

    def test_invalid_pilot_config_rejected_before_auth_or_model(self):
        changes = ({"codex_provider": "openai"}, {"codex_provider": "unknown"},
                   {"wall_time_seconds": 120}, {"model": "other"}, {"reasoning_effort": "low"},
                   {"sandbox": "danger-full-access", "network_access": True}, {"network_access": True},
                   {"inputs": inside.PILOT_INPUTS[:2]}, {"codex_executable": "/other/codex"})
        for changed in changes:
            with self.subTest(changed=changed):
                write_json(self.config_path, dict(self.config, **changed))
                with self.assertRaises((ValueError, HarnessError)):
                    self.execute("research")
                self.assertEqual(read_json(self.report)["status"], "failed")
                self.assertFalse((self.home / ".codex/auth.json").exists())
                self.probe.assert_not_called()
                self.model_call.assert_not_called()
                self.report.unlink()

    def test_invalid_kind_model_bound_and_result_path_touch_no_runtime(self):
        for arguments in (("other", "gpt-6-astra", 120), ("smoke", "other", 120),
                          ("smoke", "gpt-6-astra", 121), ("research", "gpt-6-astra", 1799),
                          ("research", "gpt-6-astra", 1800.0), ("smoke", "gpt-6-astra", True)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                inside.execute(*arguments, self.report)
        with self.assertRaises(ValueError):
            inside.execute("smoke", "gpt-6-astra", 120, self.directory / "wrong.json")
        self.assertFalse(self.report.exists())
        self.assertFalse((self.home / ".codex").exists())
        self.model_call.assert_not_called()

    def test_preexisting_auth_is_never_read_or_overwritten(self):
        auth = self.home / ".codex/auth.json"
        auth.parent.mkdir()
        auth.write_text("opaque test fixture")
        with patch.object(Path, "read_text", side_effect=AssertionError("Existing auth must not be read")):
            with self.assertRaises(FileExistsError):
                self.execute("smoke")
        self.assertEqual(auth.read_text(), "opaque test fixture")
        self.assertEqual(read_json(self.report)["status"], "failed")
        self.model_call.assert_not_called()

    def test_repeating_entry_point_cannot_make_second_model_call(self):
        self.execute("smoke")
        original_report = record(self.report)
        with self.assertRaises(FileExistsError):
            self.execute("smoke")
        self.assertEqual(record(self.report), original_report)
        self.model_call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
