"""Prepare isolated maintenance workspaces; never launch models or sandboxes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SOURCE_DIRS = {"embryo_harness", "configs", "prompts", "resources", "tests"}
ROOT_FILES = {"harness.py", "README.md", "pyproject.toml", "uv.lock", ".gitignore",
              "PLAN.md", "START.md", "sandbox_setup.py", "team.py", "campaign.py"}
SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".lock", ""}
EXCLUDED_DIRS = {"runs", "exports", "submissions", "checks", "datasets",
                 "campaign", "__pycache__", "node_modules"}
ROLES = (("coordinator", "cosmic-coordinator", 1, "3g"),
         ("worker-a", "cosmic-worker-a", 4, "10g"),
         ("uploader", "cosmic-uploader", 1, "3g"))
REQUIRED_FLAGS = ("--name", "--cpus", "--memory", "--skills")
NOTICE = "Environment provisioning only. Use team.py for communication checks and campaign.py for the finite pilot."


class SetupError(Exception):
    pass


def _allowed_name(name: str) -> bool:
    return (not name.startswith(".") and ".local." not in name
            and not name.endswith(".local"))


def source_files(root: Path):
    """Enumerate only regular source files without traversing symlinks."""
    candidates = [root / "AGENTS.md", root / "ai/mcp/codegraph/README.md"]
    candidates.extend(root / "ai" / name for name in sorted(ROOT_FILES))
    for name in sorted(SOURCE_DIRS):
        directory = root / "ai" / name
        if directory.is_symlink() or not directory.is_dir():
            continue
        for current, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = sorted(d for d in dirs if _allowed_name(d)
                             and d not in EXCLUDED_DIRS
                             and not (Path(current) / d).is_symlink())
            candidates.extend(Path(current) / f for f in sorted(files)
                              if _allowed_name(f) and Path(f).suffix in SUFFIXES)
    for path in candidates:
        parents = path.relative_to(root).parents
        if (not path.is_symlink() and path.is_file()
                and not any((root / parent).is_symlink() for parent in parents)):
            yield path


def _copy_regular(source: Path, destination: Path) -> dict:
    """Reject a final-component symlink even if the source changed during copying."""
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SetupError(f"Source is not a regular file: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with destination.open("xb") as output:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                output.write(block)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SetupError(f"Source changed during copying: {source}")
    destination.chmod(0o755 if before.st_mode & 0o111 else 0o644)
    return {"bytes": after.st_size, "sha256": digest.hexdigest()}


def _roles(destination: Path) -> list[dict]:
    return [{"role": role, "name": name, "cpus": cpus, "memory": memory,
             "workspace": str(destination / "workspaces" / role),
             "inbox": str(destination / "mailboxes" / role / "inbox"),
             "outbox": str(destination / "mailboxes" / role / "outbox")}
            for role, name, cpus, memory in ROLES]


def _commands(roles: list[dict]) -> dict:
    commands = []
    for role in roles:
        argv = ["sbx", "create", "--name", role["name"], "--cpus", str(role["cpus"]),
                "--memory", role["memory"], "--skills", "off", "codex", role["workspace"]]
        if role["role"] == "coordinator":
            argv.append(str(Path(role["inbox"]).parent.parent))
        else:
            argv.extend([role["inbox"] + ":ro", role["outbox"]])
        commands.append({"role": role["role"], "argv": argv})
    return {"version": 1, "notice": NOTICE, "commands": commands}


def _write_json(path: Path, value: dict):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o444)


def bootstrap_path(root: Path) -> Path:
    root = root.resolve(strict=True)
    for path in (root / "ai", root / "ai/campaign", root / "ai/campaign/bootstrap"):
        if path.is_symlink():
            raise SetupError(f"Refusing a symlink in the bootstrap destination: {path}")
    return root / "ai/campaign/bootstrap"


def prepare(root: Path) -> dict:
    root = root.resolve(strict=True)
    destination = bootstrap_path(root)
    if destination.exists():
        raise SetupError(f"Bootstrap already exists; refusing to overwrite: {destination}")
    required = ("AGENTS.md", "ai/harness.py", "ai/README.md", "ai/PLAN.md",
                "ai/START.md", "ai/prompts/competition.md", "ai/prompts/common.md")
    sources = list(source_files(root))
    available = {str(p.relative_to(root)) for p in sources}
    missing = set(required) - available
    if missing:
        raise SetupError("Missing required regular source files: " + ", ".join(sorted(missing)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / ".bootstrap.prepare.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SetupError(f"Preparation lock exists; inspect it before retrying: {lock}") from None
    staging = None
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()) + "\n")
        if destination.exists():
            raise SetupError(f"Bootstrap already exists; refusing to overwrite: {destination}")
        staging = Path(tempfile.mkdtemp(prefix=".bootstrap-staging-", dir=destination.parent))
        roles = _roles(destination)
        reference = None
        for role in roles:
            workspace = staging / "workspaces" / role["role"]
            records = {str(p.relative_to(root)): _copy_regular(p, workspace / p.relative_to(root))
                       for p in sources}
            if reference is not None and records != reference:
                raise SetupError("Source changed between role copies; prepare again after edits finish")
            reference = records
            for direction in ("inbox", "outbox"):
                (staging / "mailboxes" / role["role"] / direction).mkdir(parents=True)
        control = staging / "control"
        control.mkdir()
        _write_json(control / "commands.json", _commands(roles))
        manifest = {"version": 1, "notice": NOTICE, "campaign_runtime_ready": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_root": str(root), "roles": roles, "sources": reference,
                    "commands_sha256": hashlib.sha256((control / "commands.json").read_bytes()).hexdigest()}
        _write_json(control / "manifest.json", manifest)
        if destination.exists() or destination.is_symlink():
            raise SetupError(f"Bootstrap appeared during preparation; refusing to overwrite: {destination}")
        staging.rename(destination)
        staging = None
        return {"bootstrap": str(destination), "notice": NOTICE,
                "manifest": str(destination / "control/manifest.json"),
                "commands": str(destination / "control/commands.json")}
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.unlink()


def command_lines(root: Path) -> list[str]:
    destination = bootstrap_path(root)
    control = destination / "control"
    for path in (control, control / "manifest.json", control / "commands.json"):
        if path.is_symlink():
            raise SetupError(f"Refusing a symlink in bootstrap control files: {path}")
    manifest = json.loads((control / "manifest.json").read_text(encoding="utf-8"))
    payload = (control / "commands.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest.get("commands_sha256"):
        raise SetupError("Bootstrap commands do not match their manifest")
    commands = json.loads(payload)
    if commands != _commands(_roles(destination)):
        raise SetupError("Bootstrap commands differ from the expected isolated mounts and role limits")
    return ["# " + NOTICE] + [shlex.join(item["argv"]) for item in commands["commands"]]


def doctor() -> dict:
    """Inspect only host support and CLI help; discard raw subprocess output."""
    system = platform.system()
    result = {"ok": False, "platform": system, "notice": NOTICE}
    if system != "Darwin":
        return dict(result, message="Run doctor in the macOS Terminal on the Docker Desktop host, "
                    "not inside this sandbox. This command does not provision anything.")
    executable = shutil.which("sbx")
    result["sbx_available"] = executable is not None
    if executable is None:
        return dict(result, message="Install or enable Docker Sandboxes on the Mac host so sbx is on PATH.")
    replies = {}
    for name, arguments in (("version", ["version"]), ("create_help", ["create", "--help"])):
        try:
            reply = subprocess.run([executable, *arguments], capture_output=True, text=True,
                                   timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return dict(result, message=f"sbx {name} could not complete within its bounded check.")
        if reply.returncode:
            return dict(result, message=f"sbx {name} failed; inspect the installation on the Mac host.")
        replies[name] = reply.stdout
    version = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9._-]+)?)\b", replies["version"])
    result["version"] = "sbx " + version.group(1) if version else None
    result["create_flags"] = {flag: bool(re.search(r"(?<![\w-])" + re.escape(flag) + r"(?=[\s,=]|$)",
                                                 replies["create_help"])) for flag in REQUIRED_FLAGS}
    result["ok"] = bool(version) and all(result["create_flags"].values())
    result["message"] = ("Host CLI supports the generated flags; authentication and campaign runtime were not tested."
                         if result["ok"] else "The host sbx version/help does not confirm the required create interface.")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "commands", "doctor"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    try:
        if args.command == "commands":
            print("\n".join(command_lines(root)))
        else:
            result = doctor() if args.command == "doctor" else prepare(root)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if args.command == "doctor" and not result["ok"]:
                return 1
        return 0
    except (SetupError, OSError, ValueError) as error:
        print(f"sandbox setup: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
