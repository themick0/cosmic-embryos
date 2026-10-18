from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import h5py
import numpy as np
import anndata as ad
import pandas as pd
from scipy import sparse as sp

from embryo_harness import core
from embryo_harness.files import HarnessError, check_records, read_json, record, write_json
from embryo_harness.validation import inspect, validate, validate_storage


def h5ad(path, *, sparse=None, coords=False):
    """Real AnnData files, with explicit object indexes for legacy-string tests."""
    x = np.ones((3, 2), dtype=np.float32)
    if sparse:
        x = {"csr": sp.csr_matrix, "csc": sp.csc_matrix}[sparse](x)
    a = ad.AnnData(X=x)
    a.obs.index = pd.Index(["a", "b", "c"], dtype=object)
    a.var.index = pd.Index(["GeneA", "GeneB"], dtype=object)
    if coords:
        a.obsm["spatial_3D"] = np.zeros((3, 3))
    a.write_h5ad(path)


def nullable_h5ad(path):
    # Encode both indexes with the format that triggered the review finding.
    a = ad.AnnData(X=np.ones((3, 2), dtype=np.float32))
    a.obs.index = pd.Index(pd.array(["a", "b", "c"], dtype="string"))
    a.var.index = pd.Index(pd.array(["GeneA", "GeneB"], dtype="string"))
    with ad.settings.override(allow_write_nullable_strings=True):
        a.write_h5ad(path)


def broken_annotation(path, axis):
    h5ad(path)
    with h5py.File(path, "r+") as f:
        group = f[axis]
        data = group.create_dataset("broken_column", data=np.ones(2 if axis == "obs" else 3))
        data.attrs["encoding-type"] = "array"
        data.attrs["encoding-version"] = "0.2.0"
        group.attrs["column-order"] = ["broken_column"]


