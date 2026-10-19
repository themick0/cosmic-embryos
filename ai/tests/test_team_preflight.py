"""Exercise host dispatch with real local probe processes and a fake sbx boundary."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import sandbox_setup, team_preflight, team_protocol


class TeamPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cosmic team spaces ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("AGENTS.md", "ai/harness.py", "ai/README.md", "ai/PLAN.md", "ai/START.md",
                     "ai/prompts/competition.md", "ai/prompts/common.md"):
            self.write(name, b"Fixture source\n")
        self.write("ai/embryo_harness/team_protocol.py", Path(team_protocol.__file__).read_bytes())
        sandbox_setup.prepare(self.root)
        self.base = sandbox_setup.bootstrap_path(self.root)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_install_preserves_existing_sources_and_bootstrap_manifest(self):
        manifest = (self.base / "control/manifest.json").read_bytes()
        unrelated = self.write("ai/campaign/bootstrap/workspaces/worker-a/ai/operator.local.md", b"User work")
        probes = team_preflight.install(self.root)
        self.assertEqual(team_preflight.install(self.root), probes)
        self.assertEqual((self.base / "control/manifest.json").read_bytes(), manifest)
        self.assertEqual(unrelated.read_bytes(), b"User work")
        self.assertEqual(len(list((self.base / "control/preflight-deployments").glob("*.json"))), 1)
        for probe in probes.values():
            self.assertTrue(probe.is_file())
            self.assertFalse(probe.stat().st_mode & 0o222)

    def test_modified_probe_release_is_rejected_without_overwrite(self):
        probes = team_preflight.install(self.root)
        probe = probes["coordinator"]
        probe.chmod(0o644)
        probe.write_bytes(b"Changed by another process")
        with self.assertRaisesRegex(ValueError, "differs"):
            team_preflight.install(self.root)
        self.assertEqual(probe.read_bytes(), b"Changed by another process")

    def test_symlink_runtime_path_is_rejected(self):
        target = self.root / "unrelated"
        target.mkdir()
        (self.base / "workspaces/coordinator/ai/campaign").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            team_preflight.install(self.root)
        self.assertEqual(list(target.iterdir()), [])

    def test_linux_host_check_has_no_side_effects(self):
        with patch.object(team_preflight.platform, "system", return_value="Linux"), \
                patch.object(team_preflight, "install") as install, \
                patch.object(team_preflight.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "Mac host"):
                team_preflight.check(self.root, ["coordinator"])
        install.assert_not_called()
        run.assert_not_called()

    def test_real_probe_round_trip_through_fake_sbx(self):
        actual_run = subprocess.run
        seen = []
        tools = self.root / "fake-tools"
        tools.mkdir()
        for name in ("codex", "docker"):
            path = tools / name
            path.write_text("#!/bin/sh\nexit 99\n")
            path.chmod(0o755)
        actual_platform = platform.system()

        def fake_sbx(argv, **kwargs):
            self.assertEqual(argv[1:3], ["exec", "-w"])
            self.assertEqual(argv[5], "python3")
            self.assertEqual(kwargs["timeout"], 45)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            seen.append(argv[4])
            return actual_run([sys.executable, *argv[6:]], cwd=argv[3],
                              env={"PATH": str(tools)}, **kwargs)

        with patch.object(team_preflight.platform, "system", return_value="Darwin"), \
                patch.object(team_preflight.shutil, "which", return_value="/fake/sbx"), \
                patch.object(team_preflight.subprocess, "run", side_effect=fake_sbx):
            report = team_preflight.check(self.root, list(team_preflight.ROLE_NAMES))
        self.assertEqual(seen, ["cosmic-coordinator", "cosmic-worker-a", "cosmic-uploader"])
        self.assertTrue(report["communication_ok"])
        self.assertEqual(report["environment_checks_ok"], actual_platform == "Linux")
        self.assertFalse(report["runtime_ready"])
        self.assertEqual(report["model_authentication"], "not_tested")
        self.assertEqual(team_preflight.latest(self.root)["id"], report["id"])
        self.assertTrue(Path(report["report_path"]).is_file())

    def test_timeout_is_retained_and_does_not_retry(self):
        with patch.object(team_preflight.platform, "system", return_value="Darwin"), \
                patch.object(team_preflight.shutil, "which", return_value="/fake/sbx"), \
                patch.object(team_preflight.subprocess, "run", side_effect=subprocess.TimeoutExpired("sbx", 45)) as run:
            report = team_preflight.check(self.root, ["coordinator"])
        self.assertEqual(run.call_count, 1)
        self.assertFalse(report["communication_ok"])
        self.assertIn("Timed out", report["roles"][0]["error"])
        self.assertTrue(Path(report["report_path"]).is_file())

    def test_reply_binding_checks_request_and_code_hashes(self):
        request = {"version": 1, "id": "a" * 32, "nonce": "b" * 32,
                   "role": "worker-a", "workspace": "/workspace", "probe_sha256": "c" * 64}
        response = {**request, "kind": "preflight_result", "runtime_ready": False,
                    "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                                                 ensure_ascii=False).encode()).hexdigest(),
                    "checks": dict.fromkeys(("competition_rules", "common_prompt", "harness", "codex_available", "docker_available"), True),
                    "python_version": "3.11.0", "platform": "Linux"}
        team_preflight.expected_reply(response, request)
        for key in ("nonce", "id", "role", "workspace", "probe_sha256", "request_sha256"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                team_preflight.expected_reply({**response, key: "wrong"}, request)


if __name__ == "__main__":
    unittest.main()
