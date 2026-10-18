#!/usr/bin/env bash
# Run the pinned local CLI against the harness source tree.
set -euo pipefail
tool_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ai_root="$(CDPATH= cd -- "$tool_dir/../.." && pwd)"
unset VE_CHALLENGE_API_KEY
export CODEGRAPH_TELEMETRY=0
export DO_NOT_TRACK=1
export CODEGRAPH_NO_DAEMON=1
export CODEGRAPH_DIR=.codegraph
cd -- "$ai_root"
if [[ ! -x "$tool_dir/node_modules/.bin/codegraph" ]]; then
  echo "Install CodeGraph first: npm ci --prefix ai/mcp/codegraph --ignore-scripts --no-audit --no-fund" >&2
  exit 1
fi
exec "$tool_dir/node_modules/.bin/codegraph" "$@"
