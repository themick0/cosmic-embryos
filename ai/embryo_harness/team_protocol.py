"""Immutable local messages for deterministic, model-free role preflight."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import tempfile


MAX_BYTES = 64 * 1024
ROLES = {"coordinator", "worker-a", "uploader"}
REQUEST_FIELDS = {"version", "id", "kind", "role", "workspace", "nonce", "created_at", "probe_sha256"}
CHECK_FIELDS = {"competition_rules", "common_prompt", "harness", "codex_available", "docker_available"}
RESPONSE_FIELDS = {"version", "id", "kind", "role", "workspace", "nonce", "python_version",
                   "platform", "checks", "runtime_ready", "probe_sha256", "request_sha256"}


class ProtocolError(ValueError):
    pass


def _identifier(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ProtocolError("Message IDs and nonces must be lowercase UUID hex strings")
    return value


def _no_symlinks(path: Path):
    if ".." in path.parts:
        raise ProtocolError("Mailbox paths must not contain parent traversal")
    for item in (path.absolute(), *path.absolute().parents):
        if item.is_symlink():
            raise ProtocolError("Symlinks are not permitted in mailbox paths")


def _directory(path: Path) -> Path:
    path = Path(path)
    _no_symlinks(path)
    if not path.is_dir():
        raise ProtocolError("Mailbox directory does not exist")
    return path


def _encode(message: dict) -> bytes:
    if not isinstance(message, dict):
        raise ProtocolError("A message must be a JSON object")
    try:
        payload = (json.dumps(message, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ProtocolError("Message is not valid finite JSON") from None
    if len(payload) > MAX_BYTES:
        raise ProtocolError("Message exceeds the 64 KiB limit")
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ProtocolError("Non-finite JSON constants are not permitted")


def read_message(path: Path) -> dict:
    path = Path(path)
    _no_symlinks(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ProtocolError("A mailbox message must be a regular file")
        if metadata.st_size > MAX_BYTES:
            raise ProtocolError("Message exceeds the 64 KiB limit")
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ProtocolError("Message exceeds the 64 KiB limit")
    try:
        message = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ProtocolError("Message is not a valid UTF-8 JSON object") from None
    if not isinstance(message, dict):
        raise ProtocolError("A message must be a JSON object")
    return message


def _sync_directory(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(directory: Path, message: dict) -> Path:
    """Publish without overwriting; an identical replay returns the existing path."""
    directory = _directory(directory)
    if not isinstance(message, dict):
        raise ProtocolError("A message must be a JSON object")
    identifier = _identifier(message.get("id"))
    payload = _encode(message)
    destination = directory / (identifier + ".json")
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + identifier + ".", suffix=".tmp", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            if _encode(read_message(destination)) != payload:
                raise ProtocolError("Message ID already exists with different content") from None
        _sync_directory(directory)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def parse_preflight_request(message: dict) -> dict:
    if not isinstance(message, dict) or set(message) != REQUEST_FIELDS:
        raise ProtocolError("Preflight request has unexpected or missing fields")
    if type(message["version"]) is not int or message["version"] != 1 or message["kind"] != "preflight":
        raise ProtocolError("Unsupported request version or kind")
    _identifier(message["id"])
    _identifier(message["nonce"])
    if not isinstance(message["probe_sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", message["probe_sha256"]) is None:
        raise ProtocolError("probe_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(message["role"], str) or message["role"] not in ROLES:
        raise ProtocolError("Unsupported preflight role")
    workspace = message["workspace"]
    if not isinstance(workspace, str) or not Path(workspace).is_absolute() or "\x00" in workspace:
        raise ProtocolError("Request workspace must be an absolute path")
    timestamp = message["created_at"]
    if not isinstance(timestamp, str) or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", timestamp) is None:
        raise ProtocolError("created_at must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise ProtocolError("created_at must be an ISO UTC timestamp") from None
    if parsed.utcoffset() != timedelta(0):
        raise ProtocolError("created_at must use UTC")
    return message


def request_sha256(request: dict) -> str:
    request = parse_preflight_request(request)
    payload = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_preflight_result(message: dict, request: dict) -> dict:
    request = parse_preflight_request(request)
    if not isinstance(message, dict) or set(message) != RESPONSE_FIELDS:
        raise ProtocolError("Preflight result has unexpected or missing fields")
    if type(message["version"]) is not int or message["version"] != 1 or message["kind"] != "preflight_result":
        raise ProtocolError("Unsupported result version or kind")
    for key in ("id", "nonce", "role", "workspace", "probe_sha256"):
        if message[key] != request[key]:
            raise ProtocolError("Preflight result identity does not match the request")
    if message["request_sha256"] != request_sha256(request):
        raise ProtocolError("Preflight result does not match the canonical request hash")
    if message["runtime_ready"] is not False:
        raise ProtocolError("Preflight cannot declare the campaign runtime ready")
    for key in ("python_version", "platform"):
        if not isinstance(message[key], str) or not message[key]:
            raise ProtocolError("Preflight result is missing runtime metadata")
    checks = message["checks"]
    if not isinstance(checks, dict) or set(checks) != CHECK_FIELDS or any(type(v) is not bool for v in checks.values()):
        raise ProtocolError("Preflight checks must contain the required boolean fields")
    return message


def _regular_probe(path: Path) -> bool:
    try:
        _no_symlinks(path)
        return path.is_file()
    except (OSError, ProtocolError):
        return False


def handle_preflight(workspace: Path, inbox: Path, outbox: Path, role: str, request_id: str) -> dict:
    """Handle only the named request; never dispatch work or invoke a model."""
    _identifier(request_id)
    if role not in ROLES:
        raise ProtocolError("Unsupported preflight role")
    workspace = Path(workspace).resolve(strict=True)
    if workspace != Path.cwd().resolve():
        raise ProtocolError("Run the role handler from its declared workspace")
    inbox, outbox = _directory(inbox), _directory(outbox)
    request = parse_preflight_request(read_message(inbox / (request_id + ".json")))
    if request["id"] != request_id or request["role"] != role or request["workspace"] != str(workspace):
        raise ProtocolError("Request ID, role or workspace does not match this handler")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != request["probe_sha256"]:
        raise ProtocolError("Executing preflight code does not match probe_sha256")
    destination = outbox / (request_id + ".json")
    if destination.exists() or destination.is_symlink():
        return parse_preflight_result(read_message(destination), request)
    response = {"version": 1, "id": request_id, "kind": "preflight_result", "role": role,
                "workspace": str(workspace), "nonce": request["nonce"],
                "probe_sha256": request["probe_sha256"], "request_sha256": request_sha256(request),
                "python_version": sys.version.split()[0],
                "platform": platform.system(),
                "checks": {"competition_rules": _regular_probe(workspace / "ai/prompts/competition.md"),
                           "common_prompt": _regular_probe(workspace / "ai/prompts/common.md"),
                           "harness": _regular_probe(workspace / "ai/harness.py"),
                           "codex_available": shutil.which("codex") is not None,
                           "docker_available": shutil.which("docker") is not None},
                "runtime_ready": False}
    publish(outbox, response)
    return response


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--inbox", required=True, type=Path)
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=sorted(ROLES))
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = handle_preflight(args.workspace, args.inbox, args.outbox, args.role, args.request_id)
        print(json.dumps(result, indent=2))
        return 0
    except (ProtocolError, OSError) as error:
        print(f"Role preflight: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
