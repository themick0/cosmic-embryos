"""Test scorer provenance admission and retained synthetic-check outcomes."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import pilot_checks
from embryo_harness.files import HarnessError


class PilotChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cosmic pilot checks ")
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)

    def distribution(self, version=pilot_checks.SCORER_VERSION, direct=None):
        package = Mock()
        package.version = version
        package.read_text.return_value = json.dumps(direct if direct is not None else {
            "url": pilot_checks.SCORER_URL,
            "vcs_info": {"vcs": "git", "commit_id": pilot_checks.SCORER_COMMIT},
        })
        return package

    def result(self):
        return {"meta": {"task": "T2", "setting": "embryo",
                         "reference_defaulted_to_input": False},
                "metrics": {key: 0.5 for key in pilot_checks.RANKING_METRICS}}

    def test_provenance_rejections_happen_before_scorer_execution(self):
        cases = [self.distribution(version="0.1.0"), self.distribution(direct={}),
                 self.distribution(direct={"url": pilot_checks.SCORER_URL,
                                           "vcs_info": {"vcs": "git", "commit_id": "wrong"}}),
                 self.distribution(direct={"url": "https://example.com/other.git",
                                           "vcs_info": {"vcs": "git", "commit_id": pilot_checks.SCORER_COMMIT}})]
        for index, package in enumerate(cases):
            with self.subTest(index=index), \
                    patch.object(pilot_checks.importlib.metadata, "distribution", return_value=package), \
                    patch.object(pilot_checks, "_score_synthetic") as score:
                target = self.output / str(index)
                with self.assertRaises(HarnessError):
                    pilot_checks.check_scorer(target)
                score.assert_not_called()
                report = json.loads((target / "scorer-check/report.json").read_text())
                self.assertFalse(report["ok"])
                self.assertEqual(report["model_calls"], 0)

    def test_invalid_provenance_json_is_rejected(self):
        package = self.distribution()
        package.read_text.return_value = "not-json"
        with patch.object(pilot_checks.importlib.metadata, "distribution", return_value=package), \
                self.assertRaisesRegex(HarnessError, "provenance"):
            pilot_checks._provenance()

    def test_nonfinite_missing_and_boolean_metrics_fail(self):
        for value in (None, float("nan"), float("inf"), True):
            result = self.result()
            result["metrics"]["de_score"] = value
            with self.subTest(value=value), self.assertRaisesRegex(HarnessError, "de_score"):
                pilot_checks._validate_result(result)
        result = self.result()
        del result["metrics"]["mmd_u"]
        with self.assertRaisesRegex(HarnessError, "mmd_u"):
            pilot_checks._validate_result(result)

    def test_default_reference_is_rejected(self):
        result = self.result()
        result["meta"]["reference_defaulted_to_input"] = True
        with self.assertRaisesRegex(HarnessError, "reference"):
            pilot_checks._validate_result(result)

    def test_success_retains_report_and_refuses_evidence_overwrite(self):
        with patch.object(pilot_checks.importlib.metadata, "distribution", return_value=self.distribution()), \
                patch.object(pilot_checks.importlib.metadata, "distributions", return_value=[]), \
                patch.object(pilot_checks, "_score_synthetic", return_value={"result": self.result()}):
            result = pilot_checks.check_scorer(self.output)
            self.assertTrue(result["ok"])
            path = Path(result["report_path"])
            original = path.read_bytes()
            self.assertEqual(json.loads(original), result)
            with self.assertRaises(FileExistsError):
                pilot_checks.check_scorer(self.output)
            self.assertEqual(path.read_bytes(), original)

    def test_scorer_exception_records_failure_and_is_not_retried(self):
        with patch.object(pilot_checks.importlib.metadata, "distribution", return_value=self.distribution()), \
                patch.object(pilot_checks.importlib.metadata, "distributions", return_value=[]), \
                patch.object(pilot_checks, "_score_synthetic", side_effect=RuntimeError("synthetic failure")) as score:
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                pilot_checks.check_scorer(self.output)
            score.assert_called_once()
        result = json.loads((self.output / "scorer-check/report.json").read_text())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
