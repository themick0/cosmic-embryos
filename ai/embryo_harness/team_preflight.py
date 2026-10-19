"""Check existing sandbox mailboxes without launching a model or research run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import sandbox_setup
from .team_protocol import ProtocolError, publish, read_message


ROLE_NAMES = tuple(row[0] for row in sandbox_setup.ROLES)
ENTRY = '''"""Execute one deterministic mailbox check; never call a model."""
import argparse
from pathlib import Path
from team_protocol import handle_preflight

parser = argparse.ArgumentParser(description=__doc__)
for name in ("workspace", "inbox", "outbox", "role", "request-id"):
    parser.add_argument("--" + name, required=True)
args = parser.parse_args()
handle_preflight(Path(args.workspace), Path(args.inbox), Path(args.outbox),
                 args.role, args.request_id)
'''


def safe_path(root: Path, relative: str) -> Path:
    """Reject symlinks before accessing a maintenance runtime path."""
    path = root
    for part in Path(relative).parts:
        if part in ("..", "/"):
            raise ValueError("Expected a path below the maintenance root")
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Symlink is not a maintenance runtime path: {path}")
    return path


def install(root: Path) -> dict[str, Path]:
    """Add a content-addressed probe release without changing harness sources."""
    base = sandbox_setup.bootstrap_path(root)
    sandbox_setup.command_lines(root)
    source = safe_path(root, "ai/embryo_harness/team_protocol.py").read_bytes()
    files = {"probe.py": ENTRY.encode(), "team_protocol.py": source}
    revision = hashlib.sha256(ENTRY.encode() + b"\0" + source).hexdigest()
    deployed = {}
    for role in ROLE_NAMES:
        relative = f"workspaces/{role}/ai/campaign/preflight/{revision}"
        destination = safe_path(base, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            staging = Path(tempfile.mkdtemp(prefix=".probe-", dir=destination.parent))
            try:
                for name, data in files.items():
                    target = staging / name
                    with target.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    target.chmod(0o444)
                try:
                    staging.rename(destination)
                except OSError:
                    if not destination.is_dir():
                        raise
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        for name, data in files.items():
            if safe_path(destination, name).read_bytes() != data:
                raise ValueError(f"Existing preflight release differs: {destination}")
        deployed[role] = destination / "probe.py"
    receipts = safe_path(base, "control/preflight-deployments")
    receipts.mkdir(parents=True, exist_ok=True)
    publish(receipts, {"version": 1, "id": uuid.uuid5(uuid.NAMESPACE_URL, revision).hex,
                      "kind": "preflight_deployment", "revision": revision,
                      "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                      "probes": {role: str(path) for role, path in deployed.items()},
                      "runtime_ready": False})
    return deployed


def expected_reply(response: dict, request: dict) -> None:
    for key in ("version", "id", "nonce", "role", "workspace", "probe_sha256"):
        if response.get(key) != request[key]:
            raise ValueError(f"Reply identity mismatch: {key}")
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=False).encode()).hexdigest()
    if response.get("request_sha256") != request_hash:
        raise ValueError("Reply request hash mismatch")
    if response.get("kind") != "preflight_result" or response.get("runtime_ready") is not False:
        raise ValueError("Reply is not a preflight-only result")
    checks = response.get("checks")
    required = {"competition_rules", "common_prompt", "harness", "codex_available", "docker_available"}
    if not isinstance(checks, dict) or set(checks) != required:
        raise ValueError("Reply has an invalid check set")
    if any(type(value) is not bool for value in checks.values()):
        raise ValueError("Reply checks must be booleans")
    if not isinstance(response.get("python_version"), str) or not isinstance(response.get("platform"), str):
        raise ValueError("Reply lacks environment metadata")


def check(root: Path, roles: list[str], timeout: int = 45) -> dict:
    """Start only finite Python probes through host sbx exec, one role at a time."""
    if platform.system() != "Darwin":
        raise ValueError("Run this command in the Mac host terminal, outside the Linux sandbox")
    executable = shutil.which("sbx")
    if executable is None:
        raise ValueError("sbx is not on the Mac host PATH")
    if not roles or len(roles) != len(set(roles)) or any(role not in ROLE_NAMES for role in roles):
        raise ValueError("Choose each known sandbox role at most once")
    if not 5 <= timeout <= 60:
        raise ValueError("Probe timeout must be between 5 and 60 seconds")
    programs = install(root)
    base = sandbox_setup.bootstrap_path(root)
    report = {"version": 1, "id": uuid.uuid4().hex, "kind": "team_preflight_report",
              "created_at": datetime.now(timezone.utc).isoformat(), "roles": [],
              "runtime_ready": False, "model_authentication": "not_tested",
              "research_started": False, "official_uploads": False}
    definitions = {row["role"]: row for row in sandbox_setup._roles(base)}
    for role in roles:
        info = definitions[role]
        inbox = safe_path(base, f"mailboxes/{role}/inbox")
        outbox = safe_path(base, f"mailboxes/{role}/outbox")
        request = {"version": 1, "id": uuid.uuid4().hex, "kind": "preflight", "role": role,
                   "workspace": info["workspace"], "nonce": uuid.uuid4().hex,
                   "probe_sha256": hashlib.sha256(programs[role].with_name("team_protocol.py").read_bytes()).hexdigest(),
                   "created_at": datetime.now(timezone.utc).isoformat()}
        argv = [executable, "exec", "-w", info["workspace"], info["name"],
                "python3", str(programs[role]), "--workspace", info["workspace"],
                "--inbox", str(inbox), "--outbox", str(outbox), "--role", role,
                "--request-id", request["id"]]
        row = {"role": role, "sandbox": info["name"], "request_id": request["id"],
               "communication_ok": False, "environment_checks_ok": False}
        try:
            publish(inbox, request)
            # Never collect raw shell output, environment values or authentication data.
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout, check=False)
            row["exit_code"] = result.returncode
            response_path = outbox / (request["id"] + ".json")
            deadline = time.monotonic() + (5 if result.returncode == 0 else 0)
            while not response_path.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            response = read_message(response_path)
            expected_reply(response, request)
            row["communication_ok"] = result.returncode == 0
            row["checks"] = response["checks"]
            row["python_version"] = response["python_version"]
            row["platform"] = response["platform"]
            version = tuple(int(part) for part in response["python_version"].split(".")[:2])
            needed = ["competition_rules", "common_prompt", "harness", "codex_available"]
            if role == "worker-a":
                needed.append("docker_available")
            row["environment_checks_ok"] = (row["communication_ok"] and version >= (3, 11)
                                             and response["platform"] == "Linux"
                                             and all(response["checks"][key] for key in needed))
        except subprocess.TimeoutExpired:
            row["error"] = "Timed out; a late reply may remain in the outbox. No model was launched."
        except (OSError, ValueError, ProtocolError):
            row["error"] = "No valid matching reply; inspect the named sandbox and retain its mailbox files."
        report["roles"].append(row)
    report["communication_ok"] = all(row["communication_ok"] for row in report["roles"])
    report["environment_checks_ok"] = all(row["environment_checks_ok"] for row in report["roles"])
    reports = safe_path(base, "control/preflight-reports")
    reports.mkdir(parents=True, exist_ok=True)
    path = publish(reports, report)
    return {**report, "report_path": str(path)}


def latest(root: Path) -> dict:
    base = sandbox_setup.bootstrap_path(root)
    directory = safe_path(base, "control/preflight-reports")
    records = [read_message(path) for path in sorted(directory.glob("*.json"))]
    if not records:
        return {"runtime_ready": False, "message": "No host communication check has been recorded"}
    return max(records, key=lambda value: value["created_at"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="Install only deterministic probes into existing role workspaces")
    probe = commands.add_parser("check", help="Check real sandbox mailbox communication from the Mac host")
    probe.add_argument("--role", choices=ROLE_NAMES, action="append")
    probe.add_argument("--timeout", type=int, default=45)
    commands.add_parser("status", help="Read the latest host preflight report without launching anything")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    try:
        if args.command == "install":
            result = {"runtime_ready": False, "probe_paths": {k: str(v) for k, v in install(root).items()}}
        elif args.command == "check":
            result = check(root, args.role or list(ROLE_NAMES), args.timeout)
        else:
            result = latest(root)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if args.command == "check" and not result["environment_checks_ok"] else 0
    except (OSError, ValueError, sandbox_setup.SetupError) as error:
        print(f"team preflight: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
