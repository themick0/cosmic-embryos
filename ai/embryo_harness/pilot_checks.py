"""Check the pinned local scorer using synthetic inputs, without model calls."""
from __future__ import annotations

import importlib.metadata
import json
import math
import platform
import time
from pathlib import Path

from .files import HarnessError, record, utc, write_json


SCORER_VERSION = "0.1.1"
SCORER_COMMIT = "cc3859ab2a2305efc4544815d3f44f660efa77d4"
SCORER_URL = "https://github.com/aristoteleo/veckit.git"
RANKING_METRICS = (
    "de_score", "de_direction", "mmd_u", "variogram", "d2_shape",
    "occupancy_dice", "scale_log_ratio", "neighborhood_mmd",
)


def _provenance() -> dict:
    """Reject a different scorer before importing it or creating synthetic data."""
    try:
        distribution = importlib.metadata.distribution("veckit")
    except importlib.metadata.PackageNotFoundError as exc:
        raise HarnessError("The pinned veckit package is not installed") from exc
    if distribution.version != SCORER_VERSION:
        raise HarnessError("The installed veckit version does not match the pilot")
    try:
        direct = json.loads(distribution.read_text("direct_url.json") or "null")
    except (TypeError, json.JSONDecodeError) as exc:
        raise HarnessError("Invalid veckit installation provenance") from exc
    if not isinstance(direct, dict):
        raise HarnessError("The veckit installation has no Git commit provenance")
    vcs = direct.get("vcs_info")
    if (not isinstance(vcs, dict) or vcs.get("vcs") != "git"
            or vcs.get("commit_id") != SCORER_COMMIT
            or direct.get("url") != SCORER_URL):
        raise HarnessError("The installed veckit source does not match the pinned Git commit")
    return {"package": "veckit", "version": SCORER_VERSION, "url": SCORER_URL,
            "commit": SCORER_COMMIT, "licence": "MIT"}


def _score_synthetic(directory: Path) -> dict:
    """Exercise the public file-based scorer with a non-degenerate tiny fixture."""
    import anndata as ad
    import numpy as np
    from veckit import score

    rng = np.random.default_rng(42)
    n_cells, n_genes = 96, 64
    cell_types = np.where(np.arange(n_cells) % 2, "synthetic_a", "synthetic_b")
    abundance = rng.uniform(4.0, 20.0, size=n_genes)
    effect = np.zeros(n_genes)
    shuffled = rng.permutation(n_genes)
    effect[shuffled[:12]] = 1.4
    effect[shuffled[12:24]] = -1.4
    class_effect = np.ones((n_cells, n_genes))
    class_effect[::2, :8] = 3.0

    reference_counts = rng.gamma(8.0, 1 / 8.0, (n_cells, n_genes)) * abundance * class_effect
    target_counts = rng.gamma(8.0, 1 / 8.0, (n_cells, n_genes)) * abundance * class_effect
    target_counts *= np.exp(effect)
    prediction_counts = target_counts * np.exp(rng.normal(0.0, 0.05, target_counts.shape))
    reference_coords = rng.normal(size=(n_cells, 3)) * [1.0, 1.7, 2.5]
    target_coords = rng.normal(size=(n_cells, 3)) * [1.2, 2.0, 2.8]
    prediction_coords = target_coords + rng.normal(0.0, 0.02, target_coords.shape)

    paths = {}
    for name, counts, coords in (
        ("reference", reference_counts, reference_coords),
        ("target", target_counts, target_coords),
        ("prediction", prediction_counts, prediction_coords),
    ):
        expression = np.log1p(10000 * counts / counts.sum(axis=1, keepdims=True)).astype("float32")
        data = ad.AnnData(expression)
        data.var_names = [f"synthetic_gene_{index:03d}" for index in range(n_genes)]
        data.obs_names = [f"{name}_cell_{index:03d}" for index in range(n_cells)]
        data.obs["celltype"] = cell_types
        data.obsm["spatial_3D"] = coords.astype("float32")
        paths[name] = directory / f"{name}.h5ad"
        data.write_h5ad(paths[name])

    result = score(task="T2", setting="embryo", input=paths["prediction"],
                   target=paths["target"], reference=paths["reference"], seed=42)
    return {"result": result, "synthetic_inputs": {
        name: {"file": path.name, **record(path)} for name, path in paths.items()
    }, "cells_per_input": n_cells, "genes": n_genes, "seed": 42}


def _validate_result(result: dict) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("metrics"), dict):
        raise HarnessError("The scorer did not return its expected metric object")
    metadata = result.get("meta")
    if (not isinstance(metadata, dict) or metadata.get("task") != "T2"
            or metadata.get("setting") != "embryo"
            or metadata.get("reference_defaulted_to_input") is not False):
        raise HarnessError("The scorer did not confirm the explicit T2 embryo reference")
    invalid = []
    for name in RANKING_METRICS:
        value = result["metrics"].get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            invalid.append(name)
    if invalid:
        raise HarnessError("Synthetic scorer check has missing or non-finite ranking metrics: "
                           + ", ".join(invalid))


def check_scorer(output_dir: Path) -> dict:
    """Retain readiness evidence; the caller must enforce a process timeout.

    This checks installation and execution, not scientific accuracy or a model's
    authentication. Each output directory accepts only one scorer check.
    """
    directory = Path(output_dir) / "scorer-check"
    directory.mkdir(parents=True, exist_ok=False)
    report_path = directory / "report.json"
    report = {"version": 1, "kind": "synthetic_scorer_check", "created_at": utc(),
              "ok": False, "synthetic_only": True, "model_calls": 0,
              "python_version": platform.python_version(),
              "ranking_metrics": list(RANKING_METRICS), "report_path": str(report_path)}
    started = time.monotonic()
    try:
        report["scorer"] = _provenance()
        report["packages"] = dict(sorted(
            (distribution.metadata["Name"], distribution.version)
            for distribution in importlib.metadata.distributions()
            if distribution.metadata["Name"]
        ))
        report.update(_score_synthetic(directory))
        _validate_result(report["result"])
        report["ok"] = True
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:500]
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(report_path, report)
    return report
