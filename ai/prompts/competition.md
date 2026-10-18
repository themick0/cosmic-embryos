# Cosmic Embryos competition operating rules

Required reading for every repository agent. This file is also included in the
initial prompt and frozen evidence of each configured research run. All authored
comments, docstrings, documentation, reports, and experiment narratives must be
in English. User conversations may use the user's preferred language.

Configuration validation requires this file first, `ai/prompts/common.md` second,
and task instructions afterwards in `prompt_files`, including local variants.
Introduce changed instructions through a new run; preserve existing snapshots.

## Scope and authority

Team: **Cosmic Embryos**. Track: **Agent Team**. Entry: ordinary independent team,
not a partner team. Verified on **2026-10-18**: P2 validation is open. This is a
dated operating brief, not a substitute for the full competition agreements.

Before a new run or upload, the operator checks current phase, boards, and rule
changes. Live API state determines availability and quotas; current Official
Rules govern eligibility. Treat conflicting website summaries as unresolved,
not as permission to use a more permissive rule. Report material uncertainty to
the user before the affected action; do not contact organisers without authority.
Inside a locked run, use the frozen assignment and permitted inputs, without
fetching updated instructions or accessing the competition API.

## Roles and the configuration lock

| Role | Responsibilities and boundaries |
| --- | --- |
| Repository maintainer / pre-run operator | Develop the harness and prompts, choose model, tools, permissions, dependencies and budgets before execution. Inspect completed experiments to plan new runs. |
| Locked research agent | Execute the frozen assignment, evaluate experiments and select the final output autonomously. Write only inside its run's `work/`. No human steering, configuration changes, submission API, credentials, or other runs. |
| Submission operator | Submit the agent-selected file from a chosen completed run. Verify its bundle and preserve its contents. Keep upload records separately from frozen research evidence. |

