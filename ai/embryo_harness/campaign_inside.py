"""Run one admitted pilot session inside its isolated research container."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import codex, core
from embryo_harness.files import write_json, utc, record


ROOT = Path("/research")
RESULT_PATH = Path("/run/cosmic-output/result.json")
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")
PILOT_INPUTS = ["datasets/E6.75.h5ad", "datasets/E7.25.h5ad", "datasets/E8.0.h5ad"]


def _usage(value):
    if (not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] < 0
                                           for key in USAGE_KEYS)):
        raise ValueError("The adapter did not return valid reported usage")
    return {key: value[key] for key in USAGE_KEYS}


def _observed_usage(directory):
    """Retain known completed-turn usage without claiming a failed trace is complete."""
    trajectory = directory / "trajectory.jsonl"
    if not trajectory.is_file() or trajectory.is_symlink():
        return None
    totals, observed = dict.fromkeys(USAGE_KEYS, 0), False
    try:
        with trajectory.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or event.get("type") != "turn.completed":
                        continue
                    usage = event.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    counts = _usage({key: usage.get(key, 0) for key in USAGE_KEYS})
                except (ValueError, TypeError):
                    continue
                observed = True
                for key in USAGE_KEYS:
                    totals[key] += counts[key]
    except (OSError, UnicodeError):
        pass
    return totals if observed else None


def _new_directory(base, before):
    try:
        candidates = [path for path in base.iterdir()
                      if path.name not in before and path.is_dir() and not path.is_symlink()]
    except OSError:
        return None
    return candidates[0] if len(candidates) == 1 else None


def _bootstrap_auth():
    # These public placeholders are Docker's documented proxy protocol, not user credentials.
    auth = Path.home() / ".codex/auth.json"
    if os.environ.get("CODEX_HOME", str(auth.parent)) != str(auth.parent):
        raise ValueError("The image must use the fresh default Codex home")
    if auth.parent.is_symlink():
        raise ValueError("The Codex home cannot be a symlink")
    auth.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"OPENAI_API_KEY": "proxy-managed"}, stream)


def _pilot_config(root, model, wall_time_seconds):
    config_path = root / "ai/configs/t2_pilot.json"
    config = core.load_config(config_path, root)
    expected = {"codex_provider": "docker-sandbox-oauth", "model": model,
                "wall_time_seconds": wall_time_seconds, "task": "T2",
                "boards": ["T2:embryo:val_interp"], "inputs": PILOT_INPUTS,
                "sandbox": "workspace-write", "network_access": False,
                "reasoning_effort": "high", "codex_executable": "codex", "seed": 42,
                "prompt_files": ["ai/prompts/competition.md", "ai/prompts/common.md",
                                 "ai/prompts/t2_pilot.md"]}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("Frozen pilot configuration does not match the admitted session")
    return config_path


def execute(kind: str, model: str, wall_time_seconds: int, result_path: Path) -> dict:
    root = ROOT
    if kind not in ("smoke", "research") or model != "gpt-6-astra":
        raise ValueError("This pilot permits only an Astra smoke check and one research run")
    if type(wall_time_seconds) is not int or wall_time_seconds != (120 if kind == "smoke" else 1800):
        raise ValueError("Pilot wall-time contract differs")
    result_path = Path(result_path)
    if result_path != RESULT_PATH or any(path.is_symlink() for path in (result_path, *result_path.parents)):
        raise ValueError("Results must use this container's dedicated report mount")
    result = {"version": 1, "kind": kind, "model": model, "status": "running", "started_at": utc(),
              "official_uploads": False, "usage": None, "usage_complete": False,
              "usage_scope": "reported_completed_turns"}
    # The fresh report is also a single-use marker if this entry point is called twice.
    with result_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream)
    base = root / "ai" / ("checks" if kind == "smoke" else "runs")
    before = {path.name for path in base.iterdir()}
    run = None
    try:
        config_path = _pilot_config(root, model, wall_time_seconds) if kind == "research" else None
        _bootstrap_auth()
        if kind == "smoke":
            checked = codex.smoke_check(root, model, provider="docker-sandbox-oauth")
            if checked.get("ok") is not True:
                raise ValueError("The smoke adapter did not confirm success")
            result.update(status="completed", check_directory=checked["directory"],
                          usage=_usage(checked["trace"]["usage"]), usage_complete=True)
        else:
            run = core.prepare(root, config_path)
            result["run_directory"] = str(run)
            write_json(result_path, result)
            completed = core.execute(run)
            result.update(usage=_usage(completed["trace"]["usage"]), usage_complete=True)
            core.verify(run)
            bundle = core.bundle(run)
            result.update(status="completed", bundle_directory=str(bundle),
                          evidence={"lock": record(run / "lock.json"),
                                    "receipt": record(run / "receipt.json"),
                                    "manifest": record(bundle / "manifest.json")})
    except BaseException:
        directory = run if run is not None else _new_directory(base, before)
        if directory is not None:
            result["check_directory" if kind == "smoke" else "run_directory"] = str(directory)
            if result["usage"] is None:
                result["usage"] = _observed_usage(directory)
        result.update(status="failed", finished_at=utc(),
                      error="Pilot session failed; retain the original run logs and artifacts")
        write_json(result_path, result)
        raise
    result["finished_at"] = utc()
    write_json(result_path, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("smoke", "research"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--wall-time-seconds", required=True, type=int)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    execute(args.kind, args.model, args.wall_time_seconds, args.result)


if __name__ == "__main__":
    main()
