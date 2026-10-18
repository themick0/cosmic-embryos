"""Codex subprocess adapter. All flags are explicit and included in the run lock."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

from .files import HarnessError, record, read_json, write_json, utc


REQUIRED_FLAGS = ("--json", "--output-schema", "--ignore-user-config", "--ignore-rules")


def probe(executable: str) -> dict:
    resolved = shutil.which(executable)
    if not resolved:
        raise HarnessError(f"Codex executable not found: {executable}")
    version = subprocess.run([resolved, "--version"], text=True, capture_output=True,
                             timeout=20, check=True).stdout.strip()
    help_text = subprocess.run([resolved, "exec", "--help"], text=True, capture_output=True,
                               timeout=20, check=True).stdout
    missing = [flag for flag in REQUIRED_FLAGS if flag not in help_text]
    if missing:
        raise HarnessError(f"Update Codex CLI; missing flags: {missing}")
    # Resolve npm's entry-point symlink before fingerprinting; no credentials are read.
    path = Path(resolved).resolve()
    return {"path": str(path), "version": version, "entrypoint": record(path)}


def command(run: Path, config: dict, runtime: dict) -> list[str]:
    return [runtime["path"], "--no-daemon", "-a", "never", "exec",
            "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--json", "--color", "never",
            "--model", config["model"], "--sandbox", config["sandbox"],
            "-c", f'model_reasoning_effort="{config["reasoning_effort"]}"',
            "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
            "-c", "features.multi_agent=false",
            "-c", "sandbox_workspace_write.network_access=" + str(config["network_access"]).lower(),
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            "--cd", str(run / "work"),
            "--output-schema", str(run / "frozen" / "final.schema.json"),
            "--output-last-message", str(run / "final.json"), "-"]


def stop_group(process):
    """Terminate the entire process group, including training child processes."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    # Parent exit does not imply all its children exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def execute(argv: list[str], prompt: Path, run: Path, seconds: int) -> tuple[int, bool]:
    if os.name != "posix":
        raise HarnessError("This runner requires macOS/Linux process groups")
    with prompt.open("rb") as stdin, (run / "trajectory.jsonl").open("wb") as stdout, \
            (run / "stderr.log").open("wb") as stderr:
        environment = {k: v for k, v in os.environ.items() if k != "VE_CHALLENGE_API_KEY"}
        process = subprocess.Popen(argv, cwd=run / "work", stdin=stdin, stdout=stdout,
                                   stderr=stderr, start_new_session=True, env=environment)
        deadline = time.monotonic() + seconds
        def interrupted(signum, frame):
            raise KeyboardInterrupt(f"Received signal {signum}")
        old_term = signal.signal(signal.SIGTERM, interrupted)
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    stop_group(process)
                    return process.returncode, True
                time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))
        except BaseException:
            stop_group(process)
            raise
        finally:
            signal.signal(signal.SIGTERM, old_term)
        # Background jobs must not mutate predictions after the agent exits.
        stop_group(process)
        return process.returncode, False


def smoke_check(root: Path, model: str, executable="codex") -> dict:
    """Make one small real call and check shell execution with the run flags."""
    from .core import summarize_trace

    runtime = probe(executable)
    run = root / "ai/checks" / ("codex-" + uuid.uuid4().hex[:12])
    (run / "work").mkdir(parents=True)
    (run / "frozen").mkdir()
    config = {"model": model, "reasoning_effort": "high", "sandbox": "workspace-write",
              "network_access": False}
    write_json(run / "frozen/final.schema.json", {"type": "object", "additionalProperties": False,
               "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
    prompt = run / "frozen/prompt.txt"
    prompt.write_text("Infrastructure smoke test, not a research run. Use a shell command to write "
                      "exactly HARNESS_OK followed by a newline to smoke.txt in the current directory. "
                      "Do not read other files, use the network, or delegate. Return {\"ok\":true} "
                      "only if that command succeeds.\n")
    argv = command(run, config, runtime)
    write_json(run / "frozen/command.json", argv)
    result = {"checked_at": utc(), "directory": str(run), "model": model, "runtime": runtime,
              "ok": False}
    write_json(run / "result.json", result)
    rc, timeout = execute(argv, prompt, run, 120)
    result.update(returncode=rc, timed_out=timeout)
    if not rc and not timeout:
        result["trace"] = summarize_trace(run / "trajectory.jsonl")
        result["ok"] = (read_json(run / "final.json").get("ok") is True
                        and (run / "work/smoke.txt").read_text() == "HARNESS_OK\n")
    write_json(run / "result.json", result)
    if not result["ok"]:
        raise HarnessError(f"Codex smoke check failed; inspect {run}")
    return result
