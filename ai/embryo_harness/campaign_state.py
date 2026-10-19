"""Host-owned finite session admissions, not an estimate of subscription tokens.

Keep this SQLite database under ai/campaign/control on one host. Never mount it
into role sandboxes. Callers own process observation and per-job wall deadlines;
this module neither starts processes nor infers that an uncertain process died.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import quote
import uuid


STATUSES = ("reserved", "launched", "uncertain", "completed", "failed")
ACTIVE = {"reserved", "launched", "uncertain"}
NOTICE = "Finite session admissions only; this does not translate 60% subscription allowance into a token budget."


class StateError(ValueError):
    pass


class AdmissionError(StateError):
    pass


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    def validate(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise StateError("JSON object keys must be strings")
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise StateError("Only JSON values are accepted")
    try:
        validate(value)
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(result.encode("utf-8")) > 65536:
            raise StateError("JSON value exceeds 64 KiB")
        return result
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise StateError("Expected finite JSON of at most 64 KiB") from None


def _path(path: Path, *, existing=True) -> Path:
    path = Path(path).absolute()
    if ".." in path.parts or tuple(path.parent.parts[-3:]) != ("ai", "campaign", "control"):
        raise StateError("Ledger must be directly under ai/campaign/control")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise StateError("Ledger paths cannot contain symlinks")
    if existing and (not path.exists() or not stat.S_ISREG(path.stat().st_mode)):
        raise StateError("Ledger must already exist as a regular file")
    return path


def _policy(policy):
    if not isinstance(policy, dict) or set(policy) != {"max_sessions", "max_active", "allowed_models"}:
        raise StateError("Policy requires max_sessions, max_active and allowed_models")
    if any(type(policy[key]) is not int or policy[key] < 1 for key in ("max_sessions", "max_active")):
        raise StateError("Session limits must be positive integers")
    if policy["max_active"] > policy["max_sessions"]:
        raise StateError("max_active cannot exceed max_sessions")
    models = policy["allowed_models"]
    if (not isinstance(models, list) or not models
            or any(not isinstance(model, str) or not model.strip() or model != model.strip() for model in models)
            or len(models) != len(set(models))):
        raise StateError("allowed_models must contain distinct nonempty model strings")
    return json.loads(_json(policy))


@contextmanager
def _transaction(path, *, write=True):
    path = _path(path)
    connection = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=rw", uri=True,
                                 timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA fullfsync=ON")
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def create(path: Path, policy: dict) -> dict:
    """Create once; an interrupted initialization is retained, never reset."""
    policy = _policy(policy)
    path = _path(path, existing=False)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise StateError("Ledger already exists; refusing to overwrite or reset it") from None
    os.close(descriptor)
    with _transaction(path) as connection:
        connection.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, policy TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES (1, 1, ?)", (_json(policy),))
        connection.execute("""CREATE TABLE jobs (
            assignment_id TEXT PRIMARY KEY, reservation_id TEXT UNIQUE NOT NULL,
            model TEXT NOT NULL, kind TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, launched_at TEXT,
            settled_at TEXT, uncertain_reason TEXT, usage_json TEXT)""")
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return snapshot(path)


def _identifier(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise StateError("assignment_id must be a lowercase UUID hex string")
    return value


def _job(row):
    result = dict(row)
    encoded = result.pop("usage_json")
    result["usage"] = json.loads(encoded) if encoded is not None else None
    return result


def _find(connection, assignment_id):
    row = connection.execute("SELECT * FROM jobs WHERE assignment_id=?", (_identifier(assignment_id),)).fetchone()
    if row is None:
        raise StateError("Unknown assignment reservation")
    return row


def _read_policy(connection):
    row = connection.execute("SELECT version, policy FROM metadata WHERE id=1").fetchone()
    if row is None or row["version"] != 1:
        raise StateError("Unsupported ledger schema")
    return _policy(json.loads(row["policy"]))


def reserve(path: Path, assignment_id: str, model: str, kind: str, payload_sha256: str) -> dict:
    _identifier(assignment_id)
    if not isinstance(model, str) or not model or kind not in ("smoke", "research"):
        raise StateError("Reservation requires an exact model and smoke or research kind")
    if not isinstance(payload_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", payload_sha256) is None:
        raise StateError("payload_sha256 must be a lowercase SHA-256 digest")
    with _transaction(path) as connection:
        policy = _read_policy(connection)
        existing = connection.execute("SELECT * FROM jobs WHERE assignment_id=?", (assignment_id,)).fetchone()
        if existing is not None:
            if (existing["model"], existing["kind"], existing["payload_sha256"]) != (model, kind, payload_sha256):
                raise StateError("Assignment ID already binds a different model, kind or payload")
            return _job(existing)
        if model not in policy["allowed_models"]:
            raise AdmissionError("Model is outside the campaign policy")
        rows = connection.execute("SELECT status FROM jobs").fetchall()
        if len(rows) >= policy["max_sessions"]:
            raise AdmissionError("Finite session limit exhausted; no automatic reset or refund")
        if sum(row["status"] in ACTIVE for row in rows) >= policy["max_active"]:
            raise AdmissionError("Active session limit reached; uncertain jobs retain their slots")
        connection.execute("""INSERT INTO jobs
            (assignment_id, reservation_id, model, kind, payload_sha256, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'reserved', ?)""",
            (assignment_id, uuid.uuid4().hex, model, kind, payload_sha256, _utc()))
        return _job(_find(connection, assignment_id))


def mark_launched(path: Path, assignment_id: str) -> dict:
    with _transaction(path) as connection:
        row = _find(connection, assignment_id)
        if row["status"] == "launched":
            return _job(row)
        if row["status"] != "reserved":
            raise StateError("Only a reserved job can be marked launched; this is not a retry permission")
        connection.execute("UPDATE jobs SET status='launched', launched_at=? WHERE assignment_id=?", (_utc(), assignment_id))
        return _job(_find(connection, assignment_id))


def mark_uncertain(path: Path, assignment_id: str, reason: str) -> dict:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise StateError("An uncertainty reason of at most 1000 characters is required")
    with _transaction(path) as connection:
        row = _find(connection, assignment_id)
        if row["status"] == "uncertain":
            if row["uncertain_reason"] != reason:
                raise StateError("Uncertainty is already recorded with a different reason")
            return _job(row)
        if row["status"] not in ACTIVE:
            raise StateError("Terminal jobs cannot become uncertain")
        connection.execute("UPDATE jobs SET status='uncertain', uncertain_reason=? WHERE assignment_id=?", (reason, assignment_id))
        return _job(_find(connection, assignment_id))


def settle(path: Path, assignment_id: str, status: str, usage: dict | None = None) -> dict:
    """Record a reconciled outcome; unknown usage stays None and is never invented."""
    if status not in ("completed", "failed") or (usage is not None and not isinstance(usage, dict)):
        raise StateError("Settlement requires completed/failed and a usage object or None")
    encoded = _json(usage) if usage is not None else None
    with _transaction(path) as connection:
        row = _find(connection, assignment_id)
        if row["status"] in ("completed", "failed"):
            if row["status"] != status or row["usage_json"] != encoded:
                raise StateError("A terminal outcome and its usage are immutable")
            return _job(row)
        if row["status"] == "reserved" and status == "completed":
            raise StateError("An unlaunched reservation cannot be completed")
        connection.execute("UPDATE jobs SET status=?, usage_json=?, settled_at=? WHERE assignment_id=?",
                           (status, encoded, _utc(), assignment_id))
        return _job(_find(connection, assignment_id))


def snapshot(path: Path) -> dict:
    with _transaction(path, write=False) as connection:
        policy = _read_policy(connection)
        jobs = [_job(row) for row in connection.execute("SELECT * FROM jobs ORDER BY created_at, assignment_id")]
        counts = {status: sum(job["status"] == status for job in jobs) for status in STATUSES}
        active = sum(counts[status] for status in ACTIVE)
        return {"version": 1, "notice": NOTICE, "policy": policy, "counts": counts, "jobs": jobs,
                "sessions_used": len(jobs), "sessions_remaining": policy["max_sessions"] - len(jobs),
                "active": active, "active_slots_remaining": policy["max_active"] - active}
