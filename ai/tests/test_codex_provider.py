from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embryo_harness import cli, codex, core
from embryo_harness.files import HarnessError, read_json, record, write_json


class CodexProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        panels = self.root / "ai/resources/panels"
        panels.mkdir(parents=True)
        write_json(panels / "index.json", {"T1:val": {"task": "T1"}})
        write_json(panels / "provenance.json", {
            "files": {"index.json": record(panels / "index.json")}})
        for name in ("competition", "common", "task"):
            path = self.root / f"ai/prompts/{name}.md"
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"Fixture {name} instructions.\n")
        (self.root / "datasets").mkdir()
        (self.root / "datasets/input.txt").write_text("Synthetic fixture.\n")
        self.config = {
            "version": 1, "task": "T1", "boards": ["T1:val"],
            "targets": {"T1:val": "fixture"}, "inputs": ["datasets/input.txt"],
            "prompt_files": ["ai/prompts/competition.md", "ai/prompts/common.md",
                             "ai/prompts/task.md"],
            "model": "test-model", "reasoning_effort": "high",
            "sandbox": "workspace-write", "network_access": False,
            "wall_time_seconds": 10, "seed": 42, "codex_executable": "fixture-codex",
        }
        self.config_path = self.root / "ai/config.json"
        write_json(self.config_path, self.config)
        self.runtime = {"path": "/fixture/codex", "version": "fixture"}

    def prepare(self):
        with patch.object(core, "libraries"), patch.object(core, "python_packages", return_value={}), \
                patch.object(codex, "probe", return_value=self.runtime):
            return core.prepare(self.root, self.config_path)

    def test_old_config_defaults_only_in_new_snapshot(self):
        original = self.config_path.read_bytes()
        old_run = self.root / "ai/runs/existing/frozen/config.json"
        old_run.parent.mkdir(parents=True)
        old_run.write_bytes(original)
        self.assertEqual(core.load_config(self.config_path, self.root)["codex_provider"], "openai")
        run = self.prepare()
        self.assertEqual(read_json(run / "frozen/config.json")["codex_provider"], "openai")
        self.assertEqual(read_json(run / "lock.json")["config"]["codex_provider"], "openai")
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertEqual(old_run.read_bytes(), original)

    def test_unsupported_provider_rejected_before_probe_or_run_creation(self):
        for provider in ("other", "", None, True, [], {}):
            with self.subTest(provider=provider), patch.object(codex, "probe") as probe:
                write_json(self.config_path, dict(self.config, codex_provider=provider))
                with self.assertRaisesRegex(HarnessError, "Unsupported codex_provider"):
                    core.load_config(self.config_path, self.root)
                probe.assert_not_called()
                self.assertFalse((self.root / "ai/runs").exists())
        write_json(self.config_path, dict(self.config, arbitrary_provider_option=True))
        with self.assertRaises(HarnessError):
            core.load_config(self.config_path, self.root)

    def test_openai_keeps_existing_argv(self):
        run = self.root / "run with spaces"
        expected = [
            "/fixture/codex", "--no-daemon", "-a", "never", "exec",
            "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--json", "--color", "never",
            "--model", "test-model", "--sandbox", "workspace-write",
            "-c", 'model_reasoning_effort="high"', "-c", "project_doc_max_bytes=0",
            "-c", 'web_search="disabled"', "-c", "features.multi_agent=false",
            "-c", "mcp_servers.codegraph.enabled=false",
            "-c", "sandbox_workspace_write.network_access=false",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            "--cd", str(run / "work"),
            "--output-schema", str(run / "frozen/final.schema.json"),
            "--output-last-message", str(run / "final.json"), "-",
        ]
        self.assertEqual(codex.command(run, self.config, self.runtime), expected)
        self.assertEqual(codex.command(run, dict(self.config, codex_provider="openai"),
                                       self.runtime), expected)

    def test_docker_provider_freezes_public_route_without_changing_permissions(self):
        write_json(self.config_path, dict(self.config, codex_provider="docker-sandbox-oauth"))
        run = self.prepare()
        argv = read_json(run / "frozen/command.json")
        settings = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-c"]
        expected = {
            'forced_login_method="api"', 'model_provider="sandboxd"',
            'model_providers.sandboxd.name="Sandbox Proxy"',
            'model_providers.sandboxd.base_url="https://chatgpt.com/backend-api/codex"',
            'model_providers.sandboxd.experimental_bearer_token="oai-oat01-proxy-managed"',
            'model_providers.sandboxd.requires_openai_auth=false',
        }
        self.assertTrue(expected.issubset(settings))
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
        self.assertIn("sandbox_workspace_write.network_access=false", settings)
        self.assertEqual(argv[-1], "-")
        frozen = read_json(run / "frozen/config.json")
        self.assertEqual(frozen["codex_provider"], "docker-sandbox-oauth")
        core.verify(run, inputs=False)

    def test_smoke_adapter_uses_provider_and_retains_route(self):
        def execute(argv, prompt, run, seconds):
            self.assertEqual(seconds, 120)
            self.assertTrue(prompt.is_file())
            self.assertIn('model_provider="sandboxd"', argv)
            (run / "work/smoke.txt").write_text("HARNESS_OK\n")
            write_json(run / "final.json", {"ok": True})
            (run / "trajectory.jsonl").write_text(json.dumps({"type": "turn.completed"}) + "\n")
            return 0, False

        with patch.object(codex, "probe", return_value=self.runtime) as probe, \
                patch.object(codex, "execute", side_effect=execute) as execute_mock:
            result = codex.smoke_check(self.root, "test-model", "fixture-codex",
                                       provider="docker-sandbox-oauth")
        probe.assert_called_once_with("fixture-codex")
        execute_mock.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["codex_provider"], "docker-sandbox-oauth")
        run = Path(result["directory"])
        self.assertEqual(read_json(run / "result.json"), result)
        self.assertIn('model_provider="sandboxd"', read_json(run / "frozen/command.json"))

    def test_invalid_smoke_provider_does_not_probe(self):
        with patch.object(codex, "probe") as probe:
            with self.assertRaisesRegex(HarnessError, "Unsupported codex_provider"):
                codex.smoke_check(self.root, "test-model", provider="other")
        probe.assert_not_called()
        self.assertFalse((self.root / "ai/checks").exists())

    def test_cli_propagates_explicit_provider_and_preserves_default(self):
        for options, provider in (([], "openai"),
                                  (["--provider", "docker-sandbox-oauth"], "docker-sandbox-oauth")):
            with self.subTest(provider=provider), \
                    patch.object(codex, "smoke_check", return_value={"ok": True}) as smoke, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["codex-check", "--model", "test-model", *options]), 0)
                smoke.assert_called_once_with(cli.ROOT, "test-model", "codex", provider=provider)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(["codex-check", "--model", "test-model", "--provider", "other"])


if __name__ == "__main__":
    unittest.main()
