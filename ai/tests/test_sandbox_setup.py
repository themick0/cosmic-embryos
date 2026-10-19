from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import sandbox_setup as setup


class SandboxSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cosmic setup spaces ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("AGENTS.md", "ai/harness.py", "ai/README.md", "ai/PLAN.md",
                     "ai/START.md", "ai/prompts/competition.md", "ai/prompts/common.md",
                     "ai/embryo_harness/core.py", "ai/configs/t2.json", "ai/resources/panels/index.json",
                     "ai/mcp/codegraph/README.md"):
            self.write(name, "Allowed source\n")

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def test_prepare_excludes_secrets_runtime_datasets_and_symlinks(self):
        excluded = ("ai/.env.local", "ai/.env", "ai/configs/t1.local.json",
                    "ai/configs/secret.local", "ai/configs/.env", "ai/configs/.git/config",
                    "ai/.venv/private.py", "ai/runs/old/final.json", "datasets/input.h5ad",
                    "ai/checks/old/result.json", "ai/submissions/receipt.json", ".git/config",
                    "ai/embryo_harness/runs/private.py", "ai/resources/model.h5ad",
                    "ai/bootstrap/private.py", "ai/campaign/private.json",
                    "ai/mcp/codegraph/configure.py", "ai/mcp/codegraph/package.json")
        for name in excluded:
            self.write(name, "MUST_NOT_COPY")
        (self.root / "ai/embryo_harness/linked.py").symlink_to(self.root / "ai/.env.local")
        (self.root / "ai/configs/linked_dir").symlink_to(self.root / "ai/campaign", target_is_directory=True)
        setup.prepare(self.root)
        destination = setup.bootstrap_path(self.root)
        manifest = json.loads((destination / "control/manifest.json").read_text())
        self.assertFalse(manifest["campaign_runtime_ready"])
        self.assertNotIn("ai/.env.local", manifest["sources"])
        for role in manifest["roles"]:
            workspace = Path(role["workspace"])
            self.assertEqual((workspace / "ai/PLAN.md").read_text(), "Allowed source\n")
            self.assertTrue((workspace / "ai/mcp/codegraph/README.md").is_file())
            for path in workspace.rglob("*"):
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertNotIn("MUST_NOT_COPY", path.read_text())
            for name in excluded:
                self.assertFalse((workspace / name).exists(), name)
        self.assertFalse((destination / "control/manifest.json").stat().st_mode & 0o222)
        self.assertFalse(list(destination.parent.glob(".bootstrap-staging-*")))
        self.assertFalse((destination.parent / ".bootstrap.prepare.lock").exists())

    def test_prepare_refuses_existing_bootstrap_without_changes(self):
        setup.prepare(self.root)
        manifest = setup.bootstrap_path(self.root) / "control/manifest.json"
        original = manifest.read_bytes()
        with self.assertRaisesRegex(setup.SetupError, "already exists"):
            setup.prepare(self.root)
        self.assertEqual(manifest.read_bytes(), original)

    def test_commands_use_final_paths_exact_mounts_and_shell_quoting(self):
        setup.prepare(self.root)
        destination = setup.bootstrap_path(self.root)
        commands = json.loads((destination / "control/commands.json").read_text())["commands"]
        lines = setup.command_lines(self.root)
        for row, line, (role, name, cpus, memory) in zip(commands, lines[1:], setup.ROLES):
            expected = ["sbx", "create", "--name", name, "--cpus", str(cpus), "--memory", memory,
                        "--skills", "off", "codex", str(destination / "workspaces" / role)]
            expected.extend([str(destination / "mailboxes")] if role == "coordinator" else
                            [str(destination / "mailboxes" / role / "inbox") + ":ro",
                             str(destination / "mailboxes" / role / "outbox")])
            self.assertEqual(row["argv"], expected)
            self.assertEqual(shlex.split(line), expected)
            self.assertIn("'", line)
            self.assertNotIn(".bootstrap-staging-", line)
            self.assertNotIn(str(destination / "control"), row["argv"])

    def test_prepare_failure_does_not_publish_partial_bootstrap(self):
        with patch.object(setup, "_copy_regular", side_effect=OSError("simulated copy failure")):
            with self.assertRaisesRegex(OSError, "simulated"):
                setup.prepare(self.root)
        destination = setup.bootstrap_path(self.root)
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_destination_symlink_is_rejected(self):
        outside = self.root / "unrelated"
        outside.mkdir()
        (self.root / "ai/campaign").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(setup.SetupError, "symlink"):
            setup.prepare(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_codegraph_readme_with_symlink_parent_is_excluded(self):
        original = self.root / "ai/mcp/codegraph"
        replacement = self.root / "other-codegraph"
        original.rename(replacement)
        original.symlink_to(replacement, target_is_directory=True)
        self.assertNotIn(original / "README.md", list(setup.source_files(self.root)))

    def test_harness_snapshot_contains_setup_implementation_and_tests(self):
        from embryo_harness import core
        root = Path(setup.__file__).resolve().parents[2]
        files = {str(p.relative_to(root / "ai")) for p in core.source_files(root / "ai")}
        self.assertIn("embryo_harness/sandbox_setup.py", files)
        self.assertIn("tests/test_sandbox_setup.py", files)

    def test_commands_reject_tampering(self):
        setup.prepare(self.root)
        path = setup.bootstrap_path(self.root) / "control/commands.json"
        path.chmod(0o644)
        path.write_text("{}")
        with self.assertRaisesRegex(setup.SetupError, "manifest"):
            setup.command_lines(self.root)

    def test_linux_doctor_never_executes_or_looks_for_cli(self):
        with patch.object(setup.platform, "system", return_value="Linux"), \
                patch.object(setup.subprocess, "run") as run, patch.object(setup.shutil, "which") as which:
            result = setup.doctor()
        self.assertFalse(result["ok"])
        self.assertIn("macOS Terminal", result["message"])
        run.assert_not_called()
        which.assert_not_called()

    def test_mac_doctor_only_runs_bounded_read_checks_and_sanitizes_output(self):
        replies = [subprocess.CompletedProcess([], 0, "sbx version 0.12.3\nSECRET_VALUE", "SECRET_VALUE"),
                   subprocess.CompletedProcess([], 0, "--name string\n--cpus int\n--memory size\n--skills mode\n", "")]
        with patch.object(setup.platform, "system", return_value="Darwin"), \
                patch.object(setup.shutil, "which", return_value="/test/bin/sbx"), \
                patch.object(setup.subprocess, "run", side_effect=replies) as run:
            result = setup.doctor()
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "sbx 0.12.3")
        self.assertNotIn("SECRET_VALUE", json.dumps(result))
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [["/test/bin/sbx", "version"], ["/test/bin/sbx", "create", "--help"]])
        self.assertTrue(all(call.kwargs["timeout"] == 10 for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
