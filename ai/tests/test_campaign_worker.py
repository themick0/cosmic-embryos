from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import campaign_worker as worker


IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64
PROXY = "http://gateway.docker.internal:3128"


class CampaignWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="campaign worker ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.job = self.root / uuid.uuid4().hex
        self.job.mkdir()
        (self.job / "worker.json").write_text("{}", encoding="utf-8")
        for name in ("runs", "exports", "checks"):
            (self.job / "source/ai" / name).mkdir(parents=True)
        entry = self.job / "source/ai/embryo_harness/campaign_inside.py"
        entry.parent.mkdir()
        entry.write_text("# Frozen entry point.\n", encoding="utf-8")
        self.profile = self.job / "source" / worker.SECCOMP_RELATIVE
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text('{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}\n', encoding="utf-8")
        (self.job / "source/datasets").mkdir()
        (self.job / "datasets").mkdir()
        for name in worker.DATASETS:
            (self.job / "datasets" / name).write_bytes(b"measurement")
            (self.job / "source/datasets" / name).touch()
        for name in ("runs", "exports", "checks", "report"):
            (self.job / "output" / name).mkdir(parents=True)
        self.ca = self.root / "ca-certificates.crt"
        self.ca.write_text("public trust bundle", encoding="utf-8")
        self.environment = {"SBX_CRED_OPENAI_MODE": "oauth", "HTTP_PROXY": PROXY,
                            "HTTPS_PROXY": PROXY, "VE_CHALLENGE_API_KEY": "never-forward",
                            "OPENAI_API_KEY": "never-forward", "SSH_AUTH_SOCK": "/do/not/mount",
                            "NO_PROXY": "*", "DOCKER_HOST": "tcp://wrong-engine:2375"}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.environment, clear=True).start()
        patch.object(worker, "CA_BUNDLE", self.ca).start()
        patch.object(worker.sys, "platform", "linux").start()
        patch.object(worker.shutil, "which", return_value="/usr/bin/docker").start()
        patch.object(worker.socket, "gethostbyname", return_value="192.168.65.1").start()

    def command(self, kind="research", **changes):
        arguments = {"job_dir": self.job, "image_id": IMAGE, "kind": kind,
                     "model": worker.MODEL, "wall_time_seconds": 120 if kind == "smoke" else 600}
        arguments.update(changes)
        return worker.container_command(**arguments)

    def spec(self):
        return {"job_id": self.job.name, "container_name": "cosmic-" + self.job.name,
                "image_id": IMAGE, "docker": "/usr/bin/docker", "kind": "research",
                "model": worker.MODEL, "wall_time_seconds": 600, "deadline": 1}

    def inspect_result(self, running=True, **changes):
        value = {"Name": "/cosmic-" + self.job.name, "Id": CONTAINER,
                 "Image": IMAGE, "Config": {"Labels": {worker.JOB_LABEL: self.job.name,
                                                          worker.IMAGE_LABEL: IMAGE}},
                 "State": {"Running": running, "Status": "running" if running else "exited"}}
        value.update(changes)
        return subprocess.CompletedProcess([], 0, json.dumps([value]), "")

    def test_attestation_never_opens_auth_and_rejects_missing_oauth(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("No file reads in attestation")):
            self.assertEqual(worker.attest()["credential_mode"], "oauth")
            for mode in ("none", "apikey", ""):
                with patch.dict(os.environ, {"SBX_CRED_OPENAI_MODE": mode}):
                    with self.assertRaisesRegex(worker.WorkerError, "Managed ChatGPT OAuth"):
                        worker.attest()

    def test_proxy_rejects_credentials_paths_bypass_and_mismatches(self):
        for value in ("http://secret@gateway.docker.internal:3128", "http://gateway.docker.internal:3128/",
                      "http://gateway.docker.internal:3128?q=x", "http://gateway.docker.internal:3128#x",
                      "http://gateway.docker.internal:3128?", "http://localhost:3128",
                      "socks5://gateway.docker.internal:3128", "http://gateway.docker.internal:0",
                      "http://gateway.docker.internal:bad", " http://gateway.docker.internal:3128"):
            with self.subTest(value=value), patch.dict(os.environ, {"HTTP_PROXY": value, "HTTPS_PROXY": value}):
                with self.assertRaises(worker.WorkerError):
                    worker.attest()
        with patch.dict(os.environ, {"HTTPS_PROXY": "https://gateway.docker.internal:3128"}):
            with self.assertRaisesRegex(worker.WorkerError, "same explicit"):
                worker.attest()
        with patch.object(worker.socket, "gethostbyname", return_value="127.0.0.1"):
            with self.assertRaisesRegex(worker.WorkerError, "non-loopback"):
                worker.attest()

    def test_command_has_only_approved_mounts_and_no_credentials(self):
        command = self.command()
        mounts = [command[i + 1] for i, item in enumerate(command) if item == "--mount"]
        self.assertEqual(len(mounts), 9)
        self.assertEqual(sum(",readonly" in mount for mount in mounts), 5)
        for name in worker.DATASETS:
            self.assertIn(f"type=bind,src={self.job}/datasets/{name},dst=/research/datasets/{name},readonly", mounts)
        self.assertIn(f"type=bind,src={self.job}/output/report,dst=/run/cosmic-output", mounts)
        self.assertIn(f"type=bind,src={self.ca},dst=/run/cosmic-trust.pem,readonly", mounts)
        self.assertNotIn("--rm", command)
        for forbidden in ("--privileged", "seccomp=unconfined", "/var/run/docker.sock", "--volumes-from", "--env-file"):
            self.assertNotIn(forbidden, " ".join(command))
        for secret in ("never-forward", "VE_CHALLENGE_API_KEY", "OPENAI_API_KEY", "SSH_AUTH_SOCK", "NO_PROXY"):
            self.assertNotIn(secret, " ".join(command))
        self.assertEqual(command[command.index("--entrypoint") + 1], "/opt/cosmic/.venv/bin/python")
        self.assertEqual(command[command.index("--memory") + 1], "8g")
        self.assertEqual(command[command.index("--cpus") + 1], "3")
        self.assertEqual(command[command.index("--pids-limit") + 1], "512")
        self.assertIn("seccomp=" + str(self.profile), command)
        environment = worker._environment(self.job, worker.attest())
        self.assertEqual(set(environment), {"PATH", "LC_ALL", "DOCKER_CONFIG", "HTTP_PROXY", "HTTPS_PROXY"})
        self.assertEqual(environment["HTTP_PROXY"], PROXY)

    def test_smoke_mounts_no_measurements_and_arguments_are_bounded(self):
        command = self.command("smoke")
        self.assertFalse(any("src=" + str(self.job / "datasets") in arg for arg in command))
        for changes in ({"image_id": "image:latest"}, {"image_id": "sha256:" + "A" * 64},
                        {"model": "other"}, {"kind": "other"}, {"wall_time_seconds": True},
                        {"wall_time_seconds": 0}, {"wall_time_seconds": 7201}):
            with self.subTest(changes=changes), self.assertRaises(worker.WorkerError):
                self.command(**changes)
        with self.assertRaises(worker.WorkerError):
            self.command("smoke", wall_time_seconds=121)

    def test_runtime_symlinks_and_extra_measurements_are_rejected(self):
        original = self.job / "datasets" / worker.DATASETS[0]
        original.unlink()
        original.symlink_to(self.ca)
        with self.assertRaisesRegex(worker.WorkerError, "symlink"):
            self.command()
        original.unlink()
        original.write_bytes(b"measurement")
        extra = self.job / "datasets/held-out.h5ad"
        extra.touch()
        with self.assertRaisesRegex(worker.WorkerError, "exactly the three"):
            self.command()
        extra.unlink()
        output = self.job / "output/report"
        output.rmdir()
        output.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(worker.WorkerError, "symlink"):
            self.command()

    def test_source_cannot_expose_credentials_or_symlinked_content(self):
        path = self.job / "source/ai/.env.local"
        path.write_text("not read", encoding="utf-8")
        with self.assertRaisesRegex(worker.WorkerError, "excluded credential"):
            self.command()
        path.unlink()
        path = self.job / "source/ai/nested-link"
        path.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(worker.WorkerError, "symlink"):
            self.command()

    def test_stop_checks_identity_before_stopping_and_uses_immutable_id(self):
        responses = [self.inspect_result(), subprocess.CompletedProcess([], 0, "", ""),
                     self.inspect_result(False), self.inspect_result(False)]
        with patch.object(worker, "_docker", side_effect=responses) as docker:
            self.assertEqual(worker._stop_owned(self.job, self.spec()), "stopped")
        self.assertEqual(docker.call_args_list[1].args[1], ["stop", "--time", "10", CONTAINER])
        for changes in ({"Name": "/unrelated"}, {"Image": "sha256:" + "c" * 64},
                        {"Config": {"Labels": {worker.JOB_LABEL: "other"}}}):
            with self.subTest(changes=changes), patch.object(worker, "_docker", return_value=self.inspect_result(**changes)) as docker:
                self.assertEqual(worker._stop_owned(self.job, self.spec()), "uncertain")
                self.assertEqual(docker.call_count, 1)
        (self.job / "container.id").write_text("c" * 64, encoding="ascii")
        with patch.object(worker, "_docker", return_value=self.inspect_result()) as docker:
            self.assertEqual(worker._stop_owned(self.job, self.spec()), "uncertain")
            self.assertEqual(docker.call_count, 1)

    def test_success_retains_full_result_and_forbids_every_relaunch(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0

        def launch(*args, **kwargs):
            (self.job / "output/report/result.json").write_text('{"ok": true, "usage": {"tokens": 7}}', encoding="utf-8")
            return process

        with patch.object(worker, "_start_watchdog"), patch.object(worker, "_stop_owned", return_value="stopped"), \
                patch.object(worker.subprocess, "Popen", side_effect=launch) as popen:
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["usage"], {"tokens": 7})
        self.assertTrue((self.job / worker.FINISHED_FILE).is_file())
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(json.loads((self.job / worker.DEADLINE_FILE).read_text())["deadline"] - result["started_at"], 900)
        with patch.object(worker.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(worker.WorkerError, "cannot resume"):
                worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
            popen.assert_not_called()

    def test_timeout_stops_container_and_does_not_resume(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("docker", 900), 137]
        process.poll.return_value = None
        with patch.object(worker, "_start_watchdog"), \
                patch.object(worker, "_stop_owned", return_value="stopped") as stop, \
                patch.object(worker.subprocess, "Popen", return_value=process):
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(result["status"], "failed")
        stop.assert_called_once()
        process.kill.assert_called_once()
        self.assertTrue((self.job / "outer-stdout.log").exists())
        self.assertTrue((self.job / "outer-stderr.log").exists())
        with self.assertRaisesRegex(worker.WorkerError, "cannot resume"):
            worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)

    def test_uncertain_termination_does_not_cancel_watchdog(self):
        process = Mock()
        process.wait.return_value = 1
        with patch.object(worker, "_start_watchdog"), patch.object(worker, "_stop_owned", return_value="uncertain"), \
                patch.object(worker.subprocess, "Popen", return_value=process):
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse((self.job / worker.FINISHED_FILE).exists())

    def test_timeout_with_absent_container_keeps_watchdog_for_delayed_create(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("docker", 900), 137]
        process.poll.return_value = None
        with patch.object(worker, "_start_watchdog"), patch.object(worker, "_stop_owned", return_value="absent"), \
                patch.object(worker.subprocess, "Popen", return_value=process):
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse((self.job / worker.FINISHED_FILE).exists())
        process.kill.assert_called_once()

    def test_interrupted_popen_without_returned_handle_retains_watchdog(self):
        child_started = []

        def interrupted_constructor(*args, **kwargs):
            child_started.append(True)
            raise KeyboardInterrupt("Synthetic interruption after child creation")

        with patch.object(worker, "_start_watchdog"), \
                patch.object(worker.subprocess, "Popen", side_effect=interrupted_constructor), \
                patch.object(worker, "_stop_owned", return_value="absent") as stop:
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(child_started, [True])
        stop.assert_called_once()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse((self.job / worker.FINISHED_FILE).exists())

    def test_expired_deadline_prevents_container_creation(self):
        current = {"time": 1000.0}

        def late_watchdog_ready(job, spec):
            current["time"] = spec["deadline"] + 30

        with patch.object(worker.time, "time", side_effect=lambda: current["time"]), \
                patch.object(worker, "_start_watchdog", side_effect=late_watchdog_ready), \
                patch.object(worker.subprocess, "Popen") as launch, \
                patch.object(worker, "_stop_owned") as stop:
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        launch.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertTrue((self.job / worker.FINISHED_FILE).exists())

    def test_exited_client_with_absent_container_still_keeps_watchdog(self):
        for exit_code in (0, 125):
            with self.subTest(exit_code=exit_code):
                job = self.root / uuid.uuid4().hex
                shutil.copytree(self.job, job)
                process = Mock()
                process.wait.return_value = exit_code
                process.poll.return_value = exit_code
                with patch.object(worker, "_start_watchdog"), \
                        patch.object(worker, "_stop_owned", return_value="absent"), \
                        patch.object(worker.subprocess, "Popen", return_value=process):
                    result = worker.run_job(job, IMAGE, "research", worker.MODEL, 600)
                self.assertEqual(result["status"], "uncertain")
                self.assertFalse((job / worker.FINISHED_FILE).exists())

    def test_watchdog_stops_a_container_that_appears_after_its_deadline(self):
        (self.job / worker.DEADLINE_FILE).write_text(json.dumps(self.spec()), encoding="utf-8")
        responses = [subprocess.CompletedProcess([], 1, "", "Error: No such container"),
                     self.inspect_result(), subprocess.CompletedProcess([], 0, "", ""),
                     self.inspect_result(False), self.inspect_result(False)]
        with patch.object(worker, "_docker", side_effect=responses) as docker, patch.object(worker.time, "sleep"):
            worker.watch(self.job)
        self.assertEqual(docker.call_args_list[2].args[1], ["stop", "--time", "10", CONTAINER])
        self.assertEqual(json.loads((self.job / "watchdog-state.json").read_text())["status"], "stopped")

    def test_watchdog_keeps_enforcing_when_audit_storage_is_full(self):
        (self.job / worker.DEADLINE_FILE).write_text(json.dumps(self.spec()), encoding="utf-8")
        original_write = worker._write

        def full_audit_disk(path, value, **kwargs):
            if path.name == "watchdog-state.json":
                raise OSError(errno.ENOSPC, "Synthetic full filesystem")
            return original_write(path, value, **kwargs)

        with patch.object(worker, "_write", side_effect=full_audit_disk), \
                patch.object(worker, "_stop_owned", side_effect=["uncertain", "absent", "stopped"]) as stop, \
                patch.object(worker.time, "sleep"):
            worker.watch(self.job)
        self.assertEqual(stop.call_count, 3)
        self.assertTrue((self.job / worker.WATCH_READY).exists())

    def test_watchdog_keeps_enforcing_when_control_directory_disappears(self):
        (self.job / worker.DEADLINE_FILE).write_text(json.dumps(self.spec()), encoding="utf-8")
        unavailable = self.job.with_name(self.job.name + "-unavailable")
        attempts = []

        def stop_with_storage_loss(job, spec):
            attempts.append(spec["job_id"])
            if len(attempts) == 1:
                job.rename(unavailable)
                return "uncertain"
            unavailable.rename(job)
            return "stopped"

        with patch.object(worker, "_stop_owned", side_effect=stop_with_storage_loss), \
                patch.object(worker.time, "sleep"):
            worker.watch(self.job)
        self.assertEqual(attempts, [self.job.name, self.job.name])
        self.assertTrue((self.job / worker.WATCH_READY).exists())
        self.assertEqual(json.loads((self.job / "watchdog-state.json").read_text())["status"], "stopped")

    def test_watchdog_keeps_enforcing_when_finish_marker_has_io_errors(self):
        (self.job / worker.DEADLINE_FILE).write_text(json.dumps(self.spec()), encoding="utf-8")
        original_exists = Path.exists
        failures = []

        def unavailable_marker(path):
            if path == self.job / worker.FINISHED_FILE:
                failures.append(path)
                raise OSError(errno.EIO, "Synthetic unavailable control mount")
            return original_exists(path)

        with patch.object(Path, "exists", unavailable_marker), \
                patch.object(worker, "_stop_owned", side_effect=["uncertain", "stopped"]) as stop, \
                patch.object(worker.time, "sleep"):
            worker.watch(self.job)
        self.assertEqual(stop.call_count, 2)
        self.assertEqual(len(failures), 2)
        self.assertEqual(json.loads((self.job / "watchdog-state.json").read_text())["status"], "stopped")

    def test_watchdog_does_not_swallow_control_path_policy_errors(self):
        (self.job / worker.DEADLINE_FILE).write_text(json.dumps(self.spec()), encoding="utf-8")
        original_write = worker._write

        def invalid_audit_path(path, value, **kwargs):
            if path.name == "watchdog-state.json":
                raise worker.WorkerError("Synthetic control path policy failure")
            return original_write(path, value, **kwargs)

        with patch.object(worker, "_write", side_effect=invalid_audit_path), \
                patch.object(worker, "_stop_owned", return_value="uncertain"), \
                self.assertRaisesRegex(worker.WorkerError, "control path policy"):
            worker.watch(self.job)

    def test_watchdog_enforces_persisted_deadline_without_parent(self):
        (self.job / worker.DEADLINE_FILE).write_text(json.dumps(self.spec()), encoding="utf-8")
        with patch.object(worker, "_stop_owned", return_value="stopped") as stop:
            worker.watch(self.job)
        stop.assert_called_once()
        self.assertEqual(json.loads((self.job / "watchdog-state.json").read_text())["status"], "stopped")

    def test_watchdog_does_not_treat_pending_create_as_definitely_absent(self):
        (self.job / worker.DEADLINE_FILE).write_text(json.dumps(self.spec()), encoding="utf-8")
        with patch.object(worker, "_stop_owned", side_effect=["absent", "stopped"]) as stop, \
                patch.object(worker.time, "sleep"):
            worker.watch(self.job)
        self.assertEqual(stop.call_count, 2)

    def test_created_container_is_not_mistaken_for_terminal_container(self):
        created = self.inspect_result(False, State={"Running": False, "Status": "created"})
        with patch.object(worker, "_docker", return_value=created):
            self.assertEqual(worker._stop_owned(self.job, self.spec()), "uncertain")

    def test_missing_watchdog_prevents_container_launch(self):
        with patch.object(worker, "_start_watchdog", side_effect=worker.WorkerError("Not ready")), \
                patch.object(worker.subprocess, "Popen") as launch:
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(result["status"], "failed")
        launch.assert_not_called()

    def test_profile_change_after_lock_prevents_container_launch(self):
        def change_profile(*args):
            self.profile.write_text('{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[],"flags":[]}\n')

        with patch.object(worker, "_start_watchdog", side_effect=change_profile), \
                patch.object(worker.subprocess, "Popen") as launch:
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(result["status"], "failed")
        self.assertIn("sha256", result["seccomp_profile"])
        self.assertEqual(result["seccomp_profile"], json.loads((self.job / worker.DEADLINE_FILE).read_text())["seccomp_profile"])
        launch.assert_not_called()

    def test_profile_cannot_be_missing_symlinked_or_allow_all(self):
        self.profile.unlink()
        with self.assertRaises(worker.WorkerError):
            self.command()
        self.profile.symlink_to(self.ca)
        with self.assertRaisesRegex(worker.WorkerError, "symlink"):
            self.command()
        self.profile.unlink()
        self.profile.write_text('{"defaultAction":"SCMP_ACT_ALLOW"}\n')
        with self.assertRaisesRegex(worker.WorkerError, "deny-by-default"):
            self.command()

    def test_invalid_inner_report_cannot_complete_job(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0

        def launch(*args, **kwargs):
            (self.job / "output/report/result.json").write_text('{"usage": NaN}', encoding="utf-8")
            return process

        with patch.object(worker, "_start_watchdog"), patch.object(worker, "_stop_owned", return_value="stopped"), \
                patch.object(worker.subprocess, "Popen", side_effect=launch):
            result = worker.run_job(self.job, IMAGE, "research", worker.MODEL, 600)
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
