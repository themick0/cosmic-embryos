"""Competition API operations, outside locked research runs.

Official uploads use durable request identities. Evidence POSTs are never retried
blindly: their remote list must be reconciled after an uncertain response.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx

from . import core
from .files import HarnessError, check_records, digest, inside, read_json, record, utc, write_json

API_BASE = "https://api.virtualembryo.ai"
EVIDENCE_KINDS = ("trajectory", "prompts", "harness")
TERMINAL = {"scored", "rejected", "cancelled", "eligibility_review"}
TEAM_NAME = "Cosmic Embryos"


class ApiError(HarnessError):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def api_key(root):
    """Read only the competition key; never source shell code from a file."""
    if os.environ.get("VE_CHALLENGE_API_KEY"):
        return os.environ["VE_CHALLENGE_API_KEY"].strip()
    if root is None:
        return ""
    path = root / "ai/.env.local"
    if not path.exists():
        return ""
    value = ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, raw = line.partition("=")
        if name.strip() != "VE_CHALLENGE_API_KEY" or not sep or value:
            raise HarnessError("ai/.env.local must contain one VE_CHALLENGE_API_KEY assignment")
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or any(c.isspace() for c in value):
            raise HarnessError("Invalid VE_CHALLENGE_API_KEY in ai/.env.local")
    return value


class API:
    def __init__(self, root=None, *, transport=None, sleeper=time.sleep):
        self.key = api_key(root)
        self.sleeper = sleeper
        self.client = httpx.Client(base_url=API_BASE, follow_redirects=False,
                                   headers={"User-Agent": "Mozilla/5.0 CosmicEmbryosHarness/1"},
                                   timeout=httpx.Timeout(120, connect=20), transport=transport)

    def close(self):
        self.client.close()

    def request(self, method, route, *, auth=True, fields=None, file=None,
                request_id=None, retry=True, before_attempt=None):
        if not route.startswith("/challenge/"):
            raise HarnessError("Unexpected API route")
        headers = {}
        if auth:
            if not self.key:
                raise HarnessError("Set VE_CHALLENGE_API_KEY in the environment or ai/.env.local")
            headers["Authorization"] = "Bearer " + self.key
        if request_id:
            headers["Idempotency-Key"] = request_id
        # Only safe reads and idempotent prediction uploads retry automatically.
        attempts = 4 if retry and (method == "GET" or request_id) else 1
        for attempt in range(attempts):
            if before_attempt is not None:
                before_attempt()
            stream = None
            try:
                files = None
                if file is not None:
                    stream = file.open("rb")
                    files = {"file": (file.name, stream, "application/octet-stream")}
                response = self.client.request(method, route, headers=headers, data=fields, files=files)
            except httpx.TransportError as exc:
                if attempt + 1 == attempts:
                    raise ApiError(f"API transport failure ({type(exc).__name__}); retry the same saved request") from None
            else:
                if response.status_code < 500 or attempt + 1 == attempts:
                    # Never persist a secret even if an upstream error echoes it.
                    body = response.text.replace(self.key, "[REDACTED]") if self.key else response.text
                    if not 200 <= response.status_code < 300:
                        raise ApiError(f"API HTTP {response.status_code}: {body[:1000]}",
                                       status_code=response.status_code)
                    try:
                        result = json.loads(body)
                    except ValueError:
                        raise ApiError("API returned a non-JSON response; reconcile before retrying an upload") from None
                    if not isinstance(result, dict):
                        raise ApiError("API response must be an object")
                    return result
            finally:
                if stream is not None:
                    stream.close()
            self.sleeper(2 ** (attempt + 1))
        raise ApiError("API request failed")


def readiness(api: API, *, public_only=False):
    result = {"checked_at": utc(), "phase": api.request("GET", "/challenge/phase", auth=False),
              "boards": api.request("GET", "/challenge/boards", auth=False),
              "documents": api.request("GET", "/challenge/documents", auth=False)}
    if not public_only:
        result["account"] = api.request("GET", "/challenge/me")
        result["quota"] = api.request("GET", "/challenge/integration/quota")
    return result


def load_bundle(root: Path, directory: Path, board: str):
    directory = directory.resolve()
    if directory.parent != (root / "ai/exports").resolve():
        raise HarnessError("Use a bundle under ai/exports")
    if digest(directory / "manifest.json") != (directory / "manifest.sha256").read_text().strip():
        raise HarnessError("Bundle manifest has changed")
    manifest = read_json(directory / "manifest.json")
    if manifest["run_id"] != directory.name:
        raise HarnessError("Bundle/run identity mismatch")
    run = inside(root / "ai/runs", manifest["run_id"])
    lock = core.verify(run, inputs=False)
    if read_json(run / "status.json")["status"] != "completed":
        raise HarnessError("Submission requires a completed run")
    if manifest["lock_sha256"] != digest(run / "lock.json"):
        raise HarnessError("Bundle belongs to a different run lock")
    if manifest["agent_model"] != lock["config"]["model"]:
        raise HarnessError("Agent model does not match the run")
    check_records(directory, manifest["evidence"])
    if set(manifest["evidence"]) != {kind + ".tar.gz" for kind in EVIDENCE_KINDS}:
        raise HarnessError("All three evidence archives are required")
    selected = [p for p in manifest["predictions"] if p["board"] == board]
    if len(selected) != 1:
        raise HarnessError(f"Bundle does not select exactly one prediction for {board}")
    prediction = selected[0]
    final = read_json(run / "final.json")
    if {"board": board, "path": prediction["run_path"].removeprefix("work/")} not in final["predictions"]:
        raise HarnessError("Prediction was not selected by the agent")
    receipt = read_json(run / "receipt.json")
    expected = receipt["files"][prediction["run_path"]]
    path = inside(directory, prediction["file"])
    if record(path) != expected or {k: prediction[k] for k in ("bytes", "sha256")} != expected:
        raise HarnessError("Exported prediction differs from the agent's sealed output")
    spec = read_json(run / "frozen/panels/index.json")[board]
    fields = {"task": spec["task"], "agent_framework": manifest["agent_framework"],
              "agent_model": lock["config"]["model"]}
    if spec["task"] == "T2":
        fields.update(setting=spec["setting"], mode=spec["split"].rsplit("_", 1)[-1])
    return manifest, prediction, path, fields


def check_target(api, board):
    phase = api.request("GET", "/challenge/phase", auth=False)
    boards = api.request("GET", "/challenge/boards", auth=False)
    if not phase.get("accepts_submissions"):
        raise HarnessError("Submissions are closed")
    if board not in {b["key"] for b in boards["boards"]}:
        raise HarnessError(f"Board {board} is no longer active; prepare a run for the current phase")
    return phase


def state_path(root, run_id, board):
    name = board.replace(":", "__")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise HarnessError("Invalid board identifier")
    directory = inside(root / "ai/submissions", run_id)
    return directory / (name + ".json")


@contextmanager
def state_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HarnessError("Another process is handling this submission") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def format_check(root, directory, board, api):
    manifest, prediction, path, fields = load_bundle(root, directory, board)
    check_target(api, board)
    result = api.request("POST", "/challenge/submissions", fields={**fields, "format_only": "true"},
                         file=path, retry=False)
    dest = state_path(root, manifest["run_id"], board).with_suffix(".format.json")
    write_json(dest, {"checked_at": utc(), "sha256": prediction["sha256"], "response": result})
    if result.get("ok") is not True or result.get("format_only") is not True:
        raise HarnessError(f"Server format check failed; see {dest}")
    return result


def check_identity(result, prediction, board):
    if result.get("bundle_key") != board or result.get("sha256") != prediction["sha256"]:
        raise HarnessError("Server board/hash differs from the selected prediction; stop and inspect the saved response")


def check_account(api, expected=None):
    account = api.request("GET", "/challenge/me")
    if account.get("registered") is not True or account.get("track") != "agent":
        raise HarnessError("Submission requires a registered Agent Team account")
    if account.get("team_name") != TEAM_NAME or not account.get("team_id"):
        raise HarnessError(f"API key must belong to {TEAM_NAME} with a confirmed team ID")
    binding = {"team_id": account["team_id"], "track": account["track"]}
    if expected is not None and binding != expected:
        raise HarnessError("API key team/track differs from the saved submission account; preserve state")
    if account.get("stale_consents"):
        raise HarnessError("Accept the current Rules/Terms in the portal before submitting")
    return binding


def read_prediction(api, state, path, board):
    """Read and persist job detail and history for upload checks and polling."""
    submission_id = state["submission_id"]
    detail = api.request("GET", "/challenge/submissions/" + quote(submission_id, safe=""))
    state["detail"] = detail
    state["last_checked_at"] = utc()
    write_json(path, state)
    check_identity(detail, state["identity"], board)
    history = api.request("GET", "/challenge/submissions")
    entry = next((row for row in history.get("submissions", [])
                  if str(row.get("id")) == submission_id), None)
    # History contains only the latest 200 entries. Absence is not proof of deletion.
    state["history_entry"] = entry
    # Retain confirmed reclamation even after this entry leaves recent history.
    state["reclaimed_at"] = (detail.get("reclaimed_at")
                             or (entry or {}).get("reclaimed_at")
                             or state.get("reclaimed_at"))
    if state["reclaimed_at"]:
        state["evidence_complete"] = False
    write_json(path, state)
    return dict(detail, reclaimed_at=state["reclaimed_at"])


def check_prediction(api, state, path, *, require_ready=False):
    detail = read_prediction(api, state, path, state["identity"]["board"])
    if detail.get("reclaimed_at"):
        raise HarnessError("Prediction file was reclaimed and cannot proceed; a new submission "
                           "requires a separate authorised attempt. Preserve this submission state")
    if detail.get("status") in {"rejected", "cancelled", "eligibility_review"}:
        raise HarnessError(f"Submission cannot proceed: {detail.get('status')}; see {path}")
    if require_ready and detail.get("status") not in {"queued", "scoring", "scored"}:
        raise HarnessError(f"Evidence was attached but scoring readiness is unconfirmed "
                           f"({detail.get('status')}); inspect {path} and resume the same command")
    return detail


def evidence_route(submission_id):
    return "/challenge/teams/evidence/" + quote(str(submission_id), safe="")


def matching_evidence(remote, kind, sha256):
    """Prefer a server digest; use the content-addressed note only if absent."""
    note = "Cosmic Embryos SHA256=" + sha256
    return any(f.get("kind") == kind and
               (f["sha256"] == sha256 if f.get("sha256") is not None
                else f.get("note") == note)
               for f in remote.get("files", []))


def attach_evidence(api, directory, manifest, state, path):
    route = evidence_route(state["submission_id"])
    state["evidence_complete"] = False
    write_json(path, state)
    for saved in state.get("evidence", {}).values():
        if saved.get("response", {}).get("note"):
            raise HarnessError(f"Saved evidence response has a server note; inspect {path} before continuing")
    for kind in EVIDENCE_KINDS:
        remote = api.request("GET", route)
        state["evidence_requirements"] = remote
        write_json(path, state)
        name = kind + ".tar.gz"
        info = manifest["evidence"][name]
        note = "Cosmic Embryos SHA256=" + info["sha256"]
        if matching_evidence(remote, kind, info["sha256"]):
            state.setdefault("evidence", {})[kind] = {"status": "attached", "sha256": info["sha256"]}
            write_json(path, state)
            continue
        previous = state.get("evidence", {}).get(kind, {})
        if previous.get("status") == "attached":
            raise HarnessError(f"Previously attached {kind} identity cannot be confirmed remotely; "
                               "inspect submission evidence")
        if previous.get("status") == "sending":
            raise HarnessError(f"Uncertain {kind} upload; remote metadata cannot confirm it. Inspect the evidence list before retrying")
        allowed = remote.get("max_bytes")
        total = remote.get("max_total_bytes")
        if not isinstance(allowed, int) or not isinstance(total, int):
            raise HarnessError("Evidence endpoint did not provide upload limits")
        if info["bytes"] > allowed or sum(v["bytes"] for v in manifest["evidence"].values()) > total:
            raise HarnessError("Bundle exceeds current evidence limits")
        state.setdefault("evidence", {})[kind] = {"status": "sending", "sha256": info["sha256"]}
        write_json(path, state)
        try:
            response = api.request("POST", route, fields={"kind": kind, "note": note},
                                   file=directory / name, retry=False)
        except ApiError as exc:
            if exc.status_code is not None and 400 <= exc.status_code < 500:
                state["evidence"][kind]["status"] = "rejected"
                write_json(path, state)
            raise
        state["evidence"][kind] = {"status": "attached", "sha256": info["sha256"], "response": response}
        write_json(path, state)
        # The documented reclamation signal is a note, not an HTTP error. Do not
        # infer failure from queued=false alone: a job may already be queued.
        if response.get("note"):
            raise HarnessError(f"Evidence server returned a note: {response['note']}; inspect {path} "
                               "before continuing. The prediction may no longer be available")
    remote = api.request("GET", route)
    state["evidence_requirements"] = remote
    kinds = set(remote.get("kinds", []))
    minimum = remote.get("min_kinds")
    required = remote.get("required_kind")
    complete = (isinstance(minimum, int) and len(kinds) >= minimum
                and (not required or required in kinds)
                and set(EVIDENCE_KINDS).issubset(kinds)
                and all(matching_evidence(remote, kind,
                                         manifest["evidence"][kind + ".tar.gz"]["sha256"])
                        for kind in EVIDENCE_KINDS))
    write_json(path, state)
    if not complete:
        raise HarnessError("Current evidence requirements or file identities are not satisfied; "
                           "inspect the saved server response")


def submit(root, directory, board, api, *, method, architecture=None):
    directory = directory.resolve()
    manifest, prediction, file, fields = load_bundle(root, directory, board)
    if not method.strip():
        raise HarnessError("A nonempty method name is required")
    fields["model"] = method
    if architecture:
        fields["architecture"] = architecture
    path = state_path(root, manifest["run_id"], board)
    identity = {"board": board, "sha256": prediction["sha256"], "filename": file.name,
                "fields": fields, "manifest_sha256": digest(directory / "manifest.json")}
    with state_lock(path):
        if path.exists():
            state = read_json(path)
            if state["identity"] != identity:
                raise HarnessError("Retry must keep the original file, filename, bundle, and form fields")
            if not state.get("account_binding"):
                raise HarnessError("Saved submission has no team binding; inspect its original account "
                                   "before recovery. Do not delete state or replay with another key")
            check_account(api, state["account_binding"])
        else:
            phase = check_target(api, board)
            binding = check_account(api)
            quota = api.request("GET", "/challenge/integration/quota")
            # Save the current quota for review. The server is authoritative for
            # recovery credits, phase-wide budgets, and concurrent team uploads.
            state = {"created_at": utc(), "identity": identity, "request_id": uuid.uuid4().hex,
                     "account_binding": binding, "phase_before": phase,
                     "quota_before": quota, "evidence": {}}
            write_json(path, state)
        state["evidence_complete"] = False
        write_json(path, state)
        if not state.get("submission_id"):
            # An uncertain request may never have reached the server. Check the
            # original phase before every POST, including automatic retries.
            def check_upload_target():
                check_account(api, state["account_binding"])
                expected_phase = state.get("phase_before", {}).get("phase")
                if not expected_phase:
                    raise HarnessError("Saved upload phase is unknown; inspect submission history "
                                       "and recover the original submission ID before retrying")
                try:
                    current = check_target(api, board)
                    if current.get("phase") != expected_phase:
                        raise HarnessError("Submission phase has changed")
                except HarnessError as exc:
                    raise HarnessError(f"{exc}. Upload stopped; preserve the request ID and "
                                       "inspect submission history before recovery") from exc

            if state.get("prediction_status") != "sending":
                format_check(root, directory, board, api)
                state["prediction_status"] = "sending"
                write_json(path, state)
            response = api.request("POST", "/challenge/submissions", fields=fields,
                                   file=file, request_id=state["request_id"],
                                   before_attempt=check_upload_target)
            state["upload_response"] = response
            if response.get("id") is not None:
                state["submission_id"] = str(response["id"])
            write_json(path, state)
            check_identity(response, prediction, board)
            if response.get("status") == "rejected" or response.get("problems"):
                raise HarnessError(f"Prediction rejected; see {path}")
            if not state.get("submission_id"):
                raise HarnessError("Upload did not return a submission ID")
        check_prediction(api, state, path)
        attach_evidence(api, directory, manifest, state, path)
        detail = check_prediction(api, state, path, require_ready=True)
        state["evidence_complete"] = True
        write_json(path, state)
        return {"state_file": str(path), "submission_id": state["submission_id"],
                "evidence_complete": state["evidence_complete"], "status": detail["status"]}


def status(root, run_id, board, api, *, wait_seconds=0):
    path = state_path(root, run_id, board)
    with state_lock(path):
        state = read_json(path)
        if not state.get("submission_id"):
            raise HarnessError("No confirmed submission ID; resume the original submit command")
        deadline = time.monotonic() + max(0, wait_seconds)
        while True:
            result = read_prediction(api, state, path, board)
            if result.get("reclaimed_at"):
                return dict(result, polling_stopped="prediction_reclaimed")
            if result.get("status") in TERMINAL or time.monotonic() >= deadline:
                return result
            api.sleeper(min(20, max(0, deadline - time.monotonic())))
