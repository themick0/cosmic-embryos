"""Bounded-memory validation of AnnData dense, CSR and CSC submission matrices."""
from __future__ import annotations

from pathlib import Path

from .files import HarnessError, read_json


def libraries():
    try:
        import anndata
        import h5py
        import numpy as np
    except ImportError as e:
        raise HarnessError("Install dependencies: uv sync --project ai") from e
    return h5py, np


def text_attr(value):
    return value.decode() if isinstance(value, bytes) else value


def matrix_shape(x):
    h5py, _ = libraries()
    return tuple(x.shape if isinstance(x, h5py.Dataset) else x.attrs.get("shape", ()))


def check_storage(f):
    """Inspect links and storage metadata before any expression/annotation read.

    Walk hard links explicitly: HDF5's object visitor skips external links.
    Object addresses prevent loops through hard-linked groups. Soft links stay
    inside this file; their hard-linked targets are inspected by the same walk.
    """
    h5py, _ = libraries()
    pending = [f]
    visited = set()
    while pending:
        node = pending.pop()
        address = h5py.h5o.get_info(node.id).addr
        if address in visited:
            continue
        visited.add(address)
        if isinstance(node, h5py.Group):
            for name in node:
                link = node.get(name, getlink=True)
                if isinstance(link, h5py.ExternalLink):
                    raise HarnessError(f"External HDF5 link is not allowed: {node.name}/{name}")
                if isinstance(link, h5py.HardLink):
                    pending.append(node[name])
        elif isinstance(node, h5py.Dataset):
            if node.is_virtual:
                raise HarnessError(f"Virtual HDF5 dataset is not allowed: {node.name}")
            if node.external:
                raise HarnessError(f"External HDF5 raw storage is not allowed: {node.name}")


def validate_storage(path: Path):
    """A hashed h5ad input must also contain all its data within that file."""
    h5py, _ = libraries()
    try:
        with h5py.File(path, "r") as f:
            check_storage(f)
    except (OSError, KeyError, TypeError, ValueError) as e:
        raise HarnessError(f"Malformed HDF5 storage in {path}: {e}") from e


def check_x_encoding(x):
    """Supported AnnData numeric matrix encodings, without materialising X.

    AnnData's backed reader does not dispatch X through its encoding registry.
    Keep this explicit contract alongside the pinned AnnData dependency. Dense
    datasets without either attribute use AnnData's supported legacy reader.
    """
    h5py, _ = libraries()
    encoding = (text_attr(x.attrs.get("encoding-type", "")),
                text_attr(x.attrs.get("encoding-version", "")))
    supported = ({("array", "0.2.0"), ("", "")} if isinstance(x, h5py.Dataset)
                 else {("csr_matrix", "0.1.0"), ("csc_matrix", "0.1.0")})
    if encoding not in supported:
        raise HarnessError(f"Unsupported X encoding type/version: {encoding}")


def read_metadata(path: Path):
    """Let AnnData decode its own encodings and validate ancillary structures.

    Backed mode leaves X on disk; expression values are checked in blocks below.
    AnnData still reads annotations, layers and other ancillary arrays into memory.
    """
    import anndata

    adata = None
    try:
        adata = anndata.read_h5ad(path, backed="r")
        return adata.n_obs, adata.var_names.tolist()
    except Exception as e:
        raise HarnessError(f"Malformed AnnData structure in {path}: {e}") from e
    finally:
        if adata is not None:
            adata.file.close()


def inspect(path: Path) -> dict:
    h5py, _ = libraries()
    with h5py.File(path, "r") as f:
        check_storage(f)
        return {"file": str(path), "shape": [int(n) for n in matrix_shape(f["X"])],
                "obs_columns": list(f["obs"]),
                "obsm": {k: list(v.shape) for k, v in f.get("obsm", {}).items()
                         if isinstance(v, h5py.Dataset)}}


