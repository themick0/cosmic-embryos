from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

from . import codex
from .files import HarnessError, check_records, digest, inside, read_json, record, utc, write_json
from .validation import libraries, validate, validate_storage


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "summary", "predictions", "method_summary", "sources", "reproduction_files"],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "blocked"]},
        "summary": {"type": "string"},
        "predictions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["board", "path"],
            "properties": {"board": {"type": "string"}, "path": {"type": "string"}}}},
        "method_summary": {"type": "string"}, "sources": {"type": "string"},
        "reproduction_files": {"type": "array", "items": {"type": "string"}},
    },
}

CONFIG_KEYS = {"version", "task", "boards", "targets", "inputs", "prompt_files", "model",
               "reasoning_effort", "sandbox", "network_access", "wall_time_seconds", "seed",
               "codex_executable"}
OPTIONAL_CONFIG_KEYS = {"codex_provider"}
IGNORED = {"runs", "exports", ".venv", "__pycache__", ".pytest_cache", ".git"}
SOURCE_DIRS = {"embryo_harness", "configs", "prompts", "resources", "tests"}
SOURCE_ROOT_FILES = {"harness.py", "README.md", "pyproject.toml", "uv.lock", ".gitignore"}


def python_packages():
    return dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                       if d.metadata["Name"]))


def source_files(ai: Path):
    for directory, dirs, files in os.walk(ai):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not d.startswith(".")
                         and not (Path(directory) / d).is_symlink())
        if Path(directory) == ai:
            dirs[:] = [d for d in dirs if d in SOURCE_DIRS]
        for name in sorted(files):
            p = Path(directory) / name
            if Path(directory) == ai and name not in SOURCE_ROOT_FILES:
                continue
            if name.startswith(".") and name != ".gitignore":
                continue
            if ".local." not in name and p.suffix in {".py", ".md", ".json", ".txt", ".toml", ".lock", ""} and not p.is_symlink():
                yield p


def load_config(path: Path, root: Path, model: str | None = None) -> dict:
    config = read_json(path)
    if (not isinstance(config, dict) or not CONFIG_KEYS <= set(config)
            or not set(config) <= CONFIG_KEYS | OPTIONAL_CONFIG_KEYS):
        raise HarnessError(f"Config requires: {sorted(CONFIG_KEYS)}; "
                           f"optional: {sorted(OPTIONAL_CONFIG_KEYS)}")
    config["codex_provider"] = codex.validate_provider(config.get("codex_provider", "openai"))
    if model is not None:
        config["model"] = model
    if config["version"] != 1 or config["task"] not in ("T1", "T2", "T3"):
        raise HarnessError("Unsupported config version/task")
    if not isinstance(config["model"], str) or not config["model"].strip():
        raise HarnessError("Specify an exact model with --model or in the config; no implicit default")
    if config["reasoning_effort"] not in ("low", "medium", "high", "xhigh"):
        raise HarnessError("Unsupported reasoning_effort")
    if config["sandbox"] not in ("workspace-write", "danger-full-access"):
        raise HarnessError("sandbox must be workspace-write or danger-full-access")
    if type(config["network_access"]) is not bool:
        raise HarnessError("network_access must be a boolean")
    if config["sandbox"] == "danger-full-access" and not config["network_access"]:
        raise HarnessError("danger-full-access cannot enforce network_access=false")
    if type(config["wall_time_seconds"]) is not int or config["wall_time_seconds"] <= 0:
        raise HarnessError("wall_time_seconds must be a positive integer")
    if type(config["seed"]) is not int:
        raise HarnessError("seed must be an integer")
    for key in ("boards", "inputs", "prompt_files"):
        values = config[key]
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            raise HarnessError(f"{key} must be a nonempty list of strings")
        if len(set(values)) != len(values):
            raise HarnessError(f"Duplicate {key}")
    required_prompts = ["ai/prompts/competition.md", "ai/prompts/common.md"]
    if config["prompt_files"][:2] != required_prompts or len(config["prompt_files"]) < 3:
        raise HarnessError("prompt_files must start with ai/prompts/competition.md, then "
                           "ai/prompts/common.md, followed by a task prompt")
    if not isinstance(config["targets"], dict) or set(config["targets"]) != set(config["boards"]):
        raise HarnessError("targets must describe every configured board")
    panels = read_json(root / "ai/resources/panels/index.json")
    for board in config["boards"]:
        if board not in panels or panels[board]["task"] != config["task"]:
            raise HarnessError(f"Unknown board or wrong task: {board}")
    for name in config["inputs"] + config["prompt_files"]:
        if not inside(root, name).is_file():
            raise HarnessError(f"Missing input/prompt: {name}")
    return config


