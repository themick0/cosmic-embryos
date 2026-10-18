"""Register the local CodeGraph MCP in this trusted Codex project."""
from __future__ import annotations

import json
from pathlib import Path


def main():
    directory = Path(__file__).resolve().parent
    ai = directory.parent.parent
    root = ai.parent
    target = directory / "codex.local.toml"
    registration = root / ".codex/config.toml"
    if registration.exists() or registration.is_symlink():
        if not registration.is_symlink() or registration.resolve() != target:
            raise SystemExit("Existing .codex/config.toml is not managed by this setup; "
                             "preserve it and merge the CodeGraph registration manually")
    if not (directory / "node_modules/.bin/codegraph").is_file():
        raise SystemExit("Run npm ci --prefix ai/mcp/codegraph --ignore-scripts --no-audit --no-fund first")
    args = [str(directory / "run.sh"), "serve", "--mcp", "--path", str(ai)]
    target.write_text(
        '# Generated local paths; regenerate after moving the repository.\n'
        '[mcp_servers.codegraph]\n'
        'command = "bash"\n'
        f'args = {json.dumps(args)}\n'
        f'cwd = {json.dumps(str(ai))}\n'
        'enabled = true\n'
        'enabled_tools = ["codegraph_explore"]\n'
        'startup_timeout_sec = 30\n'
        'tool_timeout_sec = 120\n', encoding="utf-8")
    registration.parent.mkdir(exist_ok=True)
    if not registration.is_symlink():
        registration.symlink_to("../ai/mcp/codegraph/codex.local.toml")
    print(f"Configured CodeGraph for {root}; indexed source root: {ai}")


if __name__ == "__main__":
    main()