FAKE_CODEX = r'''
import sys, json, time, shutil
from pathlib import Path
args = sys.argv[1:]
if '--version' in args:
    print('codex-cli test-fixture'); raise SystemExit(0)
if '--help' in args:
    print('--json --output-schema --ignore-user-config --ignore-rules'); raise SystemExit(0)
model = args[args.index('--model') + 1]
if model == 'timeout':
    time.sleep(30)
if model == 'crash':
    print('simulated crash', file=sys.stderr); raise SystemExit(2)
cwd = Path(args[args.index('--cd') + 1])
context = json.loads((cwd / 'context.json').read_text())
sys.stdin.read()
print(json.dumps({'type':'thread.started', 'thread_id':'fake-only'}), flush=True)
shutil.copyfile(context['inputs'][0]['path'], cwd / 'prediction.h5ad')
if model == 'external-hdf5':
    import h5py
    backing = cwd / 'backing.h5'
    with h5py.File(cwd / 'prediction.h5ad', 'r+') as f, h5py.File(backing, 'w') as out:
        f.copy('X', out)
        del f['X']
        f['X'] = h5py.ExternalLink(str(backing), '/X')
(cwd / 'method.md').write_text('Synthetic fixture, never a competition entry.')
(cwd / 'sources.json').write_text('[]')
(cwd / 'reproduce.py').write_text('# synthetic test only\n')
prediction = 'prediction.h5ad'
if model == 'traversal':
    prediction = '../lock.json'
if model == 'symlink':
    (cwd / 'prediction.h5ad').unlink()
    (cwd / 'prediction.h5ad').symlink_to(context['inputs'][0]['path'])
if model == 'mutate-input':
    with open(context['inputs'][0]['path'], 'ab') as f: f.write(b'changed')
final = {'status':'completed', 'summary':'fixture',
         'predictions':[{'board':context['boards'][0], 'path':prediction}],
         'method_summary':'method.md', 'sources':'sources.json',
         'reproduction_files':['reproduce.py']}
Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(final))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':4,'output_tokens':2}}), flush=True)
'''


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ai = self.root / "ai"
        self.panels = self.ai / "resources/panels"
        self.panels.mkdir(parents=True)
        self.data = self.root / "datasets/tiny.h5ad"
        self.data.parent.mkdir()
        h5ad(self.data)
        self.index = {"T1:val": {"task": "T1", "n_genes": 2, "min_cells": 2, "max_cells": 4,
                                 "needs_coords": False, "genes_file": "genes.txt"}}
        write_json(self.panels / "index.json", self.index)
        (self.panels / "genes.txt").write_text("GeneA\nGeneB\n")
        write_json(self.panels / "provenance.json", {"files": {
            name: record(self.panels / name) for name in ("index.json", "genes.txt")}})
        (self.ai / "prompt.md").write_text("Test fixture prompt")
        (self.ai / "prompts").mkdir()
        (self.ai / "prompts/competition.md").write_text("Fixture competition rules")
        (self.ai / "prompts/common.md").write_text("Fixture common instructions")
        executable = self.root / "fake-codex"
        executable.write_text(f"#!{sys.executable}\n" + FAKE_CODEX)
        executable.chmod(0o755)
        self.config = {"version": 1, "task": "T1", "boards": ["T1:val"],
                       "targets": {"T1:val": "fixture"}, "inputs": ["datasets/tiny.h5ad"],
                       "prompt_files": ["ai/prompts/competition.md", "ai/prompts/common.md",
                                        "ai/prompt.md"], "model": "test-model",
                       "reasoning_effort": "high", "sandbox": "workspace-write",
                       "network_access": False, "wall_time_seconds": 10, "seed": 42,
                       "codex_executable": str(executable)}
        self.config_path = self.ai / "config.json"
        write_json(self.config_path, self.config)

    def prepare(self, model=None):
        return core.prepare(self.root, self.config_path, model)

    def test_required_prompts_cannot_be_omitted_or_reordered(self):
        invalid = [["ai/prompt.md"],
                   ["ai/prompts/competition.md", "ai/prompt.md"],
                   ["ai/prompts/common.md", "ai/prompt.md"],
                   ["ai/prompts/common.md", "ai/prompts/competition.md", "ai/prompt.md"],
                   ["ai/prompt.md", "ai/prompts/competition.md", "ai/prompts/common.md"],
                   ["ai/prompts/competition.md", "ai/prompts/common.md"]]
        for prompts in invalid:
            with self.subTest(prompts=prompts):
                write_json(self.config_path, dict(self.config, prompt_files=prompts))
                with patch.object(core.codex, "probe") as probe:
                    with self.assertRaisesRegex(HarnessError, "prompt_files must start"):
                        self.prepare()
                    probe.assert_not_called()
                self.assertFalse((self.ai / "runs").exists())

    def test_required_prompts_are_frozen_and_included_in_agent_instructions(self):
        run = self.prepare()
        prompt = (run / "frozen/prompt.txt").read_text()
        self.assertLess(prompt.index("Fixture competition rules"),
                        prompt.index("Fixture common instructions"))
        self.assertLess(prompt.index("Fixture common instructions"),
                        prompt.index("Test fixture prompt"))
        self.assertEqual((run / "frozen/prompts/00-competition.md").read_text(),
                         "Fixture competition rules")
        self.assertEqual((run / "frozen/prompts/01-common.md").read_text(),
                         "Fixture common instructions")

    def test_complete_run_and_evidence_bundle(self):
        run = self.prepare()
        result = core.execute(run)
        self.assertEqual(result["trace"]["usage"]["output_tokens"], 2)
        self.assertIsNone(result["predictions"][0]["biological_score"])
        core.verify(run)
        export = core.bundle(run)
        manifest = read_json(export / "manifest.json")
        self.assertEqual(set(manifest["evidence"]), {"trajectory.tar.gz", "prompts.tar.gz", "harness.tar.gz"})
        self.assertFalse(manifest["uploaded"])
        with tarfile.open(export / "trajectory.tar.gz") as archive:
            self.assertIn("trajectory.jsonl", archive.getnames())
        self.assertEqual(record(export / "prediction-1.h5ad"), record(run / "work/prediction.h5ad"))
        with self.assertRaisesRegex(HarnessError, "already attempted"):
            core.execute(run)
        with self.assertRaisesRegex(HarnessError, "already exists"):
            core.bundle(run)

    def test_input_tampering_prevents_execution(self):
        run = self.prepare()
        with self.data.open("ab") as f:
            f.write(b"tamper")
        with self.assertRaisesRegex(HarnessError, "Integrity"):
            core.execute(run)
        self.assertFalse((run / "started.json").exists())

    def test_prompt_tampering_prevents_execution(self):
        run = self.prepare()
        (run / "frozen/prompt.txt").write_text("changed")
        with self.assertRaisesRegex(HarnessError, "Integrity"):
            core.execute(run)

    def test_source_change_requires_new_run(self):
        run = self.prepare()
        (self.ai / "prompt.md").write_text("new prompt")
        with self.assertRaisesRegex(HarnessError, "Integrity"):
            core.execute(run)

    def test_modified_predictions_cannot_be_exported(self):
        run = self.prepare()
        core.execute(run)
        with h5py.File(run / "work/prediction.h5ad", "r+") as f:
            f["X"][0, 0] = 9
        with self.assertRaisesRegex(HarnessError, "Integrity"):
            core.bundle(run)

    def test_completed_run_requires_receipt_and_checksum(self):
        for missing in (("receipt.json",), ("receipt.sha256",), ("receipt.json", "receipt.sha256")):
            with self.subTest(missing=missing):
                run = self.prepare()
                core.execute(run)
                # Reproduce the review case: changed output plus missing receipt.
                (run / "work/prediction.h5ad").write_bytes(b"changed prediction")
                for name in missing:
                    (run / name).unlink()
                with self.assertRaisesRegex(HarnessError, "receipt"):
                    core.verify(run)
                with self.assertRaisesRegex(HarnessError, "receipt"):
                    core.bundle(run)

    def test_prepared_run_can_be_verified_without_receipt(self):
        core.verify(self.prepare())

    def test_frozen_cli_does_not_modify_its_snapshot(self):
        real_ai = Path(__file__).resolve().parents[1]
        shutil.copyfile(real_ai / "harness.py", self.ai / "harness.py")
        shutil.copytree(real_ai / "embryo_harness", self.ai / "embryo_harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
        run = self.prepare()
        result = subprocess.run([sys.executable, str(run / "frozen/harness/harness.py"), "--help"],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list((run / "frozen").rglob("__pycache__")))
        core.verify(run)

    def test_frozen_tree_rejects_files_added_after_completion(self):
        run = self.prepare()
        core.execute(run)
        (run / "frozen/harness/post_run_change.py").write_text("# added after completion\n")
        with self.assertRaisesRegex(HarnessError, "Frozen file set changed"):
            core.verify(run)
        with self.assertRaisesRegex(HarnessError, "Frozen file set changed"):
            core.bundle(run)

    def test_evidence_roundtrip_verifies_all_frozen_and_output_files(self):
        run = self.prepare()
        core.execute(run)
        exported = core.bundle(run)
        restored = self.root / "restored"
        restored.mkdir()
        manifest = read_json(exported / "manifest.json")
        check_records(exported, manifest["evidence"])
        for name in manifest["evidence"]:
            with tarfile.open(exported / name) as archive:
                archive.extractall(restored, filter="data")
        # Predictions are separate upload files, with their original path in manifest.
        for prediction in manifest["predictions"]:
            dest = restored / prediction["run_path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(exported / prediction["file"], dest)
        # Prove the check uses only the export, not the original frozen files.
        shutil.rmtree(run)
        shutil.rmtree(self.data.parent)
        core.verify(restored, inputs=False)
        check_records(restored, read_json(restored / "lock.json")["frozen"])
        check_records(restored, read_json(restored / "receipt.json")["files"])

    def test_timeout_retains_logs_and_fails(self):
        self.config["wall_time_seconds"] = 1
        write_json(self.config_path, self.config)
        run = self.prepare("timeout")
        with self.assertRaisesRegex(HarnessError, "budget exhausted"):
            core.execute(run)
        self.assertEqual(read_json(run / "status.json")["status"], "failed")
        self.assertTrue((run / "trajectory.jsonl").exists())
        with self.assertRaises(HarnessError):
            core.bundle(run)

    def test_child_failure_does_not_complete(self):
        run = self.prepare("crash")
        with self.assertRaisesRegex(HarnessError, "code 2"):
            core.execute(run)
        self.assertIn("simulated crash", (run / "stderr.log").read_text())

    def test_artifact_cannot_escape_work_directory(self):
        run = self.prepare("traversal")
        with self.assertRaisesRegex(HarnessError, "relative path"):
            core.execute(run)

    def test_symlink_artifact_is_rejected(self):
        run = self.prepare("symlink")
        with self.assertRaisesRegex(HarnessError, "escapes|Symlink"):
            core.execute(run)

    def test_agent_modifying_data_fails_post_run_check(self):
        run = self.prepare("mutate-input")
        with self.assertRaisesRegex(HarnessError, "Integrity"):
            core.execute(run)

    def test_model_is_required(self):
        self.config["model"] = None
        write_json(self.config_path, self.config)
        with self.assertRaisesRegex(HarnessError, "exact model"):
            self.prepare()

    def test_valid_dense_and_sparse_formats(self):
        for sparse in (None, "csr", "csc"):
            with self.subTest(sparse=sparse):
                h5ad(self.data, sparse=sparse)
                self.assertTrue(validate(self.data, "T1:val", self.panels)["format_valid"])
                self.assertEqual(json.loads(json.dumps(inspect(self.data)))["shape"], [3, 2])

    def test_nullable_string_indexes_roundtrip_and_validate(self):
        nullable_h5ad(self.data)
        self.assertEqual(ad.read_h5ad(self.data).var_names.tolist(), ["GeneA", "GeneB"])
        with h5py.File(self.data, "r") as f:
            self.assertEqual(f["var/_index"].attrs["encoding-type"], "nullable-string-array")
            self.assertIsInstance(f["obs/_index"], h5py.Group)
        self.assertTrue(validate(self.data, "T1:val", self.panels)["format_valid"])

    def test_legacy_string_indexes_remain_supported(self):
        h5ad(self.data)
        with h5py.File(self.data, "r") as f:
            self.assertIsInstance(f["var/_index"], h5py.Dataset)
        self.assertTrue(validate(self.data, "T1:val", self.panels)["format_valid"])

    def test_malformed_obs_and_var_columns_rejected(self):
        for axis in ("obs", "var"):
            with self.subTest(axis=axis):
                broken_annotation(self.data, axis)
                with self.assertRaises(ValueError):
                    ad.read_h5ad(self.data)
                with self.assertRaisesRegex(HarnessError, "Malformed AnnData structure"):
                    validate(self.data, "T1:val", self.panels)

    def test_malformed_optional_obsm_rejected(self):
        h5ad(self.data)
        with h5py.File(self.data, "r+") as f:
            data = f["obsm"].create_dataset("bad_embedding", data=np.zeros((2, 2)))
            data.attrs["encoding-type"] = "array"
            data.attrs["encoding-version"] = "0.2.0"
        with self.assertRaisesRegex(HarnessError, "Malformed AnnData structure"):
            validate(self.data, "T1:val", self.panels)

    def test_source_snapshot_excludes_credentials_and_runtime(self):
        (self.ai / ".env").write_text("DO_NOT_PACKAGE=secret")
        (self.ai / "secrets.json").write_text('{"token": "secret"}')
        (self.ai / "runs").mkdir()
        (self.ai / "runs/previous.txt").write_text("previous trajectory")
        run = self.prepare()
        frozen = run / "frozen/harness"
        self.assertFalse((frozen / ".env").exists())
        self.assertFalse((frozen / "secrets.json").exists())
        self.assertFalse((frozen / "runs").exists())

    def test_wrong_gene_order_rejected(self):
        with h5py.File(self.data, "r+") as f:
            f["var/_index"][:] = ["GeneB", "GeneA"]
        with self.assertRaisesRegex(HarnessError, "Gene names/order"):
            validate(self.data, "T1:val", self.panels)

    def test_negative_nonfinite_overflow_rejected(self):
        for value in (-1., float("nan"), float("inf"), 1e100):
            with self.subTest(value=value):
                h5ad(self.data)
                with h5py.File(self.data, "r+") as f:
                    del f["X"]
                    f["X"] = np.full((3, 2), value, dtype=np.float64)
                with self.assertRaises(HarnessError):
                    validate(self.data, "T1:val", self.panels)

    def test_spatial_board_requires_coordinates(self):
        self.index["T1:val"]["needs_coords"] = True
        write_json(self.panels / "index.json", self.index)
        with self.assertRaises(HarnessError):
            validate(self.data, "T1:val", self.panels)
        h5ad(self.data, coords=True)
        self.assertTrue(validate(self.data, "T1:val", self.panels)["format_valid"])

    def test_invalid_sparse_indices_rejected(self):
        h5ad(self.data, sparse="csr")
        with h5py.File(self.data, "r+") as f:
            f["X/indices"][0] = 200
        with self.assertRaisesRegex(HarnessError, "bounds"):
            validate(self.data, "T1:val", self.panels)

    def test_external_links_rejected_before_reading_any_metadata(self):
        for target in ("X", "obs/_index", "obsm/spatial_3D", "uns/nested/dependency"):
            with self.subTest(target=target):
                h5ad(self.data, coords=True)
                with h5py.File(self.data, "r+") as f:
                    parent, _, name = target.rpartition("/")
                    group = f.require_group(parent) if parent else f
                    if name in group:
                        del group[name]
                    # Even a dangling external link must be rejected without resolving it.
                    group[name] = h5py.ExternalLink(str(self.root / "absent.h5"), "/data")
                with patch("embryo_harness.validation.read_metadata") as reader:
                    with self.assertRaisesRegex(HarnessError, "External HDF5 link"):
                        validate(self.data, "T1:val", self.panels)
                    reader.assert_not_called()

    def test_external_sparse_payload_rejected(self):
        h5ad(self.data, sparse="csr")
        with h5py.File(self.data, "r+") as f:
            del f["X/data"]
            f["X/data"] = h5py.ExternalLink(str(self.root / "absent.h5"), "/values")
        with self.assertRaisesRegex(HarnessError, "External HDF5 link"):
            validate(self.data, "T1:val", self.panels)

    def test_virtual_arrays_rejected_without_resolving_source(self):
        for target in ("X", "uns/virtual_array"):
            with self.subTest(target=target):
                h5ad(self.data)
                layout = h5py.VirtualLayout(shape=(3, 2), dtype=np.float32)
                layout[:] = h5py.VirtualSource(str(self.root / "absent.h5"), "/X", shape=(3, 2))
                with h5py.File(self.data, "r+") as f:
                    if target in f:
                        del f[target]
                    ds = f.create_virtual_dataset(target, layout, fillvalue=0)
                    ds.attrs["encoding-type"] = "array"
                    ds.attrs["encoding-version"] = "0.2.0"
                with patch("embryo_harness.validation.read_metadata") as reader:
                    with self.assertRaisesRegex(HarnessError, "Virtual HDF5 dataset"):
                        validate(self.data, "T1:val", self.panels)
                    reader.assert_not_called()

    def test_external_raw_storage_rejected(self):
        for target in ("X", "uns/raw_array"):
            with self.subTest(target=target):
                h5ad(self.data)
                with h5py.File(self.data, "r+") as f:
                    if target in f:
                        del f[target]
                    ds = f.create_dataset(target, shape=(3, 2), dtype=np.float32,
                                          external=[(str(self.root / "absent.raw"), 0, h5py.h5f.UNLIMITED)])
                    ds.attrs["encoding-type"] = "array"
                    ds.attrs["encoding-version"] = "0.2.0"
                with self.assertRaisesRegex(HarnessError, "External HDF5 raw storage"):
                    validate(self.data, "T1:val", self.panels)

    def test_internal_links_preserve_standalone_file_support(self):
        h5ad(self.data)
        with h5py.File(self.data, "r+") as f:
            f.move("X", "uns/expression")
            f["X"] = h5py.SoftLink("/uns/expression")
            f["uns/expression_alias"] = f["uns/expression"]
        self.assertTrue(validate(self.data, "T1:val", self.panels)["format_valid"])
        np.testing.assert_array_equal(ad.read_h5ad(self.data).X, np.ones((3, 2)))

    def test_storage_traversal_handles_hard_link_cycles(self):
        with h5py.File(self.data, "r+") as f:
            group = f["uns"].create_group("cycle")
            group["again"] = group
            group["dependency"] = h5py.ExternalLink(str(self.root / "absent.h5"), "/X")
        with self.assertRaisesRegex(HarnessError, "External HDF5 link"):
            validate_storage(self.data)

    def test_inputs_with_external_data_cannot_be_locked(self):
        with h5py.File(self.data, "r+") as f:
            del f["X"]
            f["X"] = h5py.ExternalLink(str(self.root / "absent.h5"), "/X")
        with self.assertRaisesRegex(HarnessError, "External HDF5 link"):
            self.prepare()

    def test_external_prediction_cannot_complete_or_export(self):
        run = self.prepare("external-hdf5")
        with self.assertRaisesRegex(HarnessError, "External HDF5 link"):
            core.execute(run)
        self.assertEqual(read_json(run / "status.json")["status"], "failed")
        self.assertFalse((run / "receipt.json").exists())
        with self.assertRaises(HarnessError):
            core.bundle(run)

    def test_unsupported_x_encoding_type_and_version_rejected(self):
        for sparse in (None, "csr", "csc"):
            for attribute, value in (("encoding-type", "unsupported-array"),
                                     ("encoding-version", "999.0.0"),
                                     ("encoding-version", None)):
                with self.subTest(sparse=sparse, attribute=attribute, value=value):
                    h5ad(self.data, sparse=sparse)
                    with h5py.File(self.data, "r+") as f:
                        if value is None:
                            del f["X"].attrs[attribute]
                        else:
                            f["X"].attrs[attribute] = value
                    with patch("embryo_harness.validation.read_metadata") as reader:
                        with self.assertRaisesRegex(HarnessError, "Unsupported X encoding type/version"):
                            validate(self.data, "T1:val", self.panels)
                        reader.assert_not_called()

    def test_dense_legacy_x_without_encoding_attributes_still_valid(self):
        with h5py.File(self.data, "r+") as f:
            del f["X"].attrs["encoding-type"]
            del f["X"].attrs["encoding-version"]
        self.assertTrue(validate(self.data, "T1:val", self.panels)["format_valid"])

    def test_x_encoding_must_match_hdf5_container(self):
        for sparse, encoding, version in ((None, "csr_matrix", "0.1.0"),
                                          ("csr", "array", "0.2.0")):
            with self.subTest(sparse=sparse):
                h5ad(self.data, sparse=sparse)
                with h5py.File(self.data, "r+") as f:
                    f["X"].attrs["encoding-type"] = encoding
                    f["X"].attrs["encoding-version"] = version
                with self.assertRaisesRegex(HarnessError, "Unsupported X encoding type/version"):
                    validate(self.data, "T1:val", self.panels)


if __name__ == "__main__":
    unittest.main()