def validate(path: Path, board: str, panels: Path) -> dict:
    h5py, np = libraries()
    index = read_json(panels / "index.json")
    if board not in index:
        raise HarnessError(f"Unknown board {board}; refresh panels before preparing a new run")
    spec = index[board]
    genes_file = spec["genes_file"]
    if Path(genes_file).name != genes_file:
        raise HarnessError("Invalid panel filename")
    genes = (panels / genes_file).read_text().splitlines()
    errors = []
    if len(genes) != spec["n_genes"] or len(set(genes)) != len(genes):
        raise HarnessError("Invalid gene panel")
    if path.stat().st_size > 1_200_000_000:
        errors.append("Prediction exceeds 1200 MB")

    def finite_nonnegative(ds, name, nonnegative=True):
        if ds.dtype.kind not in "iuf":
            errors.append(f"{name} must be real numeric data")
            return
        step = max(1, 1_000_000 // max(1, int(np.prod(ds.shape[1:]))))
        for start in range(0, ds.shape[0], step):
            # Check representability in the scorer's float32, as well as original values.
            with np.errstate(over="ignore", invalid="ignore"):
                values = ds[start:start + step].astype(np.float32)
            if not np.isfinite(values).all():
                errors.append(f"{name} contains non-finite values (including float32 overflow)")
                break
            if nonnegative and (values < 0).any():
                errors.append(f"{name} contains negative values")
                break

    try:
        with h5py.File(path, "r") as f:
            check_storage(f)
            if text_attr(f.attrs.get("encoding-type")) != "anndata":
                raise HarnessError("Root encoding-type must be anndata")
            if text_attr(f.attrs.get("encoding-version")) != "0.1.0":
                raise HarnessError("Root encoding-version must be 0.1.0")
            x = f["X"]
            check_x_encoding(x)
            shape = matrix_shape(x)
            if len(shape) != 2:
                raise HarnessError("X must be a 2D matrix")
            n, g = shape
            if not spec["min_cells"] <= n <= spec["max_cells"]:
                errors.append(f"Cell count {n} outside [{spec['min_cells']}, {spec['max_cells']}]")
            n_obs, actual_genes = read_metadata(path)
            if g != len(genes) or actual_genes != genes:
                errors.append("Gene names/order do not exactly match the board panel")
            if n_obs != n:
                errors.append("obs index length does not match X")
            if isinstance(x, h5py.Dataset):
                finite_nonnegative(x, "X")
            else:
                encoding = text_attr(x.attrs.get("encoding-type"))
                if encoding not in ("csr_matrix", "csc_matrix"):
                    raise HarnessError(f"Unsupported X encoding: {encoding}")
                data, indices, indptr = x["data"], x["indices"], x["indptr"]
                rows, columns = (n, g) if encoding == "csr_matrix" else (g, n)
                if data.ndim != 1 or indices.shape != data.shape or indptr.shape != (rows + 1,):
                    raise HarnessError("Malformed sparse matrix dimensions")
                if indices.dtype.kind not in "iu" or indptr.dtype.kind not in "iu":
                    raise HarnessError("Sparse indices must be integers")
                ptr = indptr[:]
                if ptr[0] != 0 or ptr[-1] != len(data) or (ptr[1:] < ptr[:-1]).any():
                    errors.append("Invalid sparse indptr")
                for start in range(0, len(indices), 1_000_000):
                    ix = indices[start:start + 1_000_000]
                    if (ix < 0).any() or (ix >= columns).any():
                        errors.append("Sparse index out of bounds")
                        break
                finite_nonnegative(data, "X.data")
                # Duplicate sparse indices are summed by the scorer's densifier.
                # Check that sum in the stored dtype, then in float32. Keep only
                # one dense row/column and bounded sparse chunks in memory.
                if not errors:
                    for row in range(rows):
                        start, end = int(ptr[row]), int(ptr[row + 1])
                        accumulated = np.zeros(columns, dtype=data.dtype)
                        with np.errstate(over="ignore", invalid="ignore"):
                            for offset in range(start, end, 1_000_000):
                                stop = min(end, offset + 1_000_000)
                                np.add.at(accumulated, indices[offset:stop], data[offset:stop])
                            totals = accumulated.astype(np.float32)
                        if not np.isfinite(totals).all() or (totals < 0).any():
                            errors.append("Sparse duplicate sums are non-finite or negative in float32")
                            break
            if spec["needs_coords"]:
                coords = f["obsm"]["spatial_3D"]
                if coords.ndim != 2 or coords.shape[0] != n or coords.shape[1] < 3:
                    errors.append("spatial_3D must have shape (cells, >=3)")
                else:
                    finite_nonnegative(coords, "spatial_3D", nonnegative=False)
    except (KeyError, OSError, TypeError, ValueError) as e:
        raise HarnessError(f"Malformed h5ad {path}: {e}") from e
    if errors:
        raise HarnessError("; ".join(errors))
    return {"board": board, "cells": int(n), "genes": int(g), "format_valid": True,
            "biological_score": None}
