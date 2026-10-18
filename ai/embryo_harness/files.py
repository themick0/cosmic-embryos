from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class HarnessError(Exception):
    pass


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def inside(root: Path, relative: str) -> Path:
    """Reject absolute paths, traversal and symlinks, including parent components."""
    p = Path(relative)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise HarnessError(f"Expected a relative path inside {root}: {relative}")
    candidate = root / p
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise HarnessError(f"Path escapes {root}: {relative}")
    for part in [candidate, *candidate.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise HarnessError(f"Symlink is not an artifact/input: {relative}")
    return candidate


def record(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise HarnessError(f"Missing regular file: {path}")
    before = path.stat()
    sha = digest(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise HarnessError(f"File changed while hashing: {path}")
    return {"bytes": after.st_size, "sha256": sha}


def check_records(root: Path, records: dict) -> None:
    for name, expected in records.items():
        if record(inside(root, name)) != expected:
            raise HarnessError(f"Integrity check failed: {root / name}")