def prepare(root: Path, config_path: Path, model: str | None = None) -> Path:
    libraries()  # Fail before a run is created if validation dependencies are absent.
    config = load_config(config_path, root, model)
    runtime = codex.probe(config["codex_executable"])
    run_id = utc().replace(":", "").replace("+", "_") + "-" + uuid.uuid4().hex[:8]
    run = root / "ai/runs" / run_id
    run.mkdir(parents=True)
    (run / "work").mkdir()
    (run / "frozen").mkdir()
    try:
        for name in config["inputs"]:
            if Path(name).suffix.lower() == ".h5ad":
                validate_storage(inside(root, name))
        inputs = {name: record(inside(root, name)) for name in config["inputs"]}
        source = {}
        for p in source_files(root / "ai"):
            relative = p.relative_to(root / "ai")
            dest = run / "frozen/harness" / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
            source[str(relative)] = record(dest)
        panels = root / "ai/resources/panels"
        check_records(panels, read_json(panels / "provenance.json")["files"])
        shutil.copytree(panels, run / "frozen/panels")
        check_records(run / "frozen/panels", read_json(panels / "provenance.json")["files"])
        prompt_parts = []
        for i, name in enumerate(config["prompt_files"]):
            p = inside(root, name)
            target = run / "frozen/prompts" / f"{i:02d}-{p.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            prompt_parts.append(target.read_text())
        context = {"team": "Cosmic Embryos", "track": "Agent Team", "task": config["task"],
                   "boards": config["boards"], "targets": config["targets"],
                   "seed": config["seed"], "wall_time_seconds": config["wall_time_seconds"],
                   "inputs": [{"path": str(root / name), **info} for name, info in inputs.items()],
                   "panels_directory": str(run / "frozen/panels"),
                   "harness_entrypoint": str(run / "frozen/harness/harness.py"),
                   "python_executable": sys.executable,
                   "network_access": config["network_access"],
                   "official_submissions_allowed": False}
        write_json(run / "work/context.json", context)
        prompt = "\n\n".join(prompt_parts) + "\n\nLocked run context:\n" + json.dumps(context, indent=2)
        (run / "frozen/prompt.txt").write_text(prompt + "\n")
        write_json(run / "frozen/config.json", config)
        write_json(run / "frozen/final.schema.json", SCHEMA)
        argv = codex.command(run, config, runtime)
        write_json(run / "frozen/command.json", argv)
        frozen = {str(p.relative_to(run)): record(p) for p in sorted((run / "frozen").rglob("*"))
                  if p.is_file()}
        frozen["work/context.json"] = record(run / "work/context.json")
        lock = {"schema_version": 1, "prepared_at": utc(), "run_id": run_id,
                "root": str(root), "config": config, "runtime": runtime,
                "environment": {"python": sys.version, "python_executable": sys.executable,
                                "platform": platform.platform(), "path": os.environ.get("PATH", ""),
                                "python_packages": python_packages()},
                "inputs": inputs, "source": source, "frozen": frozen,
                "prompt_sources": {name: record(inside(root, name)) for name in config["prompt_files"]}}
        write_json(run / "lock.json", lock)
        (run / "lock.sha256").write_text(digest(run / "lock.json") + "\n")
        write_json(run / "status.json", {"status": "prepared", "at": utc()})
        return run
    except Exception:
        write_json(run / "status.json", {"status": "preparation_failed", "at": utc()})
        raise


def verify(run: Path, *, inputs=True, outputs=True) -> dict:
    expected = (run / "lock.sha256").read_text().strip()
    if digest(run / "lock.json") != expected:
        raise HarnessError("Run lock has changed")
    lock = read_json(run / "lock.json")
    # Comparing hashes alone misses newly inserted files. The frozen tree is a
    # closed set; mutable experiment files in work/ are intentionally separate.
    expected_frozen = {name for name in lock["frozen"] if name.startswith("frozen/")}
    actual_frozen = {str(p.relative_to(run)) for p in (run / "frozen").rglob("*")
                     if p.is_file() or p.is_symlink()}
    if actual_frozen != expected_frozen:
        raise HarnessError("Frozen file set changed: "
                           f"missing={sorted(expected_frozen - actual_frozen)}, "
                           f"extra={sorted(actual_frozen - expected_frozen)}")
    check_records(run, lock["frozen"])
    if inputs:
        check_records(Path(lock["root"]), lock["inputs"])
    if outputs:
        receipt_exists = (run / "receipt.json").is_file()
        checksum_exists = (run / "receipt.sha256").is_file()
        completed = read_json(run / "status.json")["status"] == "completed"
        if (completed or receipt_exists or checksum_exists) and not (receipt_exists and checksum_exists):
            raise HarnessError("Output receipt and its checksum are required for a completed/sealed run")
    if outputs and receipt_exists:
        receipt = read_json(run / "receipt.json")
        if digest(run / "receipt.json") != (run / "receipt.sha256").read_text().strip():
            raise HarnessError("Output receipt has changed")
        if receipt["lock_sha256"] != expected:
            raise HarnessError("Output receipt belongs to a different run lock")
        check_records(run, receipt["files"])
    return lock


