"""Host supervisor for one smoke session followed by one isolated T2 pilot.

This finite pilot does not upload predictions or start an open-ended search.
Its SQLite admission ledger belongs to this host and is never mounted in a role.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "embryo_harness"

from . import campaign_state, sandbox_setup
from .files import record, check_records, utc, read_json, write_json
from .team_preflight import safe_path, latest

MODEL = "gpt-6-astra"
INPUTS = ("E6.75.h5ad", "E7.25.h5ad", "E8.0.h5ad")


def control(root: Path) -> Path:
    return safe_path(root, "ai/campaign/control")


def host_cli() -> str:
    if platform.system() != "Darwin" or not shutil.which("sbx"):
        raise ValueError("Run this command on the Mac host with sbx on PATH")
    return shutil.which("sbx")


def source_release(root: Path) -> Path:
    sources = list(sandbox_setup.source_files(root))
    required = {"ai/embryo_harness/campaign_host.py", "ai/embryo_harness/campaign_inside.py",
                "ai/embryo_harness/campaign_worker.py", "ai/embryo_harness/campaign_image.py",
                "ai/embryo_harness/pilot_checks.py", "ai/resources/pilot/Dockerfile"}
    records = {str(path.relative_to(root)): record(path) for path in sources}
    if not required.issubset(records):
        raise ValueError("Pilot implementation is incomplete")
    revision = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    manifest = {"revision": revision, "files": records}
    destination = safe_path(root, "ai/campaign/releases/" + revision)
    if destination.exists():
        if read_json(destination / "release.json") != manifest:
            raise ValueError("Prepared release manifest changed")
        check_records(destination, records)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".release-", dir=destination.parent))
    try:
        for name in records:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
            target.chmod(0o444)
        check_records(staging, records)
        write_json(staging / "release.json", manifest)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def copy_release(release: Path, destination: Path) -> None:
    manifest = read_json(release / "release.json")
    records = manifest["files"]
    if destination.exists():
        if read_json(destination / "release.json") != manifest:
            raise ValueError("Worker release manifest changed")
        check_records(destination, records)
        return
    shutil.copytree(release, destination)
    check_records(destination, records)


def job_source(release: Path, job: Path) -> dict:
    """Expose only harness source, with its manifest outside the research mount."""
    source = job / "source"
    source.mkdir()
    manifest = read_json(release / "release.json")
    files = {name: value for name, value in manifest["files"].items() if name.startswith("ai/")}
    check_records(release, files)
    for name in files:
        target = safe_path(source, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_path(release, name), target)
        target.chmod(0o444)
    check_records(source, files)
    write_json(job / "source-manifest.json", {"revision": manifest["revision"], "files": files})
    return record(job / "source-manifest.json")


def phase_check() -> dict:
    responses = {}
    for name in ("phase", "boards"):
        with urlopen("https://api.virtualembryo.ai/challenge/" + name, timeout=20) as response:
            responses[name] = json.load(response)
    return responses


def require_p2(observation: dict) -> None:
    phase = observation["phase"]
    phase_text = str(phase.get("phase", "")).lower()
    if phase_text != "p2" or phase.get("split") != "val" or phase.get("data_open") is not True:
        raise ValueError("The P2 pilot requires a fresh configuration for the current competition phase")
    boards = observation["boards"]
    # The API may return a board list directly or an envelope containing that list.
    rows = boards.get("boards", []) if isinstance(boards, dict) else boards
    if not isinstance(rows, list) or not any(
            isinstance(row, dict) and row.get("key") == "T2:embryo:val_interp" for row in rows):
        raise ValueError("The live board contract does not confirm the embryo validation board")


def prepare(root: Path) -> dict:
    host_cli()
    ctl = control(root)
    ctl.mkdir(parents=True, exist_ok=True)
    with (ctl / "prepare.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Pilot preparation is already running") from None
        return _prepare(root)


def _setup_directory(worker: Path, revision: str) -> Path:
    """Reuse successful setup; retain failed attempts and allocate a fresh one."""
    directory = safe_path(worker, "ai/campaign/images/" + revision)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "image.json").exists():
        return directory
    for child in sorted(directory.iterdir()):
        if child.name.startswith("attempt-"):
            candidate = safe_path(directory, child.name)
            if (candidate / "image.json").exists():
                return candidate
    return Path(tempfile.mkdtemp(prefix="attempt-", dir=directory))


def _prepare(root: Path) -> dict:
    sbx = host_cli()
    preflight = latest(root)
    if (not preflight.get("environment_checks_ok") or not preflight.get("communication_ok")
            or {r["role"] for r in preflight.get("roles", [])} != {"coordinator", "worker-a", "uploader"}):
        raise ValueError("Complete the three-role host preflight first")
    ctl = control(root)
    ctl.mkdir(parents=True, exist_ok=True)
    manifest_path = ctl / "pilot.json"
    if manifest_path.exists():
        return read_json(manifest_path)
    if (ctl / "pilot-sessions.sqlite").exists():
        raise ValueError("An unfinished preparation already has a ledger; preserve and inspect it")
    public = phase_check()
    require_p2(public)
    release = source_release(root)
    worker = sandbox_setup.bootstrap_path(root) / "workspaces/worker-a"
    worker_release = safe_path(worker, "ai/campaign/releases/" + release.name)
    worker_release.parent.mkdir(parents=True, exist_ok=True)
    copy_release(release, worker_release)
    setup_dir = _setup_directory(worker, release.name)
    # Setup is deterministic: dependency installation and synthetic checks only.
    argv = [sbx, "exec", "-w", str(worker), "cosmic-worker-a", "python3",
            str(worker_release / "ai/embryo_harness/campaign_host.py"), "role-setup",
            "--release", str(worker_release), "--output", str(setup_dir)]
    with (ctl / "pilot-setup.log").open("ab") as output:
        subprocess.run(argv, stdout=output, stderr=output, stdin=subprocess.DEVNULL, timeout=1500, check=True)
    image = read_json(setup_dir / "image.json")
    campaign_id = uuid.uuid4().hex
    jobs = []
    for kind, seconds in (("smoke", 120), ("research", 1800)):
        job_id = uuid.uuid5(uuid.UUID(campaign_id), kind).hex
        job = safe_path(worker, "ai/campaign/jobs/" + job_id)
        job.mkdir(parents=True)
        source_manifest = job_source(release, job)
        for name in ("runs", "exports", "checks"):
            (job / "source/ai" / name).mkdir()
            (job / "output" / name).mkdir(parents=True)
        (job / "output/report").mkdir()
        (job / "source/datasets").mkdir()
        (job / "datasets").mkdir()
        inputs = {}
        if kind == "research":
            for name in INPUTS:
                source = safe_path(root, "datasets/" + name)
                expected = record(source)
                target = job / "datasets" / name
                shutil.copyfile(source, target)
                target.chmod(0o444)
                if record(target) != expected:
                    raise ValueError("A pilot input changed during preparation")
                (job / "source/datasets" / name).touch()
                inputs[name] = expected
        payload = {"id": job_id, "kind": kind, "model": MODEL, "wall_time_seconds": seconds,
                   "image_id": image["image_id"], "source_revision": release.name,
                   "source_manifest": source_manifest,
                   "inputs": inputs, "official_uploads": False}
        write_json(job / "worker.json", payload)
        jobs.append({**payload, "directory": str(job), "payload_sha256": record(job / "worker.json")["sha256"]})
    policy = {"max_sessions": 2, "max_active": 1, "allowed_models": [MODEL]}
    campaign_state.create(ctl / "pilot-sessions.sqlite", policy)
    manifest = {"version": 1, "id": campaign_id, "created_at": utc(), "jobs": jobs,
                "release": str(worker_release), "worker_workspace": str(worker),
                "phase_observation": public, "policy": policy, "official_uploads": False,
                "scope": "one smoke session followed by one research pilot"}
    write_json(manifest_path, manifest)
    return manifest


def reconcile(ledger: Path, job: dict) -> str:
    state_path = Path(job["directory"]) / "worker-state.json"
    if not state_path.exists():
        return "uncertain"
    state = read_json(state_path)
    if (state.get("job_id") != job["id"] or state.get("image_id") != job["image_id"]
            or state.get("kind") != job["kind"] or state.get("model") != job["model"]):
        return "uncertain"
    if state.get("status") not in ("completed", "failed"):
        return "uncertain"
    inner_path = Path(job["directory"]) / "output/report/result.json"
    inner = read_json(inner_path) if inner_path.exists() else {}
    outcome = "completed" if state["status"] == "completed" and inner.get("status") == "completed" else "failed"
    campaign_state.settle(ledger, job["id"], outcome, inner.get("usage"))
    return outcome


@contextmanager
def _dispatch_lock(ctl: Path):
    """Serialize stop with admission and creation, but never hold during execution."""
    with (ctl / "dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _check_stop(ctl: Path) -> None:
    if (ctl / "STOP_AFTER_CURRENT").exists():
        raise ValueError("Stop requested; no further session will start")


def stop(root: Path) -> dict:
    ctl = control(root)
    ctl.mkdir(parents=True, exist_ok=True)
    with _dispatch_lock(ctl):
        (ctl / "STOP_AFTER_CURRENT").touch(exist_ok=True)
    return {"stop_after_current": True, "active_locked_run": "allowed to finish unchanged"}


def run(root: Path) -> dict:
    sbx = host_cli()
    ctl = control(root)
    manifest = read_json(ctl / "pilot.json")
    ledger = ctl / "pilot-sessions.sqlite"
    with (ctl / "pilot.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("The pilot supervisor is already running") from None
        write_json(ctl / "pilot-supervisor.json", {"status": "running", "pid": os.getpid(), "at": utc()})
        try:
            for job in manifest["jobs"]:
                _check_stop(ctl)
                if job["model"] != MODEL:
                    raise ValueError("Prepared assignment model changed")
                directory = Path(job["directory"])
                payload_path = directory / "worker.json"
                payload = {key: value for key, value in job.items()
                           if key not in ("directory", "payload_sha256")}
                if (record(payload_path)["sha256"] != job["payload_sha256"]
                        or read_json(payload_path) != payload):
                    raise ValueError("Prepared assignment changed")
                existing = {row["assignment_id"]: row for row in campaign_state.snapshot(ledger)["jobs"]}
                previous = existing.get(job["id"])
                if previous:
                    if any(previous[key] != job[key] for key in ("model", "kind", "payload_sha256")):
                        raise ValueError("Prepared assignment disagrees with its durable reservation")
                    if previous["status"] == "completed":
                        continue
                    if previous["status"] == "failed":
                        raise ValueError("A pilot session failed; it will not be replayed")
                    outcome = reconcile(ledger, job)
                    if outcome == "completed":
                        continue
                    if outcome != "failed" and previous["status"] != "uncertain":
                        campaign_state.mark_uncertain(ledger, job["id"], "Supervisor recovery requires a terminal worker record")
                    raise ValueError("Retained session needs reconciliation; no duplicate model call was launched")
                public = phase_check()
                require_p2(public)
                write_json(ctl / ("phase-" + job["id"] + ".json"), public)
                if record(directory / "source-manifest.json") != job["source_manifest"]:
                    raise ValueError("Prepared source manifest changed")
                source_manifest = read_json(directory / "source-manifest.json")
                if source_manifest["revision"] != job["source_revision"]:
                    raise ValueError("Prepared source revision changed")
                check_records(directory / "source", source_manifest["files"])
                check_records(directory / "datasets", job["inputs"])
                argv = [sbx, "exec", "-w", manifest["worker_workspace"], "cosmic-worker-a", "python3",
                        str(Path(manifest["release"]) / "ai/embryo_harness/campaign_host.py"), "role-run",
                        "--job", job["directory"], "--image", job["image_id"], "--kind", job["kind"],
                        "--wall-time-seconds", str(job["wall_time_seconds"])]
                try:
                    with (ctl / ("launch-" + job["id"] + ".log")).open("ab") as output:
                        with _dispatch_lock(ctl):
                            _check_stop(ctl)
                            # The outer launcher imports this release, not the inner job copy.
                            check_records(Path(manifest["release"]), source_manifest["files"])
                            campaign_state.reserve(ledger, job["id"], MODEL, job["kind"], job["payload_sha256"])
                            campaign_state.mark_launched(ledger, job["id"])
                            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                                       stdout=output, stderr=output)
                        try:
                            process.wait(timeout=job["wall_time_seconds"] + 600)
                        except subprocess.TimeoutExpired:
                            # Only the sbx client is killed; the worker owns container deadlines.
                            process.kill()
                            process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                outcome = reconcile(ledger, job)
                if outcome == "uncertain":
                    campaign_state.mark_uncertain(ledger, job["id"], "Worker outcome is not confirmed")
                if outcome != "completed":
                    raise ValueError("Pilot did not complete; original evidence and admission remain retained")
            result = {"status": "completed", "finished_at": utc(), "official_uploads": False,
                      "research_job": manifest["jobs"][-1]["directory"]}
            write_json(ctl / "pilot-supervisor.json", result)
            return result
        except BaseException:
            write_json(ctl / "pilot-supervisor.json", {"status": "stopped", "at": utc(),
                                                      "reason": "Inspect the retained ledger and worker logs before further sessions"})
            raise


def status(root: Path) -> dict:
    ctl = control(root)
    if not (ctl / "pilot.json").exists():
        return {"prepared": False, "official_uploads": False}
    return {"prepared": True, "manifest": read_json(ctl / "pilot.json"),
            "ledger": campaign_state.snapshot(ctl / "pilot-sessions.sqlite"),
            "supervisor": read_json(ctl / "pilot-supervisor.json") if (ctl / "pilot-supervisor.json").exists() else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "start", "run", "status", "stop"):
        subs.add_parser(name)
    setup = subs.add_parser("role-setup")
    setup.add_argument("--release", required=True, type=Path)
    setup.add_argument("--output", required=True, type=Path)
    worker = subs.add_parser("role-run")
    worker.add_argument("--job", required=True, type=Path)
    worker.add_argument("--image", required=True)
    worker.add_argument("--kind", required=True)
    worker.add_argument("--wall-time-seconds", required=True, type=int)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    try:
        if args.command == "role-setup":
            from .campaign_worker import attest
            from .campaign_image import build
            attest()
            result = build(args.release, args.output)
        elif args.command == "role-run":
            from .campaign_worker import run_job
            result = run_job(args.job, args.image, args.kind, MODEL, args.wall_time_seconds)
        elif args.command == "prepare":
            result = prepare(root)
        elif args.command == "run":
            result = run(root)
        elif args.command == "start":
            host_cli()
            ctl = control(root)
            read_json(ctl / "pilot.json")
            if (ctl / "STOP_AFTER_CURRENT").exists():
                raise ValueError("A stop request is recorded; inspect before starting again")
            command = [sys.executable, str(root / "ai/campaign.py"), "run"]
            if shutil.which("caffeinate"):
                command = [shutil.which("caffeinate"), "-is", *command]
            with (ctl / "supervisor.log").open("ab") as output:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                           stderr=output, start_new_session=True)
            result = {"supervisor_pid": process.pid, "status": "starting", "log": str(ctl / "supervisor.log")}
        elif args.command == "stop":
            result = stop(root)
        else:
            result = status(root)
        print(json.dumps(result, indent=2))
        return 1 if args.command == "role-run" and result.get("status") != "completed" else 0
    except Exception as error:
        print(f"campaign pilot: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
