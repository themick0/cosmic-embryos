"""Build a dependency-only pilot image and check its scorer without model calls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
from urllib.parse import urlsplit
import uuid

from .files import HarnessError, record, write_json


TRUST_FILE = Path("/etc/ssl/certs/ca-certificates.crt")
RECIPE_FILES = {
    "Dockerfile": "ai/resources/pilot/Dockerfile",
    "pyproject.toml": "ai/pyproject.toml",
    "uv.lock": "ai/uv.lock",
}
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
CODEX_VERSION = "codex-cli 0.163.0"
SECCOMP_SHA256 = "1d4b426f7ca08285ef13af50c9410cdbd24c87842aa63af99489313a39f42d31"
SECCOMP_PROVENANCE = {
    "upstream_url": "https://raw.githubusercontent.com/moby/profiles/refs/tags/seccomp/v0.2.4/seccomp/default.json",
    "upstream_sha256": "785b2429264afba4d594320337cb17f144f3c7d51585f9805eef72e28f4f9334",
    "upstream_version": "seccomp/v0.2.4",
    "delta": ["clone requires CLONE_NEWUSER", "mount and pivot_root",
              "umount2 requires MNT_DETACH", "unshare requires exactly CLONE_NEWUSER"],
}
# All upstream rules remain unchanged. Mount and pivot_root still require
# capabilities in the namespace being changed; the outer container drops every
# capability. These additions let non-root bubblewrap create its own isolation.
ISOLATION_CODE = r'''
import errno, json, socket, sys
from pathlib import Path

allowed = Path('/checks/allowed-write.txt')
allowed.write_text('allowed\n')
assert allowed.read_text() == 'allowed\n', 'Workspace write did not succeed'
outside_denied = False
try:
    Path('/home/agent/cosmic-outside-write.txt').write_text('changed\n')
except OSError as error:
    outside_denied = error.errno in (errno.EPERM, errno.EACCES, errno.EROFS)
assert outside_denied, 'Write outside the workspace was not denied'
tmp_denied = False
try:
    Path('/tmp/cosmic-outside-write.txt').write_text('changed\n')
except OSError as error:
    tmp_denied = error.errno in (errno.EPERM, errno.EACCES, errno.EROFS)
assert tmp_denied, 'Write to the excluded /tmp root was not denied'
network_denied = False
try:
    with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=3):
        pass
except OSError:
    network_denied = True
assert network_denied, 'Network access escaped the Codex sandbox'
print(json.dumps({'workspace_write': True, 'outside_write_denied': True,
                  'tmp_write_denied': True, 'network_denied': True}), flush=True)
'''
CHECK_CODE = (
    "from pathlib import Path; import json; "
    "from embryo_harness.campaign_image import check_runtime; "
    "check_runtime(Path('/checks')); "
    "from embryo_harness.pilot_checks import check_scorer; "
    "result = check_scorer(Path('/checks')); "
    "Path('/checks/result.json').write_text(json.dumps(result, sort_keys=True)); "
    "Path('/checks/requirements-resolved.txt').write_bytes("
    "Path('/opt/cosmic/requirements-resolved.txt').read_bytes())"
)


def check_runtime(directory: Path) -> dict:
    """Probe CLI parsing and actual sandbox restrictions without authentication."""
    report = {"version": 1, "ok": False, "model_calls": 0,
              "seccomp_profile_sha256": SECCOMP_SHA256}
    directory = Path(directory)
    try:
        version = subprocess.run(["codex", "--version"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=20, check=False)
        report["codex_version"] = version.stdout.strip()
        if version.returncode or version.stdout.strip() != CODEX_VERSION:
            raise HarnessError("Pilot image does not contain the pinned Codex CLI")
        prefix = ["codex", "--no-daemon", "-a", "never", "exec",
                  "--ignore-user-config", "--ignore-rules", "--help"]
        checked = subprocess.run(prefix, stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=20, check=False)
        (directory / "codex-prefix-help.txt").write_text(checked.stdout + checked.stderr)
        if checked.returncode or any(flag not in checked.stdout for flag in
                                     ("--json", "--output-schema", "--ignore-user-config", "--ignore-rules")):
            raise HarnessError("The full harness Codex prefix is unsupported")
        report["prefix_supported"] = True
        outside = Path("/home/agent/cosmic-outside-write.txt")
        outside.write_text("before\n")
        temporary_outside = Path("/tmp/cosmic-outside-write.txt")
        temporary_outside.write_text("before\n")
        # An outer loopback listener proves that failure inside the sandbox is
        # not merely the container's lack of external networking or DNS.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                pass
            # Codex 0.163 requires an explicit named profile for this diagnostic
            # command. These grants match the harness's workspace-only writes,
            # excluded temporary roots and disabled command networking.
            # https://learn.chatgpt.com/docs/config-file/config-reference
            report["sandbox_policy"] = {"extends": ":workspace",
                                        "filesystem": {":root": "read", ":tmpdir": "read", ":slash_tmp": "read"},
                                        "network_enabled": False}
            argv = ["codex", "--no-daemon", "sandbox", "-P", "cosmic-probe",
                    "-c", 'permissions.cosmic-probe.extends=":workspace"',
                    "-c", 'permissions.cosmic-probe.filesystem={":root"="read",":tmpdir"="read",":slash_tmp"="read"}',
                    "-c", "permissions.cosmic-probe.network.enabled=false", "-C", "/checks", "--",
                    "/opt/cosmic/.venv/bin/python", "-c", ISOLATION_CODE, str(port)]
            isolated = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=30, check=False)
        (directory / "codex-sandbox.log").write_text(isolated.stdout + isolated.stderr)
        if isolated.returncode:
            raise HarnessError("Workspace-write isolation failed under the production Docker restrictions")
        try:
            verified = json.loads(isolated.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise HarnessError("Codex sandbox did not return its isolation proof") from None
        if (verified != {"workspace_write": True, "outside_write_denied": True,
                         "tmp_write_denied": True, "network_denied": True}
                or outside.read_text() != "before\n"
                or temporary_outside.read_text() != "before\n"
                or (directory / "allowed-write.txt").read_text() != "allowed\n"):
            raise HarnessError("Codex sandbox isolation proof does not match observed files")
        report.update(verified, ok=True)
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)[:500]
        raise
    finally:
        write_json(directory / "runtime.json", report)
    return report


def _path(path: Path, *, file=False) -> Path:
    path = Path(path).absolute()
    if ".." in path.parts:
        raise HarnessError("Image setup paths cannot contain parent traversal")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise HarnessError("Image setup paths cannot contain symlinks")
    if file and (not path.exists() or not stat.S_ISREG(path.stat().st_mode)):
        raise HarnessError("Image setup requires an existing regular source file")
    return path


def _json(path: Path) -> dict:
    _path(path, file=True)
    if path.stat().st_size > 262144:
        raise HarnessError("Image setup JSON exceeds 256 KiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise HarnessError("Image setup JSON is invalid") from None
    if not isinstance(value, dict):
        raise HarnessError("Image setup JSON must be an object")
    return value


def _recipe(release_root: Path):
    contents = {name: _path(release_root / source, file=True).read_bytes()
                for name, source in RECIPE_FILES.items()}
    contents["trust.pem"] = _path(TRUST_FILE, file=True).read_bytes()
    try:
        dockerfile = contents["Dockerfile"].decode("utf-8")
    except UnicodeError:
        raise HarnessError("Pilot Dockerfile must be UTF-8") from None
    references = re.findall(r"^FROM\s+(\S+)\s*$", dockerfile, flags=re.MULTILINE | re.IGNORECASE)
    if len(references) != 1 or not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}", references[0]):
        raise HarnessError("Pilot Dockerfile must use one digest-pinned base image")
    files = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
             for name, data in contents.items()}
    recipe_hash = hashlib.sha256(json.dumps(files, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
    return contents, files, recipe_hash, references[0]


def _proxies():
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        value = os.environ.get(name)
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in ("http", "https") and parsed.hostname
                     and parsed.username is None and parsed.password is None
                     and not parsed.query and not parsed.fragment)
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise HarnessError(f"{name} must be an HTTP(S) proxy URL without credentials, query or fragment")


def _run(argv: list[str], log: Path, seconds: int):
    with log.open("ab") as stream:
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=seconds, check=False)
        except subprocess.TimeoutExpired:
            raise HarnessError(f"Image setup command exceeded {seconds} seconds; inspect {log}") from None
        except OSError:
            raise HarnessError(f"Cannot start Docker image setup; inspect {log}") from None
    if result.returncode:
        raise HarnessError(f"Docker image setup failed with status {result.returncode}; inspect {log}")


def _inspect(image_id: str):
    if not isinstance(image_id, str) or IMAGE_ID.fullmatch(image_id) is None:
        raise HarnessError("Docker did not return a content-addressed image ID")
    try:
        result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image_id],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise HarnessError("Cannot inspect the prepared Docker image") from None
    if result.returncode or result.stdout.strip() != image_id:
        raise HarnessError("Prepared Docker image is unavailable or has the wrong identity")


def _reuse(output: Path, files: dict, recipe_hash: str, base: str,
           release: Path, check_source: dict, runtime_source: dict, seccomp_source: dict) -> dict:
    manifest = _json(output / "image.json")
    if (manifest.get("version") != 2 or manifest.get("recipe_sha256") != recipe_hash
            or manifest.get("recipe_files") != files or manifest.get("base_reference") != base
            or manifest.get("release_root") != str(release)
            or manifest.get("check_source") != check_source
            or manifest.get("runtime_source") != runtime_source
            or manifest.get("seccomp_source") != seccomp_source
            or manifest.get("seccomp_provenance") != SECCOMP_PROVENANCE):
        raise HarnessError("Image output belongs to a different recipe or release; use a new directory")
    for key, filename in (("setup_check_result", "checks/result.json"),
                          ("runtime_check_result", "checks/runtime.json"),
                          ("seccomp_profile", "seccomp.json"),
                          ("requirements_file", "checks/requirements-resolved.txt")):
        artifact = _path(output / filename, file=True)
        if manifest.get(key) != str(artifact) or manifest.get(key + "_record") != record(artifact):
            raise HarnessError("Prepared image check artifacts changed")
    _inspect(manifest.get("image_id"))
    return manifest


def build(release_root: Path, output_dir: Path) -> dict:
    """Build once, or verify an existing successful setup without rerunning it.

    Only four public dependency inputs enter the build context. Failed or
    interrupted output directories remain available for inspection and are not
    retried automatically. This function performs no authentication or model call.
    """
    release = _path(release_root)
    if not release.is_dir():
        raise HarnessError("Release root must be an existing directory")
    output = _path(output_dir)
    if any("," in str(path) or "\n" in str(path) for path in (release, output)):
        raise HarnessError("Docker bind paths cannot contain commas or newlines")
    contents, files, recipe_hash, base = _recipe(release)
    check_source = record(_path(release / "ai/embryo_harness/pilot_checks.py", file=True))
    runtime_source = record(_path(release / "ai/embryo_harness/campaign_image.py", file=True))
    seccomp_path = _path(release / "ai/resources/pilot/seccomp.json", file=True)
    seccomp_source = record(seccomp_path)
    if seccomp_source["sha256"] != SECCOMP_SHA256:
        raise HarnessError("Pilot seccomp policy does not match the reviewed pinned profile")
    if output.exists() and not output.is_dir():
        raise HarnessError("Image output must be a directory")
    if (output / "image.json").exists():
        return _reuse(output, files, recipe_hash, base, release, check_source, runtime_source, seccomp_source)
    _proxies()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise HarnessError("Incomplete image setup retained; use a new output directory")
    try:
        with (output / "started.json").open("x", encoding="utf-8") as stream:
            json.dump({"recipe_sha256": recipe_hash, "model_calls": False}, stream)
    except FileExistsError:
        raise HarnessError("Another image setup already owns this output directory") from None
    context = output / "context"
    context.mkdir()
    for name, data in contents.items():
        path = context / name
        path.write_bytes(data)
        path.chmod(0o444)
    checks = output / "checks"
    checks.mkdir()
    policy = output / "seccomp.json"
    policy.write_bytes(seccomp_path.read_bytes())
    policy.chmod(0o444)
    if record(policy) != seccomp_source:
        raise HarnessError("Seccomp policy changed while preparing the image check")
    tag = "cosmic-pilot:" + recipe_hash[:16]
    iidfile = output / "image-id.txt"
    _run(["docker", "build", "--build-arg", "HTTP_PROXY", "--build-arg", "HTTPS_PROXY",
          "--iidfile", str(iidfile), "--tag", tag, "--file", str(context / "Dockerfile"),
          str(context)], output / "build.log", 900)
    image_id = _path(iidfile, file=True).read_text(encoding="utf-8").strip()
    _inspect(image_id)
    # The check uses synthetic files only, with no network or credential mounts.
    name = "cosmic-image-check-" + uuid.uuid4().hex[:16]
    check_argv = [
        "docker", "run", "--rm", "--name", name, "--network", "none",
        "--pids-limit", "512", "--memory", "8g", "--cpus", "3",
        "--user", "1000:1000", "--init", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--security-opt", "seccomp=" + str(policy),
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
        "--tmpfs", "/home/agent:rw,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=700",
        "--workdir", "/checks",
        "--mount", f"type=bind,src={release / 'ai'},dst=/research/ai,readonly",
        "--mount", f"type=bind,src={checks},dst=/checks",
        "--env", "PYTHONPATH=/research/ai", "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint", "/opt/cosmic/.venv/bin/python", image_id, "-c", CHECK_CODE,
    ]
    try:
        _run(check_argv, output / "check.log", 300)
    except BaseException:
        # Killing a Docker client does not prove its container stopped.
        try:
            _run(["docker", "rm", "--force", name], output / "cleanup.log", 30)
        except HarnessError:
            pass
        raise
    result_path = checks / "result.json"
    result = _json(result_path)
    if result.get("ok") is not True:
        raise HarnessError("Synthetic scorer check did not report ok=true")
    runtime_path = checks / "runtime.json"
    runtime = _json(runtime_path)
    if (runtime.get("ok") is not True or runtime.get("codex_version") != CODEX_VERSION
            or runtime.get("seccomp_profile_sha256") != SECCOMP_SHA256
            or any(runtime.get(key) is not True for key in
                   ("prefix_supported", "workspace_write", "outside_write_denied", "tmp_write_denied", "network_denied"))):
        raise HarnessError("Codex runtime isolation check did not succeed")
    requirements = _path(checks / "requirements-resolved.txt", file=True)
    if not requirements.stat().st_size or requirements.stat().st_size > 1048576:
        raise HarnessError("Resolved dependency list is missing or exceeds 1 MiB")
    if record(_path(release / "ai/embryo_harness/pilot_checks.py", file=True)) != check_source:
        raise HarnessError("Scorer check source changed during image preparation")
    if record(_path(release / "ai/embryo_harness/campaign_image.py", file=True)) != runtime_source:
        raise HarnessError("Runtime check source changed during image preparation")
    if record(seccomp_path) != seccomp_source or record(policy) != seccomp_source:
        raise HarnessError("Seccomp policy changed during image preparation")
    manifest = {
        "version": 2, "image_id": image_id, "tag": tag, "base_reference": base,
        "recipe_sha256": recipe_hash, "recipe_files": files, "release_root": str(release),
        "check_source": check_source, "runtime_source": runtime_source,
        "seccomp_source": seccomp_source, "seccomp_provenance": SECCOMP_PROVENANCE,
        "seccomp_profile": str(policy), "seccomp_profile_record": record(policy),
        "runtime_check_result": str(runtime_path), "runtime_check_result_record": record(runtime_path),
        "setup_check_result": str(result_path),
        "setup_check_result_record": record(result_path), "requirements_file": str(requirements),
        "requirements_file_record": record(requirements), "model_calls": False,
    }
    write_json(output / "image.json", manifest)
    return manifest