The configuration lock occurs at `execute`, recorded in `started.json`. A person
may choose among completed runs, but must not choose intermediate outputs for
an active agent. Do not edit a completed prediction or reconstruct evidence.
[Official Rules, sections 9 and 14](https://virtualembryo.ai/challenge/rules).

Repository policy: every attempted run directory is single-use. A failed run
gets a new directory and lock; the old evidence stays intact. The competition
allows unchanged crash restarts, but this harness does not implement resume.
No subagents are enabled in the current locked configuration. Changing these
capabilities belongs to pre-run development, not an active run.

## Submission quotas and coordination

| Phase | Scored allowance for this team |
| --- | --- |
| P2: T1 | 8 per UTC day |
| P2: T2 | 8 per UTC day shared across embryo interpolation, heart interpolation, and heart extrapolation |
| P2: T3 | 8 per UTC day |
| P3 | 2 official submissions per board across the entire phase |

Daily limits reset at 00:00 UTC. Website and API use the same allowance; extra
keys or agents do not increase it. Format-only checks spend neither scoring nor
upload allowance. Rejections consume upload allowance but not scoring attempts.
P3 allowances do not reset daily. Query live quotas, including upload ceilings,
recovery credits and `official_per_board`, before sending anything official.
[API documentation](https://virtualembryo.ai/challenge/account/api-keys).

Current implementation: `api-check` reads state; `submit` reads and saves quotas.
There is **no local quota blocker or reservation across different runs/boards**.
Only the server enforces the remaining allowance. The local process lock covers
one run/board, not the team. Operators must coordinate who owns the next attempt,
check allowance immediately beforehand, and stop when it is exhausted. Never
test quota handling by spending real submissions. Use mocks for upload tests.

## Data, targets, and scientific integrity

Current P2 assignments:

| Board | Target | Configured measured inputs |
| --- | --- | --- |
| `T1:val` | E10.5 RNA | E8.5 and E9.5 RNA |
| `T2:embryo:val_interp` | E7.5 embryo | E6.75, E7.25, E8.0 embryo |
| `T2:heart:val_interp` | E8.5 heart | E8.25, E8.75, E9.5 heart |
| `T2:heart:val_extrap` | E10.5 heart | E8.25, E8.75, E9.5 heart |
| `T3:gata4` | Gata4 KO at E8.75 | WT E8.75, WT E9.5, Mab21l2 KO E9.5 |

T2's two settings share a run in the template, but are separate populations.
Actual allowed paths are enumerated in `context.json`; this table grants no
additional data access. Future targets are T1 E12.5, T2 embryo E7.75 / heart
E12.5, and T3 beta-catenin KO E8.75. Obtain the actual released contracts before
creating test configurations; never relabel a validation prediction as test.
[Task definitions](https://virtualembryo.ai/challenge/tasks).

Use only explicitly authorised measured inputs. Current templates allow no
external measured data. Never access held-out data indirectly through pretrained
models, alternative alleles, nearby stages, or other assays. A future expansion
of the allowlist requires checking section 10's stage windows, genotype rules,
licences, and disclosure requirements before locking. Disclose external software
and data in the method report and sources file. Do not infer hidden target values
from returned scores. Never fabricate results or experiment history.
[Official Rules, section 10](https://virtualembryo.ai/challenge/rules).

Competition data is for research/education. Outside-Challenge use requires the
specified attribution; publication or public presentation using it requires
prior data-owner approval. Check the full credit and permission requirements
before sharing data or preparing a publication. A staged dataset is not evidence
of permission to publish it. [Terms, clause 14](https://virtualembryo.ai/challenge/terms).

## Prediction and evidence contract

Use the frozen panel's exact gene order and cell limits. Output a self-contained
AnnData `.h5ad` with finite, non-negative log-normalised expression. T2/T3 require
finite `obsm["spatial_3D"]` coordinates with at least three columns. Cell-type
labels are not prediction targets. Validate with the frozen harness; format
validity does not measure biological quality.
[Data contract](https://virtualembryo.ai/challenge/data),
[upload checks](https://virtualembryo.ai/challenge/account/submissions).

The local prediction cap is 1,200,000,000 bytes, conservatively below the API's
documented 1,200 MiB cap. Evidence archives are capped locally at 200,000,000
bytes each and 600,000,000 bytes per bundle. The **600 MB official evidence cap
is team-wide**, including other runs; bundling cannot establish remaining space.

Retain trajectory, all supplied prompts, harness, selected files, method report,
source disclosures, reproduction commands, versions, seeds, experiments and
failures. The current scoring gate needs two evidence kinds including trajectory;
our uploader attaches all three core kinds to each submission ID. Declare the
actual agent framework and model. Prize verification requires complete evidence
and reproducibility. Never trim or invent logs to fit a limit.
[Official Rules, sections 9 and 14](https://virtualembryo.ai/challenge/rules).

## Operator procedure and failure handling

Only the operator, outside a locked run, uses these commands from the repository
root. See `ai/README.md` for the full command arguments and setup.

1. `api-check --public-only`: inspect phase and accepted boards. `api-check`:
   inspect account, current agreements and quotas. A missing key or stale consent
   is not successful verification. Do not accept agreements on the user's behalf.
2. Before research: select config, inspect inputs/dependencies, run `doctor`, and
   use `codex-check` if access needs checking. The latter consumes model usage.
   Prepare a new snapshot, then execute it once. Models and budgets are explicit.
3. After completion: `verify`, then `bundle`. Use only the selected prediction
   and matching evidence. `format-check` contacts the server but creates no
   official entry. Offline validator tests do not need uploads.
4. Invoke `submit` only within the user's authorised submission scope. Setup,
   review and training requests alone do not request official uploads. Existing
   authorisation remains valid; do not ask for the same permission again.
5. Retry the same submission command with unchanged files and fields. Preserve
   `ai/submissions/` state and request IDs. Check status using `submission-status`.
   A successful HTTP response can still describe a rejected prediction.

Prediction retries reuse their idempotency key and recheck the original phase and
active board before every official POST. A changed or unknown original phase
blocks an uncertain retry: preserve state and inspect submission history to
recover an existing submission ID. Confirmed IDs may finish evidence after the
phase closes. A phase transition between the check and POST remains possible
because the API has no atomic expected-phase condition.

The uploader requires registration in Cosmic Embryos on the Agent Team track.
It saves the team's stable ID and track with each request, checks them on resume
and before official POST attempts, and permits key rotation within the same team.
State without this binding requires inspection of the original account before
recovery; do not replace it with whichever account the current key selects.

Evidence retries first reconcile the remote list; do not duplicate uncertain
uploads. An explicit server hash takes precedence over a content-addressed note.
Matching kinds alone cannot confirm an archive. Completion requires a final
remote identity check of all three archives and confirmation that the prediction
is queued, scoring, or scored. Check detail and recent history for reclaimed
prediction files. A nonempty evidence response note stops processing for
inspection, including on resume; HTTP 200 does not override that warning.
Reclaimed files require a separate authorised submission, never an automatic
replacement. `queued=false` alone is not proof of failure. On 429 stop and inspect the
quota. On 401/403/409 resolve credentials, permissions, consent or state first.
Do not loop unchanged 4xx requests. Poll status at 20-second intervals.
`submission-status` checks detail and recent history, records confirmed
reclamation, and stops polling immediately with `polling_stopped` set to
`prediction_reclaimed`. Preserve that record; a missing recent-history entry
alone does not prove deletion or erase previously confirmed reclamation.
[API documentation](https://virtualembryo.ai/challenge/account/api-keys).

The uploader alone reads `VE_CHALLENGE_API_KEY` from its environment or
`ai/.env.local`. Never print it, commit it, copy it into evidence, or ask a research
agent to read it. Environment removal and workspace-write do not isolate all
filesystem reads; use a separate environment when stronger isolation is needed.

Wall time is enforced; money, tokens, GPU, RAM and disk are not hard-budgeted by
this harness. Do not promise those guarantees. Hashes detect accidental changes,
not independent attestation. Organiser audit remains authoritative.
