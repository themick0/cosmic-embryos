# Repository instructions

## Required reading before work

Read [the competition operating rules](ai/prompts/competition.md) before changing
the harness, researching a solution, starting a run, or handling a submission.
Read [the harness instructions](ai/README.md) for the corresponding commands.
These instructions apply to every agent working in this repository, not only
to the agent launched by the harness.

Identify your role first: harness maintenance, a locked research run, or submission
operations. Do not apply an uploader's permissions to a locked research agent.

## Critical operating constraints

- Cosmic Embryos competes as an ordinary independent Agent Team. Do not use the
  partner-team quota rules.
- Read current phase, boards, account, and quotas before a new official upload.
  In P2, T1/T2/T3 each have eight scored attempts per team per UTC day. All three
  T2 validation boards share the same eight attempts. In P3, the limit is two
  official attempts per board for the entire phase, not per day. Live API state
  takes precedence over this dated summary.
- The client reads quotas but has no local remaining-attempt blocker or
  team-wide reservation. Coordinate submission ownership across agents; do not
  assume a different run, key, process, or browser has a separate allowance.
- Official uploads must be within the user's authorised task. A request to
  review, configure, test, or train does not by itself request an official
  submission. If the user already authorised the specific submission work,
  proceed within that scope without asking again.
- Never steer a locked run using intermediate results, change its configuration,
  or edit its selected prediction afterwards. Preserve frozen inputs, evidence,
  receipts, exports, and durable submission request identities.
- Never print, commit, or include API keys in prompts/evidence. Only the uploader
  reads `ai/.env.local`; locked research agents must not access credentials.
- Do not publish datasets or assume that staged dataset files may be pushed to
  a public repository. Follow the data-use requirements linked in the operating
  rules. Preserve unrelated user changes and staging.

## Required language

All authored code comments, docstrings, documentation, README files, research
reports, and explanatory text in repository files must be written in English.
This requirement applies throughout the repository, including generated code and
documentation. Write new content and revisions in English regardless of the
language used in the conversation.

Preserve original datasets, identifiers, verbatim external evidence, and frozen
run artifacts; do not translate or rewrite them to apply this rule. Conversation
with the user may use the user's preferred language.

## Harness location

Keep all harness infrastructure, including code, configuration, prompts, tests,
resources, and runtime artifacts, under `ai/`.
Source datasets stay under `datasets/`. Shared research modules and notebooks
outside individual runs belong outside `ai/`; a locked run's generated research
files belong in its own `ai/runs/<id>/work/` so they can be tied to its evidence.

## Deliver these rules to harness agents

Codex runs intentionally disable AGENTS.md loading. Every production task
configuration must include `ai/prompts/competition.md`, then
`ai/prompts/common.md`, then its task prompt in `prompt_files`. Keep these
instructions aligned when changing the workflow. Never rewrite existing frozen
snapshots to introduce new rules; prepare a new run.

## Repository code navigation

CodeGraph MCP is available for repository maintenance. Read
[its setup and scope](ai/mcp/codegraph/README.md). Use `codegraph_explore` when a
symbol or call-path query helps; its default project is `ai/`, and result paths
are relative to that directory. Confirm relevant source before editing.
If MCP has not loaded, use `bash ai/mcp/codegraph/run.sh explore "symbol names"`
from the repository root; run `sync` first when freshness matters.
Locked research runs must not query this shared repository index. Their Codex
command explicitly disables this MCP server; enabling a run-scoped index is a
pre-run configuration change that requires matching frozen evidence.
