"""Run one admitted job in a fresh container, without importing model credentials.

The host owns admission. This module never retries a job, builds an image, reads
Codex authentication files, or changes the container's sandbox after failure.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid


DATASETS = ("E6.75.h5ad", "E7.25.h5ad", "E8.0.h5ad")
MODEL = "gpt-6-astra"
CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
JOB_LABEL = "ai.cosmic-embryos.job"
IMAGE_LABEL = "ai.cosmic-embryos.image"
STATE_FILE = "worker-state.json"
DEADLINE_FILE = "worker-deadline.json"
WATCH_READY = "watchdog-ready.json"
FINISHED_FILE = "worker-finished.json"
SECCOMP_RELATIVE = Path("ai/resources/pilot/seccomp.json")


class WorkerError(ValueError):
    pass


class WorkerStorageUnavailable(WorkerError):
    """A runtime path could not be inspected because its storage is unavailable."""


def _path(value, *, directory=False):
    path = Path(value).absolute()
    if ".." in path.parts or any(character in str(path) for character in (",", "\n", "\r", "\x00")):
        raise WorkerError("Runtime paths cannot contain traversal, commas or control characters")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise WorkerError("Runtime paths cannot contain symlinks")
    try:
        mode = path.stat().st_mode
    except OSError as error:
        raise WorkerStorageUnavailable("A required runtime path is unavailable") from error
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise WorkerError("Runtime paths must be regular files or directories of the expected kind")
    return path


def _identifier(job_dir):
    job = _path(job_dir, directory=True)
    if re.fullmatch(r"[0-9a-f]{32}", job.name) is None:
        raise WorkerError("Job directory basename must be a lowercase UUID hex string")
    return job, job.name


def _json_file(path):
    path = _path(path)
    if path.stat().st_size > 1024 * 1024:
        raise WorkerError("Runtime JSON exceeds 1 MiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        json.dumps(value, allow_nan=False)
    except (ValueError, UnicodeError):
        raise WorkerError("Invalid runtime JSON") from None
    if not isinstance(value, dict):
        raise WorkerError("Runtime JSON must be an object")
    return value


def _write(path, value, *, exclusive=False):
    """Publish durable outer control files; never use an agent-writable directory."""
    path = Path(path)
    _path(path.parent, directory=True)
    if path.is_symlink():
        raise WorkerError("Control files cannot be symlinks")
    encoded = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    target = path if exclusive else path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if not exclusive:
            os.replace(target, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if not exclusive and target.exists():
            target.unlink()


def _proxy(value):
    if not isinstance(value, str) or value != value.strip() or any(ord(c) < 33 for c in value):
        raise WorkerError("Explicit HTTP_PROXY and HTTPS_PROXY URLs are required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise WorkerError("Invalid forward proxy URL") from None
    if (parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("gateway.docker.internal", "host.docker.internal")
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or port == 0 or "?" in value or "#" in value):
        raise WorkerError("Forward proxy must be an uncredentialed Docker gateway origin without a path or query")
    return parsed.hostname


def attest() -> dict:
    """Inspect only a nonsecret mode indicator, proxy settings and local tooling."""
    if sys.platform != "linux":
        raise WorkerError("The research worker must run inside its Linux Docker Sandbox")
    if os.environ.get("SBX_CRED_OPENAI_MODE") != "oauth":
        raise WorkerError("Managed ChatGPT OAuth is not active in this worker; configure sbx secret set openai --oauth on the host and refresh the worker credential binding before retrying with a new job")
    http, https = os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY")
    hostname = _proxy(http)
    if _proxy(https) != hostname or http != https:
        raise WorkerError("HTTP_PROXY and HTTPS_PROXY must name the same explicit forward proxy URL")
    try:
        address = str(ipaddress.IPv4Address(socket.gethostbyname(hostname)))
    except (OSError, ValueError):
        raise WorkerError("Cannot resolve the Docker forward proxy gateway") from None
    parsed_address = ipaddress.IPv4Address(address)
    if parsed_address.is_loopback or parsed_address.is_unspecified or parsed_address.is_multicast:
        raise WorkerError("The Docker forward proxy gateway must resolve to a reachable non-loopback address")
    docker = shutil.which("docker")
    if not docker:
        raise WorkerError("Docker is not installed in this worker")
    return {"credential_mode": "oauth", "http_proxy": http, "https_proxy": https,
            "gateway_hostname": hostname, "gateway_address": address,
            "ca_bundle": str(_path(CA_BUNDLE)), "docker": str(Path(docker).absolute())}


def _layout(job_dir, kind):
    job, identifier = _identifier(job_dir)
    _json_file(job / "worker.json")
    source = _path(job / "source", directory=True)
    if {p.name for p in source.iterdir()} != {"ai", "datasets"}:
        raise WorkerError("Pinned source must contain only ai and the dataset mount targets")
    _path(source / "ai/embryo_harness/campaign_inside.py")
    for directory, subdirectories, files in os.walk(source, followlinks=False):
        for name in subdirectories + files:
            candidate = Path(directory) / name
            if candidate.is_symlink() or name in (".git", ".codex", ".venv", "auth.json") or name.startswith(".env"):
                raise WorkerError("Pinned source contains a symlink or excluded credential/runtime path")
            _path(candidate, directory=candidate.is_dir())
    for name in ("runs", "exports", "checks"):
        target = _path(source / "ai" / name, directory=True)
        if any(target.iterdir()):
            raise WorkerError("Pinned source runtime mount targets must be empty")
    dataset_targets = _path(source / "datasets", directory=True)
    if any(p.name not in DATASETS or not p.is_file() or p.stat().st_size for p in dataset_targets.iterdir()):
        raise WorkerError("Source datasets may contain only empty allowlisted mount targets")
    output = _path(job / "output", directory=True)
    if {p.name for p in output.iterdir()} != {"runs", "exports", "checks", "report"}:
        raise WorkerError("Output must contain exactly runs, exports, checks and report")
    for name in ("runs", "exports", "checks", "report"):
        path = _path(output / name, directory=True)
        if any(path.iterdir()):
            raise WorkerError("A job must start with empty output directories")
    if kind == "research":
        datasets = _path(job / "datasets", directory=True)
        if {p.name for p in datasets.iterdir()} != set(DATASETS):
            raise WorkerError("Research requires exactly the three permitted embryo inputs")
        for name in DATASETS:
            _path(datasets / name)
            _path(dataset_targets / name)
    return job, identifier


def _parameters(image_id, kind, model, wall_time_seconds):
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise WorkerError("A local immutable sha256 image ID is required")
    if kind not in ("smoke", "research") or model != MODEL:
        raise WorkerError("The pilot supports smoke/research with gpt-6-astra only")
    if type(wall_time_seconds) is not int or not 1 <= wall_time_seconds <= (120 if kind == "smoke" else 7200):
        raise WorkerError("Wall time must be 1..120 seconds for smoke or 1..7200 for research")


def _seccomp_profile(job):
    path = _path(job / "source" / SECCOMP_RELATIVE)
    if _json_file(path).get("defaultAction") != "SCMP_ACT_ERRNO":
        raise WorkerError("The pinned seccomp profile must retain its deny-by-default policy")
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise WorkerError("The seccomp profile changed while being recorded")
    return {"path": str(path), "bytes": after.st_size, "sha256": digest}


def _command(job, identifier, image_id, kind, model, wall_time_seconds, attestation):
    profile = _seccomp_profile(job)
    command = [attestation["docker"], "run", "--pull", "never", "--name", "cosmic-" + identifier,
               "--label", JOB_LABEL + "=" + identifier, "--label", IMAGE_LABEL + "=" + image_id,
               "--cidfile", str(job / "container.id"), "--cpus", "3", "--memory", "8g",
               "--pids-limit", "512", "--user", "1000:1000", "--init", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--security-opt", "seccomp=" + profile["path"], "--network", "bridge",
               "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
               "--tmpfs", "/home/agent:rw,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=700",
               "--workdir", "/research", "--add-host",
               attestation["gateway_hostname"] + ":" + attestation["gateway_address"]]
    mounts = [(job / "source", "/research", True),
              (Path(attestation["ca_bundle"]), "/run/cosmic-trust.pem", True)]
    mounts += [(job / "output" / name, "/research/ai/" + name, False) for name in ("runs", "exports", "checks")]
    mounts.append((job / "output/report", "/run/cosmic-output", False))
    if kind == "research":
        mounts += [(job / "datasets" / name, "/research/datasets/" + name, True) for name in DATASETS]
    for source, target, readonly in mounts:
        command += ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        command += ["--env", name]
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        command += ["--env", name + "=/run/cosmic-trust.pem"]
    command += ["--entrypoint", "/opt/cosmic/.venv/bin/python", image_id,
                "/research/ai/embryo_harness/campaign_inside.py", "--kind", kind,
                "--model", model, "--wall-time-seconds", str(wall_time_seconds),
                "--result", "/run/cosmic-output/result.json"]
    return command


def container_command(job_dir, image_id, kind, model, wall_time_seconds):
    """Build a fixed mount/permission command; no Docker or model process starts."""
    _parameters(image_id, kind, model, wall_time_seconds)
    job, identifier = _layout(job_dir, kind)
    return _command(job, identifier, image_id, kind, model, wall_time_seconds, attest())


def _environment(job, attestation=None):
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8",
                   "DOCKER_CONFIG": str(job / "docker-client")}
    if attestation is not None:
        environment.update(HTTP_PROXY=attestation["http_proxy"], HTTPS_PROXY=attestation["https_proxy"])
    return environment


def _docker(spec, arguments, job, *, timeout=15):
    return subprocess.run([spec["docker"], *arguments], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          timeout=timeout, env=_environment(job), check=False)


def _inspect(job, spec):
    completed = _docker(spec, ["container", "inspect", spec["container_name"]], job)
    if completed.returncode:
        if "No such container" in completed.stderr or "No such object" in completed.stderr:
            return None
        raise WorkerError("Docker container identity could not be reconciled")
    try:
        container = json.loads(completed.stdout)[0]
        labels = container["Config"]["Labels"] or {}
        identifier = container["Id"]
        valid = (container["Name"] == "/" + spec["container_name"]
                 and re.fullmatch(r"[0-9a-f]{64}", identifier) is not None
                 and labels.get(JOB_LABEL) == spec["job_id"]
                 and labels.get(IMAGE_LABEL) == spec["image_id"]
                 and container["Image"] == spec["image_id"])
        cid_file = job / "container.id"
        if cid_file.exists() or cid_file.is_symlink():
            cid = _path(cid_file).read_text(encoding="ascii").strip()
            valid = valid and cid == identifier
        if (not valid or type(container["State"]["Running"]) is not bool
                or container["State"].get("Status") not in
                ("created", "running", "paused", "restarting", "removing", "exited", "dead")):
            raise WorkerError("Container identity does not match this job; refusing to stop it")
        return container
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError):
        raise WorkerError("Container identity does not match this job; refusing to stop it") from None


def _stop_owned(job, spec):
    """Stop only the immutable ID after checking the name, labels and image."""
    try:
        container = _inspect(job, spec)
        if container is None:
            return "absent"
        if container["State"]["Running"]:
            try:
                _docker(spec, ["stop", "--time", "10", container["Id"]], job, timeout=20)
            except subprocess.TimeoutExpired:
                pass
            current = _inspect(job, spec)
            if current is not None and current["State"]["Running"]:
                _docker(spec, ["kill", current["Id"]], job)
        current = _inspect(job, spec)
        if current is None:
            return "absent"
        return "stopped" if current["State"]["Status"] in ("exited", "dead") else "uncertain"
    except (OSError, WorkerError, subprocess.SubprocessError):
        return "uncertain"


def watch(job_dir):
    """An independent process enforces the durable deadline after parent loss."""
    job, identifier = _identifier(job_dir)
    spec = _json_file(job / DEADLINE_FILE)
    if (spec.get("job_id") != identifier or spec.get("container_name") != "cosmic-" + identifier
            or type(spec.get("deadline")) not in (float, int) or not math.isfinite(spec["deadline"])
            or not isinstance(spec.get("docker"), str) or not Path(spec["docker"]).is_absolute()):
        raise WorkerError("Invalid watchdog identity or deadline")
    _parameters(spec.get("image_id"), spec.get("kind"), spec.get("model"), spec.get("wall_time_seconds"))
    _write(job / WATCH_READY, {"job_id": identifier, "pid": os.getpid()}, exclusive=True)
    while True:
        try:
            finished = (job / FINISHED_FILE).exists()
        except OSError:
            # An unreadable finish marker cannot confirm that enforcement ended.
            finished = False
        if finished:
            return
        if time.time() >= spec["deadline"]:
            outcome = _stop_owned(job, spec)
            try:
                _write(job / "watchdog-state.json", {"job_id": identifier, "status": outcome, "checked_at": time.time()})
            except (OSError, WorkerStorageUnavailable):
                # A full or unavailable output filesystem must not disable
                # termination retries. Identity/policy errors still propagate.
                pass
            if outcome == "stopped":
                return
            # A delayed Docker create or unavailable daemon is not proof that no
            # container can start. Keep watching until the parent reconciles it.
        time.sleep(2)


def _start_watchdog(job, spec):
    with (job / "watchdog.log").open("xb") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "watch", "--job-dir", str(job)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   env=_environment(job), start_new_session=True, close_fds=True)
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if (job / WATCH_READY).exists():
            ready = _json_file(job / WATCH_READY)
            if ready.get("job_id") == spec["job_id"] and ready.get("pid") == process.pid:
                return process
            break
        if process.poll() is not None:
            break
        time.sleep(0.05)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    raise WorkerError("The independent watchdog did not become ready; no container was launched")


def _finish(job, result):
    _write(job / STATE_FILE, result)
    if result["status"] != "uncertain":
        _write(job / FINISHED_FILE, {"job_id": result["job_id"], "status": result["status"]})
    return result


def run_job(job_dir, image_id, kind, model, wall_time_seconds):
    """Execute one host-admitted job; retained state forbids every relaunch."""
    _parameters(image_id, kind, model, wall_time_seconds)
    job, identifier = _identifier(job_dir)
    if (job / STATE_FILE).exists() or (job / STATE_FILE).is_symlink():
        raise WorkerError("Worker state already exists; a failed or interrupted job cannot resume")
    job, identifier = _layout(job, kind)
    attestation = attest()
    command = _command(job, identifier, image_id, kind, model, wall_time_seconds, attestation)
    allowance = wall_time_seconds + (60 if kind == "smoke" else 300)
    started = time.time()
    spec = {"job_id": identifier, "container_name": "cosmic-" + identifier,
            "image_id": image_id, "docker": attestation["docker"], "kind": kind,
            "model": model, "wall_time_seconds": wall_time_seconds,
            "started_at": started, "deadline": started + allowance,
            "seccomp_profile": _seccomp_profile(job)}
    result = {**spec, "status": "launching", "exit_code": None, "result": None, "reason": None}
    try:
        _write(job / STATE_FILE, result, exclusive=True)
    except FileExistsError:
        raise WorkerError("Worker state already exists; this job cannot resume") from None
    process = None
    launch_attempted = False
    try:
        _write(job / DEADLINE_FILE, spec, exclusive=True)
        _write(job / "container-command.json", {"argv": command}, exclusive=True)
        (job / "docker-client").mkdir(mode=0o700)
        _start_watchdog(job, spec)
        if _seccomp_profile(job) != spec["seccomp_profile"]:
            raise WorkerError("The frozen seccomp profile changed before container launch")
        with (job / "outer-stdout.log").open("xb") as stdout, (job / "outer-stderr.log").open("xb") as stderr:
            if time.time() >= spec["deadline"]:
                raise WorkerError("The worker deadline expired before container launch")
            # Popen can be interrupted after creating a child but before it
            # returns a handle. Reconciliation cannot depend on that handle.
            launch_attempted = True
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       env=_environment(job, attestation), start_new_session=True, close_fds=True)
            result["status"] = "running"
            _write(job / STATE_FILE, result)
            result["exit_code"] = process.wait(timeout=max(0.01, spec["deadline"] - time.time()))
        termination = _stop_owned(job, spec)
        if termination != "stopped":
            result.update(status="uncertain", reason="Container termination could not be reconciled")
        elif result["exit_code"] != 0:
            result.update(status="failed", reason="The inner container failed; inspect retained outer logs")
        else:
            report = _json_file(job / "output/report/result.json")
            result.update(status="completed", result=report)
    except (OSError, WorkerError, subprocess.SubprocessError, KeyboardInterrupt):
        termination = _stop_owned(job, spec) if launch_attempted else "absent"
        # An interrupted Docker client may still have a create/start request in
        # flight. A momentary absence must not cancel its independent watchdog.
        uncertain = launch_attempted and termination != "stopped"
        result.update(status="uncertain" if uncertain else "failed",
                      reason="Worker launch, execution, deadline or result verification failed; job is not resumable")
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                result.update(status="uncertain", reason="Docker client termination could not be reconciled")
    result["finished_at"] = time.time()
    return _finish(job, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("attest")
    watcher = subparsers.add_parser("watch")
    watcher.add_argument("--job-dir", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--job-dir", type=Path, required=True)
    run.add_argument("--image-id", required=True)
    run.add_argument("--kind", choices=("smoke", "research"), required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--wall-time-seconds", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "watch":
            watch(args.job_dir)
            return 0
        result = attest() if args.command == "attest" else run_job(
            args.job_dir, args.image_id, args.kind, args.model, args.wall_time_seconds)
        print(json.dumps(result, allow_nan=False))
        return 0 if args.command == "attest" or result["status"] == "completed" else 1
    except WorkerError as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
