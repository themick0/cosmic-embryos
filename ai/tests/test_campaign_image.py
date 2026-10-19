from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embryo_harness import campaign_image
from embryo_harness.files import HarnessError


class CampaignImageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.release = self.root / "release with spaces"
        self.output = self.root / "image output"
        (self.release / "ai/resources/pilot").mkdir(parents=True)
        (self.release / "ai/embryo_harness").mkdir()
        (self.release / "ai/resources/pilot/Dockerfile").write_text(
            "FROM fixture/image@sha256:" + "a" * 64 + "\nCOPY trust.pem /opt/trust.pem\n")
        (self.release / "ai/resources/pilot/seccomp.json").write_bytes(
            (Path(__file__).resolve().parents[1] / "resources/pilot/seccomp.json").read_bytes())
        (self.release / "ai/pyproject.toml").write_text("[project]\nname = 'fixture'\n")
        (self.release / "ai/uv.lock").write_text("version = 1\n")
        (self.release / "ai/embryo_harness/pilot_checks.py").write_text(
            "def check_scorer(path): return {'ok': True}\n")
        (self.release / "ai/embryo_harness/campaign_image.py").write_text("# Fixture runtime probe.\n")
        (self.release / "ai/.env.local").write_text("SECRET_FIXTURE=never-copy\n")
        (self.release / "datasets").mkdir()
        (self.release / "datasets/private.h5ad").write_text("never-copy\n")
        self.trust = self.root / "public-ca.pem"
        self.trust.write_text("PUBLIC FIXTURE CA BUNDLE\n")
        self.image_id = "sha256:" + "b" * 64
        self.calls = []
        self.failure = None
        self.timeout = None
        self.check_ok = True
        self.runtime_ok = True
        self.addCleanup(patch.stopall)
        patch.object(campaign_image, "TRUST_FILE", self.trust).start()
        patch.dict("os.environ", {"HTTP_PROXY": "http://proxy.example:3128",
                                  "HTTPS_PROXY": "http://proxy.example:3128"}).start()
        self.run = patch.object(campaign_image.subprocess, "run", side_effect=self.fake_run).start()

    def fake_run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        action = argv[1]
        if action == self.timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if action == self.failure:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="fixture failure")
        if action == "build":
            context = Path(argv[-1])
            self.assertEqual(set(p.name for p in context.iterdir()),
                             {"Dockerfile", "pyproject.toml", "uv.lock", "trust.pem"})
            self.assertNotIn(b"SECRET_FIXTURE", b"".join(p.read_bytes() for p in context.iterdir()))
            Path(argv[argv.index("--iidfile") + 1]).write_text(self.image_id + "\n")
        elif action == "image":
            return subprocess.CompletedProcess(argv, 0, stdout=self.image_id + "\n", stderr="")
        elif action == "run":
            mount = next(arg for arg in argv if "dst=/checks" in arg)
            checks = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
            (checks / "result.json").write_text(json.dumps({"ok": self.check_ok, "synthetic": True}))
            (checks / "requirements-resolved.txt").write_text("fixture-library==1.0\n")
            (checks / "runtime.json").write_text(json.dumps({
                "ok": self.runtime_ok, "codex_version": campaign_image.CODEX_VERSION,
                "prefix_supported": True, "workspace_write": True,
                "outside_write_denied": True, "tmp_write_denied": True, "network_denied": True,
                "seccomp_profile_sha256": campaign_image.SECCOMP_SHA256}))
        elif action != "rm":
            raise AssertionError(f"Unexpected Docker action: {action}")
        return subprocess.CompletedProcess(argv, 0)

    def test_minimal_context_pinned_identity_and_offline_resource_bounded_check(self):
        result = campaign_image.build(self.release, self.output)
        self.assertEqual(result["image_id"], self.image_id)
        self.assertEqual(result["base_reference"], "fixture/image@sha256:" + "a" * 64)
        self.assertFalse(result["model_calls"])
        self.assertEqual(json.loads((self.output / "image.json").read_text()), result)
        self.assertEqual(Path(result["requirements_file"]).read_text(), "fixture-library==1.0\n")
        build, inspect, check = self.calls
        self.assertEqual(build[1]["timeout"], 900)
        self.assertEqual(inspect[1]["timeout"], 30)
        self.assertEqual(check[1]["timeout"], 300)
        self.assertNotIn("http://proxy.example:3128", build[0])
        self.assertEqual(build[0].count("--build-arg"), 2)
        self.assertTrue(Path(build[0][build[0].index("--iidfile") + 1]).is_absolute())
        for flag, expected in (("--network", "none"), ("--pids-limit", "512"),
                               ("--memory", "8g"), ("--cpus", "3")):
            self.assertEqual(check[0][check[0].index(flag) + 1], expected)
        self.assertIn(f"type=bind,src={self.release / 'ai'},dst=/research/ai,readonly", check[0])
        self.assertIn("PYTHONPATH=/research/ai", check[0])
        self.assertIn("--read-only", check[0])
        self.assertEqual(check[0][check[0].index("--cap-drop") + 1], "ALL")
        self.assertEqual(check[0][check[0].index("--security-opt") + 1], "no-new-privileges")
        self.assertNotIn("--privileged", check[0])
        self.assertNotIn("seccomp=unconfined", check[0])
        self.assertIn("seccomp=" + str(self.output / "seccomp.json"), check[0])
        self.assertEqual(result["seccomp_profile_record"]["sha256"], campaign_image.SECCOMP_SHA256)
        self.assertNotIn("HOME=", " ".join(check[0]))
        self.assertNotIn("codex", " ".join(check[0]))
        for argv, kwargs in self.calls:
            self.assertIsInstance(argv, list)
            self.assertFalse(kwargs.get("shell", False))

    def test_recipe_hash_is_deterministic_and_changes_with_dependency_input(self):
        first = campaign_image.build(self.release, self.output)
        second = campaign_image.build(self.release, self.root / "same recipe")
        self.assertEqual(first["recipe_sha256"], second["recipe_sha256"])
        self.assertEqual(first["tag"], second["tag"])
        (self.release / "ai/uv.lock").write_text("version = 2\n")
        third = campaign_image.build(self.release, self.root / "different recipe")
        self.assertNotEqual(first["recipe_sha256"], third["recipe_sha256"])

    def test_reuse_verifies_image_and_artifacts_without_rebuilding_or_checking_again(self):
        first = campaign_image.build(self.release, self.output)
        self.calls.clear()
        self.assertEqual(campaign_image.build(self.release, self.output), first)
        self.assertEqual([argv[1] for argv, _ in self.calls], ["image"])
        Path(first["setup_check_result"]).write_text('{"ok":false}')
        self.calls.clear()
        with self.assertRaisesRegex(HarnessError, "artifacts changed"):
            campaign_image.build(self.release, self.output)
        self.assertFalse(self.calls)

    def test_reuse_rejects_missing_image_and_changed_recipe(self):
        campaign_image.build(self.release, self.output)
        self.failure = "image"
        with self.assertRaisesRegex(HarnessError, "unavailable"):
            campaign_image.build(self.release, self.output)
        self.failure = None
        (self.release / "ai/uv.lock").write_text("changed\n")
        self.calls.clear()
        with self.assertRaisesRegex(HarnessError, "different recipe"):
            campaign_image.build(self.release, self.output)
        self.assertFalse(self.calls)

    def test_proxy_credentials_are_rejected_without_disclosing_value_or_running_docker(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://username:secret-fixture@proxy.example"}):
            with self.assertRaises(HarnessError) as caught:
                campaign_image.build(self.release, self.output)
        self.assertNotIn("secret-fixture", str(caught.exception))
        self.assertFalse(self.calls)
        self.assertFalse(self.output.exists())

    def test_symlink_inputs_output_and_parent_traversal_rejected(self):
        source = self.release / "ai/uv.lock"
        source.unlink()
        source.symlink_to(self.trust)
        with self.assertRaisesRegex(HarnessError, "symlinks"):
            campaign_image.build(self.release, self.output)
        source.unlink()
        source.write_text("fixture\n")
        self.output.symlink_to(self.root / "elsewhere")
        with self.assertRaisesRegex(HarnessError, "symlinks"):
            campaign_image.build(self.release, self.output)
        with self.assertRaisesRegex(HarnessError, "parent traversal"):
            campaign_image.build(self.release / ".." / self.release.name, self.root / "out")
        self.assertFalse(self.calls)

    def test_unpinned_base_rejected(self):
        (self.release / "ai/resources/pilot/Dockerfile").write_text("FROM fixture/image:latest\n")
        with self.assertRaisesRegex(HarnessError, "digest-pinned"):
            campaign_image.build(self.release, self.output)
        self.assertFalse(self.calls)

    def test_mount_separator_in_output_path_rejected(self):
        with self.assertRaisesRegex(HarnessError, "bind paths"):
            campaign_image.build(self.release, self.root / "invalid,output")
        self.assertFalse(self.calls)

    def test_failed_build_retained_without_automatic_retry(self):
        self.failure = "build"
        with self.assertRaisesRegex(HarnessError, "status 1"):
            campaign_image.build(self.release, self.output)
        self.assertFalse((self.output / "image.json").exists())
        self.assertTrue((self.output / "build.log").exists())
        self.calls.clear()
        with self.assertRaisesRegex(HarnessError, "Incomplete"):
            campaign_image.build(self.release, self.output)
        self.assertFalse(self.calls)

    def test_check_timeout_cleans_up_only_its_own_container(self):
        self.timeout = "run"
        with self.assertRaisesRegex(HarnessError, "300 seconds"):
            campaign_image.build(self.release, self.output)
        check = next(argv for argv, _ in self.calls if argv[1] == "run")
        cleanup = self.calls[-1][0]
        self.assertEqual(cleanup, ["docker", "rm", "--force", check[check.index("--name") + 1]])
        self.assertFalse((self.output / "image.json").exists())

    def test_false_check_result_does_not_publish_success_manifest(self):
        self.check_ok = False
        with self.assertRaisesRegex(HarnessError, "ok=true"):
            campaign_image.build(self.release, self.output)
        self.assertFalse((self.output / "image.json").exists())

    def test_false_runtime_check_does_not_publish_success_manifest(self):
        self.runtime_ok = False
        with self.assertRaisesRegex(HarnessError, "runtime isolation"):
            campaign_image.build(self.release, self.output)
        self.assertFalse((self.output / "image.json").exists())

    def test_full_prefix_rejected_before_any_sandbox_or_model_invocation(self):
        calls = []

        def probe(argv, **kwargs):
            calls.append(argv)
            if argv == ["codex", "--version"]:
                return subprocess.CompletedProcess(argv, 0, campaign_image.CODEX_VERSION, "")
            self.assertEqual(argv, ["codex", "--no-daemon", "-a", "never", "exec",
                                    "--ignore-user-config", "--ignore-rules", "--help"])
            return subprocess.CompletedProcess(argv, 2, "", "unsupported prefix")

        self.output.mkdir()
        with patch.object(campaign_image.subprocess, "run", side_effect=probe):
            with self.assertRaisesRegex(HarnessError, "prefix is unsupported"):
                campaign_image.check_runtime(self.output)
        self.assertEqual(len(calls), 2)
        self.assertFalse(json.loads((self.output / "runtime.json").read_text())["ok"])

    def test_runtime_sandbox_failure_is_retained_and_never_bypassed(self):
        calls = []
        self.output.mkdir()
        outside = self.output / "outside.txt"

        def probe(argv, **kwargs):
            calls.append(argv)
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, campaign_image.CODEX_VERSION, "")
            if "--help" in argv:
                return subprocess.CompletedProcess(argv, 0,
                    "--json --output-schema --ignore-user-config --ignore-rules", "")
            self.assertEqual(argv[:3], ["codex", "--no-daemon", "sandbox"])
            self.assertIn('permissions.cosmic-probe.extends=":workspace"', argv)
            self.assertIn('permissions.cosmic-probe.filesystem={":root"="read",":tmpdir"="read",":slash_tmp"="read"}', argv)
            self.assertIn("permissions.cosmic-probe.network.enabled=false", argv)
            self.assertEqual(argv[argv.index("-P") + 1], "cosmic-probe")
            return subprocess.CompletedProcess(argv, 1, "", "Operation not permitted")

        def paths(value):
            if str(value) in ("/home/agent/cosmic-outside-write.txt", "/tmp/cosmic-outside-write.txt"):
                return outside
            return Path(value)

        with patch.object(campaign_image.subprocess, "run", side_effect=probe), \
                patch.object(campaign_image, "Path", side_effect=paths), \
                patch.object(campaign_image.socket, "socket") as socket_mock, \
                patch.object(campaign_image.socket, "create_connection"):
            socket_mock.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 44123)
            with self.assertRaisesRegex(HarnessError, "isolation failed"):
                campaign_image.check_runtime(self.output)
        self.assertEqual(len(calls), 3)
        self.assertIn("Operation not permitted", (self.output / "codex-sandbox.log").read_text())
        self.assertFalse(json.loads((self.output / "runtime.json").read_text())["ok"])

    def test_unreviewed_policy_is_rejected_before_any_docker_command(self):
        (self.release / "ai/resources/pilot/seccomp.json").write_text('{"defaultAction":"SCMP_ACT_ALLOW"}')
        with self.assertRaisesRegex(HarnessError, "reviewed pinned profile"):
            campaign_image.build(self.release, self.output)
        self.assertFalse(self.calls)

    def test_reuse_rejects_modified_frozen_policy(self):
        result = campaign_image.build(self.release, self.output)
        policy = Path(result["seccomp_profile"])
        policy.chmod(0o600)
        policy.write_text("{}\n")
        self.calls.clear()
        with self.assertRaisesRegex(HarnessError, "artifacts changed"):
            campaign_image.build(self.release, self.output)
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
