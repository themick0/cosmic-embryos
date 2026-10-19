# CodeGraph MCP

Repository-maintenance integration for
[colbymchenry/codegraph](https://github.com/colbymchenry/codegraph), pinned to
1.6.2 in `package.json` and `package-lock.json`. It runs locally over MCP stdio.

## Setup

From the repository root, with Python 3, Bash, Node.js and npm available:

```bash
npm ci --prefix ai/mcp/codegraph --ignore-scripts --no-audit --no-fund
python3 ai/mcp/codegraph/configure.py
bash ai/mcp/codegraph/run.sh init --yes
codex mcp get codegraph
```

Restart the Codex session after setup so it can load `codegraph_explore`.
Codex must trust the project to load its project configuration. Configuration
follows the [official Codex MCP documentation](https://developers.openai.com/codex/mcp/).

The generated configuration lives in `ai/mcp/codegraph/codex.local.toml`.
`.codex/config.toml` is a relative symlink to it: Codex requires that discovery
location. The generated file is ignored because it contains machine-specific
absolute paths. Re-run `configure.py` after cloning or moving the repository.
The script preserves an existing independently managed project configuration.

## Scope and storage

- The indexed project is `ai/`, which currently contains all repository source
  code. Shared research code added elsewhere later needs a separate index.
- The SQLite index lives in `ai/.codegraph/`; downloaded binaries live in
  `ai/mcp/codegraph/node_modules/`. Both are ignored by Git.
- `ai/codegraph.json` explicitly excludes runs, exports, submissions, checks,
  dependencies, local configuration, credentials and H5AD files. The sibling
  `datasets/` directory is outside the indexed project.
- The wrapper disables telemetry and removes `VE_CHALLENGE_API_KEY` from the
  subprocess environment. It never reads `ai/.env.local`.
- MCP clients use CodeGraph's shared local daemon, so multiple Codex sessions
  can share one index and watcher. The daemon may exit when idle; use `sync`
  when freshness matters.
- The wrapper preserves an explicit `CODEGRAPH_NO_DAEMON=1` override for
  environments that cannot use the shared daemon. Direct mode permits only
  one MCP writer per project; do not use it for concurrent sessions.

Index exclusions are not a filesystem sandbox. Repository-maintenance agents
already have access to the repository. Locked research runs explicitly disable
`mcp_servers.codegraph` to prevent this shared index from extending their frozen
data or tool scope. This integration is not automatically included in research
evidence. Adding CodeGraph to a research run requires a separate, frozen setup
limited to that run's permitted files and configured before execution.

## Use

Use `codegraph_explore` for symbols, callers, call paths and related source.
Omit `projectPath` to use the configured `ai/` project. Paths in results are
relative to `ai/`. Read the actual source before editing and verify uncertain
relationships; a static graph is a navigation aid, not proof of correctness.

Equivalent CLI commands are available immediately, including in a session that
has not yet reloaded MCP configuration:

```bash
bash ai/mcp/codegraph/run.sh sync
bash ai/mcp/codegraph/run.sh status
bash ai/mcp/codegraph/run.sh explore "submit read_prediction attach_evidence"
```

To upgrade, deliberately update the pinned dependency and lockfile, reinstall,
and rebuild the index. Do not use an unpinned `npx` command in MCP configuration.

## Switching from direct mode

Earlier versions of the wrapper forced `CODEGRAPH_NO_DAEMON=1`. Restart the
CodeGraph MCP connections in existing Codex sessions after updating the wrapper
so they use the shared daemon. A process already running in direct mode keeps
its writer lock until it exits. Do not delete `ai/.codegraph/writer.pid` while
its owner is alive. No reinstallation or index rebuild is required.
