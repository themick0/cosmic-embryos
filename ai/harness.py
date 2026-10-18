#!/usr/bin/env python3
"""Run from the repository root: python3 ai/harness.py --help."""
import sys

# Validating through the frozen entrypoint must not add files to its snapshot.
sys.dont_write_bytecode = True

from embryo_harness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
