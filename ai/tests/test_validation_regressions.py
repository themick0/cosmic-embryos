from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anndata as ad
import h5py
import numpy as np
import test_harness
from embryo_harness.files import HarnessError
from embryo_harness.validation import validate


class ValidationRegressions(unittest.TestCase):
    def setUp(self):
        self.fixture = test_harness.HarnessTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_locked_run_disables_repository_codegraph(self):
        from embryo_harness.files import read_json
        run = self.fixture.prepare()
        command = read_json(run / 'frozen/command.json')
        overrides = [command[i + 1] for i, value in enumerate(command[:-1]) if value == '-c']
        self.assertIn('mcp_servers.codegraph.enabled=false', overrides)

    def test_root_encoding_version_is_checked_before_anndata(self):
        with h5py.File(self.fixture.data, 'r+') as f:
            f.attrs['encoding-version'] = '99.0.0'
        with patch('embryo_harness.validation.read_metadata') as reader:
            with self.assertRaisesRegex(HarnessError, 'Root encoding-version'):
                validate(self.fixture.data, 'T1:val', self.fixture.panels)
            reader.assert_not_called()

    def test_duplicate_sparse_overflow_is_rejected(self):
        for kind in ('csr', 'csc'):
            with self.subTest(kind=kind):
                test_harness.h5ad(self.fixture.data, sparse=kind)
                with h5py.File(self.fixture.data, 'r+') as f:
                    f['X/indices'][1] = f['X/indices'][0]
                    f['X/data'][:2] = [3e38, 3e38]
                with self.assertRaisesRegex(HarnessError, 'duplicate sums'):
                    validate(self.fixture.data, 'T1:val', self.fixture.panels)

    def test_finite_duplicate_sparse_sums_remain_supported(self):
        for kind in ('csr', 'csc'):
            test_harness.h5ad(self.fixture.data, sparse=kind)
            with h5py.File(self.fixture.data, 'r+') as f:
                f['X/indices'][1] = f['X/indices'][0]
            self.assertTrue(validate(self.fixture.data, 'T1:val', self.fixture.panels)['format_valid'])
            self.assertTrue(np.isfinite(ad.read_h5ad(self.fixture.data).X.toarray()).all())

    def test_competition_key_is_removed_from_child_environment(self):
        from embryo_harness import codex
        run = self.fixture.root / 'child'
        (run / 'work').mkdir(parents=True)
        prompt = run / 'prompt.txt'
        prompt.write_text('fixture')
        script = "import os; assert 'VE_CHALLENGE_API_KEY' not in os.environ"
        with patch.dict('os.environ', {'VE_CHALLENGE_API_KEY': 'test-secret'}):
            rc, timeout = codex.execute([sys.executable, '-c', script], prompt, run, 5)
        self.assertEqual(rc, 0)
        self.assertFalse(timeout)
