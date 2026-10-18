from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import codex, core, panels, submissions
from .files import HarnessError, read_json
from .validation import inspect, validate

ROOT = Path(__file__).resolve().parents[2]


def parser():
    p = argparse.ArgumentParser(description="Cosmic Embryos autonomous-run harness")
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Check local tools without calling a model")
    doctor.add_argument("--codex", default="codex")
    smoke = sub.add_parser("codex-check", help="Make a small real model call and test sandbox execution")
    smoke.add_argument("--model", required=True)
    smoke.add_argument("--codex", default="codex")
    sub.add_parser("inventory", help="Read metadata from local datasets")
    sub.add_parser("sync-panels", help="Refresh official public gene panels before a new run")
    for name in ("prepare", "run"):
        cmd = sub.add_parser(name, help="Freeze a run" if name == "prepare" else "Prepare and execute a run")
        cmd.add_argument("--config", type=Path, required=True)
        cmd.add_argument("--model", help="Exact model string; overrides the template's null model")
    for name in ("execute", "status", "verify", "bundle"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run_directory", type=Path)
    check = sub.add_parser("validate", help="Check h5ad structure; does not compute a biological score")
    check.add_argument("prediction", type=Path)
    check.add_argument("--board", required=True)
    check.add_argument("--panels", type=Path, default=ROOT / "ai/resources/panels")
    api_check = sub.add_parser("api-check", help="Read current phase, boards, account and quota")
    api_check.add_argument("--public-only", action="store_true", help="Do not require an API key")
    for name in ("format-check", "submit"):
        cmd = sub.add_parser(name, help="Check on the server" if name == "format-check" else
                             "Create/resume an official submission and attach all evidence")
        cmd.add_argument("bundle_directory", type=Path)
        cmd.add_argument("--board", required=True)
        if name == "submit":
            cmd.add_argument("--method", required=True, help="Prediction method name (not the agent model)")
            cmd.add_argument("--architecture")
    remote = sub.add_parser("submission-status", help="Read and persist server status/scores")
    remote.add_argument("run_id")
    remote.add_argument("--board", required=True)
    remote.add_argument("--wait-seconds", type=int, default=0, help="Poll every 20 seconds until terminal or timeout")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        match args.command:
            case "doctor":
                from .validation import libraries
                h5py, np = libraries()
                result = {"python": sys.version, "h5py": h5py.__version__, "numpy": np.__version__,
                          "codex": codex.probe(args.codex), "root": str(ROOT),
                          "panels_available": (ROOT / "ai/resources/panels/index.json").exists(),
                          "model_authentication": "not tested (no model call)"}
            case "inventory":
                result = [inspect(p) for p in sorted((ROOT / "datasets").glob("*.h5ad"))]
            case "codex-check":
                result = codex.smoke_check(ROOT, args.model, args.codex)
            case "sync-panels":
                result = panels.sync(ROOT / "ai")
            case "prepare" | "run":
                run = core.prepare(ROOT, args.config.resolve(), args.model)
                print(json.dumps({"run_directory": str(run), "status": "prepared"}), flush=True)
                if args.command == "prepare":
                    return 0
                result = core.execute(run)
            case "execute":
                result = core.execute(args.run_directory.resolve())
            case "status":
                result = read_json(args.run_directory / "status.json")
            case "verify":
                core.verify(args.run_directory.resolve())
                result = {"integrity_verified": True, "run_directory": str(args.run_directory)}
            case "bundle":
                result = {"bundle": str(core.bundle(args.run_directory.resolve()))}
            case "validate":
                result = validate(args.prediction, args.board, args.panels)
            case "api-check" | "format-check" | "submit" | "submission-status":
                api = submissions.API(ROOT)
                try:
                    if args.command == "api-check":
                        result = submissions.readiness(api, public_only=args.public_only)
                    elif args.command == "format-check":
                        result = submissions.format_check(ROOT, args.bundle_directory, args.board, api)
                    elif args.command == "submit":
                        result = submissions.submit(ROOT, args.bundle_directory, args.board, api,
                                                    method=args.method, architecture=args.architecture)
                    else:
                        result = submissions.status(ROOT, args.run_id, args.board, api,
                                                    wait_seconds=args.wait_seconds)
                finally:
                    api.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (HarnessError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        print(f"harness: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("harness: interrupted; evidence retained", file=sys.stderr)
        return 130