def summarize_trace(path: Path) -> dict:
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    events = 0
    completed = 0
    failed = False
    threads = []
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError) as e:
                raise HarnessError("Codex trajectory contains invalid JSONL") from e
            if not isinstance(event, dict):
                raise HarnessError("Codex trajectory event must be an object")
            events += 1
            if event.get("type") == "thread.started":
                threads.append(event.get("thread_id"))
            if event.get("type") == "turn.failed":
                failed = True
            if event.get("type") == "turn.completed":
                completed += 1
                for key in usage:
                    usage[key] += int(event.get("usage", {}).get(key, 0))
    if not events or not completed or failed:
        raise HarnessError("Trajectory has no successfully completed run, or contains a failed turn")
    return {"events": events, "completed_turns": completed, "thread_ids": threads, "usage": usage}


def collect(run: Path, config: dict) -> tuple[dict, dict]:
    final = read_json(run / "final.json")
    if not isinstance(final, dict) or set(final) != set(SCHEMA["required"]):
        raise HarnessError("Final response does not match the output contract")
    if final["status"] != "completed":
        raise HarnessError("Agent did not complete the task: " + str(final["summary"]))
    predictions = final["predictions"]
    if not isinstance(predictions, list) or any(not isinstance(p, dict) or set(p) != {"board", "path"}
                                              for p in predictions):
        raise HarnessError("Invalid predictions manifest")
    if sorted(p["board"] for p in predictions) != sorted(config["boards"]):
        raise HarnessError("Agent must select exactly one prediction for every configured board")
    artifacts = [final["method_summary"], final["sources"]]
    if not isinstance(final["reproduction_files"], list) or not final["reproduction_files"]:
        raise HarnessError("Agent must supply reproduction_files")
    artifacts.extend(final["reproduction_files"])
    reports = []
    for prediction in predictions:
        artifact = inside(run / "work", prediction["path"])
        reports.append(validate(artifact, prediction["board"], run / "frozen/panels"))
        artifacts.append(prediction["path"])
    files = {}
    for name in artifacts:
        if not isinstance(name, str):
            raise HarnessError("Artifact paths must be strings")
        artifact = inside(run / "work", name)
        files["work/" + name] = record(artifact)
    # Sources is deliberately a plain disclosure file, not inferred from scores or logs.
    if not isinstance(read_json(inside(run / "work", final["sources"])), list):
        raise HarnessError("sources must point to a JSON array (empty if no external sources)")
    return files, {"predictions": reports, "agent_summary": final["summary"]}


