"""Explicit refresh of public board contracts; never called during a locked run."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .files import HarnessError, record, utc, write_json

BASE_URL = "https://virtualembryo.ai/challenge/panels/"


def sync(ai: Path) -> dict:
    if not shutil.which("curl"):
        raise HarnessError("sync-panels requires curl")
    resources = ai / "resources"
    resources.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".panels-", dir=resources) as td:
        temp = Path(td)

        def download(name):
            if Path(name).name != name or not name:
                raise HarnessError("Invalid filename in remote panel index")
            response = subprocess.run(["curl", "--fail-with-body", "--silent", "--show-error",
                                       "--location", "--max-time", "60", "--proto", "=https",
                                       "--proto-redir", "=https", "--user-agent", "Mozilla/5.0",
                                       BASE_URL + name], capture_output=True, timeout=65)
            if response.returncode:
                raise HarnessError(f"Panel download failed: {name}: " +
                                   response.stderr.decode(errors="replace") +
                                   response.stdout[:1000].decode(errors="replace"))
            (temp / name).write_bytes(response.stdout)

        download("index.json")
        index = json.loads((temp / "index.json").read_text())
        if not isinstance(index, dict) or not index:
            raise HarnessError("Empty or invalid panel index")
        for board, spec in index.items():
            name = spec["genes_file"]
            if not (temp / name).exists():
                download(name)
            genes = (temp / name).read_text().splitlines()
            if len(genes) != spec["n_genes"] or len(set(genes)) != len(genes):
                raise HarnessError(f"Invalid gene list for {board}")
            # The published checksum is a truncated SHA-256 over newline-joined names.
            expected = spec.get("genes_sha256", "")
            candidates = [hashlib.sha256(("\n".join(genes) + ending).encode()).hexdigest()
                          for ending in ("", "\n")]
            if expected and not any(h.startswith(expected) for h in candidates):
                raise HarnessError(f"Published gene checksum mismatch for {board}")
        provenance = {"fetched_at": utc(), "source": BASE_URL + "index.json",
                      "files": {p.name: record(p) for p in temp.iterdir() if p.is_file()}}
        write_json(temp / "provenance.json", provenance)
        destination = resources / "panels"
        destination.mkdir(exist_ok=True)
        # Each file is atomically replaced; the manifest goes last. Run preparation
        # verifies the entire manifest, so it rejects an overlapping refresh.
        for p in sorted(temp.iterdir(), key=lambda p: p.name == "provenance.json"):
            p.replace(destination / p.name)
    return {"boards": list(index), "directory": str(destination)}