def execute(run: Path) -> dict:
    lock = verify(run, outputs=False)
    if read_json(run / "status.json")["status"] != "prepared":
        raise HarnessError("Run already attempted. Prepare a new run; logs are never overwritten")
    check_records(Path(lock["root"]) / "ai", lock["source"])
    check_records(Path(lock["root"]), lock["prompt_sources"])
    runtime = codex.probe(lock["config"]["codex_executable"])
    if runtime != lock["runtime"]:
        raise HarnessError("Codex version/entrypoint changed since preparation")
    if sys.executable != lock["environment"]["python_executable"] or sys.version != lock["environment"]["python"]:
        raise HarnessError("Python environment changed since preparation")
    if os.environ.get("PATH", "") != lock["environment"]["path"]:
        raise HarnessError("PATH changed since preparation")
    if python_packages() != lock["environment"]["python_packages"]:
        raise HarnessError("Python packages changed since preparation")
    # Exclusive creation prevents two processes from executing the same prepared run.
    try:
        with (run / "started.json").open("x") as f:
            json.dump({"configuration_locked_at": utc(), "pid": os.getpid(),
                       "lock_sha256": digest(run / "lock.json")}, f)
    except FileExistsError as e:
        raise HarnessError("Run already started; refusing to overwrite its evidence") from e
    write_json(run / "status.json", {"status": "running", "at": utc()})
    try:
        config = lock["config"]
        rc, timeout = codex.execute(read_json(run / "frozen/command.json"),
                                    run / "frozen/prompt.txt", run, config["wall_time_seconds"])
        if timeout:
            raise HarnessError(f"Wall-time budget exhausted ({config['wall_time_seconds']} seconds)")
        if rc:
            raise HarnessError(f"Codex exited with code {rc}; see stderr.log and trajectory.jsonl")
        trace = summarize_trace(run / "trajectory.jsonl")
        artifacts, result = collect(run, config)
        verify(run, outputs=False)
        result["trace"] = trace
        write_json(run / "validation.json", result)
        for name in ("trajectory.jsonl", "stderr.log", "final.json", "validation.json", "started.json"):
            artifacts[name] = record(run / name)
        write_json(run / "receipt.json", {"completed_at": utc(), "lock_sha256": digest(run / "lock.json"),
                                          "files": artifacts})
        (run / "receipt.sha256").write_text(digest(run / "receipt.json") + "\n")
        write_json(run / "status.json", {"status": "completed", "at": utc(), "result": result})
        return result
    except BaseException as e:
        status = "interrupted" if isinstance(e, (KeyboardInterrupt, SystemExit)) else "failed"
        write_json(run / "status.json", {"status": status, "at": utc(), "error": str(e)})
        raise


def bundle(run: Path) -> Path:
    lock = verify(run)
    if read_json(run / "status.json")["status"] != "completed" or not (run / "receipt.json").exists():
        raise HarnessError("Only completed and verified runs can be packaged for submission")
    export_root = run.parent.parent / "exports"
    export_root.mkdir(exist_ok=True)
    destination = export_root / run.name
    if destination.exists():
        raise HarnessError(f"Bundle already exists: {destination}")
    final = read_json(run / "final.json")
    groups = {
        "trajectory": ["trajectory.jsonl", "stderr.log", "final.json", "validation.json", "started.json",
                       "lock.json", "lock.sha256", "receipt.json", "receipt.sha256", "status.json"],
        "prompts": ["frozen/prompt.txt", "frozen/config.json", "frozen/final.schema.json", "work/context.json"]
                   + [name for name in lock["frozen"] if name.startswith("frozen/prompts/")],
        "harness": [name for name in lock["frozen"]
                    if name.startswith(("frozen/harness/", "frozen/panels/"))]
                   + ["frozen/command.json"]
                   + ["work/" + p for p in final["reproduction_files"]]
                   + ["work/" + final["method_summary"], "work/" + final["sources"]],
    }
    archived = {name for names in groups.values() for name in names}
    if not set(lock["frozen"]).issubset(archived):
        raise HarnessError("Evidence plan omits files from the frozen manifest")
    tmp = Path(tempfile.mkdtemp(prefix=".bundle-", dir=export_root))
    try:
        evidence = {}
        for kind, names in groups.items():
            path = tmp / (kind + ".tar.gz")
            with tarfile.open(path, "w:gz") as archive:
                for name in sorted(set(names)):
                    archive.add(inside(run, name), arcname=name, recursive=False)
            if path.stat().st_size > 200_000_000:
                raise HarnessError(f"{kind} exceeds the 200 MB evidence-file limit; full logs retained")
            evidence[path.name] = record(path)
        if sum(item["bytes"] for item in evidence.values()) > 600_000_000:
            raise HarnessError("Evidence exceeds 600 MB (team-wide usage must also be checked separately)")
        predictions = []
        for i, p in enumerate(final["predictions"]):
            dest = tmp / f"prediction-{i + 1}.h5ad"
            shutil.copyfile(inside(run / "work", p["path"]), dest)
            predictions.append({"board": p["board"], "file": dest.name,
                                "run_path": "work/" + p["path"], **record(dest)})
        # Detect any change between verification and copying/archiving.
        verify(run)
        write_json(tmp / "manifest.json", {"run_id": run.name,
                                          "lock_sha256": digest(run / "lock.json"),
                                          "agent_framework": "Cosmic Embryos harness / Codex CLI",
                                          "agent_model": lock["config"]["model"], "evidence": evidence,
                                          "predictions": predictions, "uploaded": False})
        (tmp / "manifest.sha256").write_text(digest(tmp / "manifest.json") + "\n")
        tmp.rename(destination)
    except BaseException:
        shutil.rmtree(tmp)
        raise
    return destination
