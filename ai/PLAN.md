# Cosmic Embryos Agent Team campaign plan

Updated: 2026-10-19. Owner: the campaign coordinator, acting as a repository
maintainer and pre-run operator. Status: final planning review completed;
finite pilot implementation is present; runtime and research acceptance gates
below remain open until their recorded checks pass.

The objective is to reach aggregate Agent Team rank 1 and sustain that position,
allowing temporary drops to ranks 2 or 3, through the final competition period.
Track first attainment, subsequent sampled standings, and final audited placement
separately. A temporary lead does not complete the campaign. There is no measured
basis yet for estimating the probability of winning.

The user supplies initial setup, not ongoing supervision. Use three sandbox
roles initially: coordinator, research worker A and verifier/uploader. Add worker
B as the fourth sandbox after resource calibration. Persistent services handle
dispatch and recovery; models make bounded scientific decisions. The first
automatic assignment must produce a useful scientific baseline as well as test
the handoff. Establish coverage across all tasks, then concentrate resources
where a completed improvement could most plausibly close the aggregate gap.

This document incorporates the strategy review. It does not launch workers,
install dependencies, activate monitoring, or authorise official uploads by
itself. Existing user authorisation remains valid within its stated scope.
The [competition operating rules](prompts/competition.md) and
[harness commands](README.md) govern execution. This is an operator document;
research assignments receive only their relevant, frozen instructions.

The [startup guide](START.md) and `python3 ai/sandbox_setup.py` now provide
host checks and isolated bootstrap source directories with printed provisioning
commands. They do not implement dispatch or launch the campaign. The generated
mailbox tree is under `ai/campaign/bootstrap/mailboxes/`; the runtime controller
must use its configured location rather than assume a second queue path.
`python3 ai/team.py check` now exercises the real host-to-role mailbox transport
without model calls, using bounded probes with request and source hashes. This
is a setup acceptance check. `python3 ai/campaign.py prepare/start/status/stop`
now provides a finite host-supervised pilot: two sequential model admissions
(Astra/high smoke, then one 30-minute embryo research run), immutable jobs,
isolated inner containers, an independent deadline watcher and retained full
artifacts. It makes no official uploads. The coordinator and uploader remain
idle; continuing multi-role scheduling, independent release review, submission
reservations and nomination are still pending. The pilot's session cap does not
cover other Codex sessions or establish a hard token limit.

## Starting position

| Item | Verified state or planning constraint |
| --- | --- |
| Team | Cosmic Embryos, ordinary independent Agent Team |
| Public phase | P2 validation, accepting submissions on 2026-10-18 |
| Aggregate standings | At API timestamp 2026-10-18 16:58:54 UTC: syl 220.6573; Ablation 220.1347; YB-Salvectors 219.0903; Cosmic Embryos absent from scored rows |
| Local research | No research runs, exports, or submissions found during the audit; no demonstrated biological prediction method |
| Host | User reports Apple M1 Pro with 32 GB RAM |
| Current sandbox | Approximately 16 GiB RAM and 10 visible CPUs; use CPU as the established compute path |
| Codex allowance | User reports 60% of the weekly allowance remaining and three resets; reset type, timing, and expiry are unresolved |
| Model access | Existing Astra/high smoke check succeeded; Sol and Luna access and useful-work cost remain unverified |
| Dependencies | The original maintenance environment has AnnData, HDF5, NumPy and SciPy. The dedicated pilot image additionally has pinned veckit and scikit-learn and passed synthetic scorer checks; do not infer those packages exist in every sandbox |
| Harness | Frozen runs, validation, evidence packaging, uploader and finite persistent pilot dispatch exist. Continuing multi-role dispatch, team-wide attempt reservation, hard token limits and research resume remain unimplemented |

The standings are a dated observation from the
[public aggregate API](https://api.virtualembryo.ai/challenge/overall?track=agent).
Refresh the [phase](https://api.virtualembryo.ai/challenge/phase) and
[boards](https://api.virtualembryo.ai/challenge/boards) before dispatching a new
campaign stage. Public checks do not establish account consent or remaining
team allowance; the submission operator checks those separately.

## Phase transition and ranking

For each task, average the team's best scores on that task's active boards,
including zero for unattempted boards. Sum the three task scores. Use the live
API's board membership and ranking semantics rather than a fixed board count.
The [API schema](https://api.virtualembryo.ai/openapi.json) documents this rule.

| Target | P2 validation | Published P3 target |
| --- | --- | --- |
| T1 RNA | E10.5 | E12.5 |
| T2 embryo interpolation | E7.5 | E7.75 |
| T2 heart interpolation | E8.5 | No separate test target published |
| T2 heart extrapolation | E10.5 | E12.5 |
| T3 perturbation | Gata4 KO at E8.75 | Beta-catenin KO at E8.75 |

The [task definitions](https://virtualembryo.ai/challenge/tasks) imply four P3
targets. Confirm the actual released contracts before creating configurations;
do not invent test board identifiers. P2 gives a single T2 board one third of
that task's weight. If P3 has the two published T2 targets, each carries half.
Heart interpolation remains useful for method evaluation, with limited value
as a separate late-P2 optimisation objective.

P3 is scheduled to start on October 20, with validation data released for local
development. Final submissions and nominations are due December 2. Confirm the
exact cutoff and timezone; do not assume midnight. The
[official timeline](https://virtualembryo.ai/challenge/timeline) is authoritative.

At the transition, refresh rules, phase, panels, board contracts and released
data permissions; audit new input files and hashes; create fresh configurations
and runs. Preserve P2 artifacts unchanged. Do not relabel their predictions or
assume that every previously held-out or external dataset becomes permitted.
This refresh takes priority over unfinished P2 coverage. Stop dispatching obsolete
configurations, let active locked runs finish unchanged, and move unfinished
baseline work to the confirmed P3 targets.

## Team and operating boundaries

| Sandbox role | Initial model and effort | Responsibility |
| --- | --- | --- |
| S1 Coordinator | GPT-6 Astra, `gpt-6-astra`, `high`, on decision events | Own the queue; select questions, inspect completed runs, allocate resources and choose eligible candidates |
| S2 Research worker A | GPT-6.1 Sol, `gpt-6.1-sol`, `high`, subject to calibration | A launcher executes one frozen assignment in an isolated research environment |
| S3 Verifier/uploader | Deterministic tooling; optional GPT-6 Luna, `gpt-6-luna`, `medium`, for bounded review after access verification | Validate collected evidence; exclusively own credentials, attempts, uploads and recovery; never alter predictions |
| S4 Research worker B, optional | Same initial candidate as A | Execute a second independent assignment when the measured resource budget permits |

These model choices are starting hypotheses based on
[OpenAI's model guidance](https://learn.chatgpt.com/docs/models). Prefer Standard
speed and subscription authentication. Routine engineering can use Sol/medium
in a separately configured assignment. Choose model and effort before locking;
never change an active run's configuration. Do not silently substitute a model
when access fails.

Sandbox count is not the number of continuously active model sessions. Polling,
heartbeats, queue delivery, copying, hashing and status checks require no model.
During the pilot, measure one model in isolation. Later, cap active Codex
sessions at two initially, including coordinator/reviewer sessions. Reserve a
slot until its process exits, even while it runs numerical tools. Idle role
services wait without consuming tokens; they are not idle model sessions.

Workers are not permanently assigned to T1 or T2. Each task, including T3, must
have a queued baseline assignment and an owner in the first research cycle.
After coverage, allocate work by plausible aggregate benefit, uncertainty,
remaining attempts, measured resource cost and time to a usable result.
Local proxy metrics cannot be converted into a promised leaderboard gain.

Use isolated research roots with only the permitted data mounted read-only,
their own writable run workspace and necessary model authentication. Competition
credentials, mailboxes and other runs are absent. Prepare inside the final
environment: the harness freezes absolute paths, dependencies and executable identities.
Git versions source code; local artifact exchange is sufficient on this host.
Keep shared research modules outside `ai/`; explicitly enumerate and freeze any
module supplied to a run. Generated research stays in that run's `work/`.

The coordinator reviews completed runs and prepares new assignments. Workers
select their own final outputs. Current runs cannot receive messages, read
other runs, spawn agents or access the competition API. Follow the role
boundaries in [competition.md](prompts/competition.md); do not turn the queue
into a mechanism for steering active research.

### Communication and isolation

Use local file mailboxes as the initial transport. Docker documents isolated
sandbox networks and explicit workspace mounts, including read-only mounts;
do not assume a Docker Compose network connects separate Docker Sandboxes.
Confirm the installed product/version and its mount behavior during startup.
See [Docker isolation](https://docs.docker.com/ai/sandboxes/security/isolation)
and [workspace usage](https://docs.docker.com/ai/sandboxes/usage/).

The coordinator owns the registry and receives the entire mailbox tree under
`ai/campaign/bootstrap/mailboxes/`. Each worker and the uploader receive only
their own inbox and outbox under that tree; one producer owns each direction.
Messages contain schema version, message/assignment IDs, claim generation,
sender, type and payload hashes. Publish a complete file by atomic rename on the
same local filesystem, acknowledge it durably and deduplicate by ID. Test this
across the actual mounts; do not use cloud-synced storage or shared SQLite writes
from several sandboxes. Git is for reviewed source changes, not queue delivery.

A worker launcher receives an immutable assignment, creates a fresh execution
container inside its sandbox, and runs `prepare` and `execute` there. That inner
container receives the pinned harness, allowed inputs and this run's workspace;
it receives no mailbox, campaign archive, prior run or Docker socket. Model
authentication must work in that exact environment. Merely putting the launcher
and researcher in different directories is insufficient: the current CLI's
`workspace-write` setting does not isolate all reads. If this containment cannot
be demonstrated, dispatch remains disabled until a suitable boundary exists.
Keep launcher code and role prompts under tracked `ai/` infrastructure. Include
the code that controls research execution in the harness snapshot, plus the
container image digest, mount policy and resource limits in pre-run provenance.
Private queue state is runtime data, not a substitute for that frozen code.

Persist the complete per-assignment run tree and export location on durable
storage before execution, outside disposable container layers. Keeping only
`work/` is insufficient: `started.json`, trajectory, final selection and receipts
live beside it. Mount only this assignment's storage into its research container;
retain it after success or failure until archival has been verified.

The launcher reports only process/resource health while research is active.
After exit, it returns a completed sealed bundle or a failure record. Completed
lessons become frozen inputs to later assignments; no live chat enters a locked
run. A future collaborating multi-agent research run would need a separately
implemented and frozen communication graph, permissions and complete evidence.
It is not required for this initial campaign.

### One-time setup and autonomous authority

Before leaving the campaign unattended, record a startup manifest with:

- Installed sandbox/CLI versions, persistent paths, permitted data mounts,
  noninteractive model authentication and access to the chosen model IDs.
- Host and sandbox memory/CPU/disk limits, model-call concurrency, subscription
  reset details where observable, a dispatch allowance and recovery reserve.
- Approved source/dependency changes, allowed model/effort choices and campaign
  end conditions. No additional paid API or cloud spending is assumed.
- Existing official-upload authority, if any: team, phases, boards, attempt
  policy, evidence limits and final-nomination authority with the required
  captain access. Establish missing scope once at deployment,
  not before every submission. Without it, continue research and queue decisions
  while official uploads remain disabled. This planning request does not add it.
- Supervisor startup/restart configuration and a Mac power arrangement that
  keeps Docker available. Host sleep or shutdown suspends local progress.

After setup, the coordinator autonomously chooses methods, assignments, run
order, candidate selection and submission timing within that manifest. It may
prepare source or dependency revisions for future runs after local checks;
active environments remain pinned. Scientific uncertainty is a decision for
the coordinator, not a request for routine user supervision.

Authentication renewal, newly required agreement acceptance, unavailable host
resources, unresolved rule changes or spending beyond scope are exceptional
blockers. Record one actionable local notice, stop only dependent actions and
continue independent permitted work. Do not accept agreements or repeatedly
retry failed authentication. External notifications require a separately
authorised channel; the local status file is the default.

## Delivery sequence and acceptance gates

Build the minimum automatic single-worker loop first. Its first real assignment
also calibrates resources and produces a baseline. A second worker, dashboards
and general framework integration are not prerequisites for first science.
Recovery checks are required before unattended production, not before developing
the pilot. The coordinator records a missed target and revised forecast; phase
changes and submission deadlines never wait for earlier research gates.

| Gate | Internal target | Owner | Completion evidence |
| --- | --- | --- | --- |
| A Minimum autonomous path | First setup session, October 18 to 19 target | Maintainer | One-worker queue/launcher, pinned scorer, model access, isolated mounts and durable process observation work |
| B Useful pilot and recovery | Same startup cycle | Coordinator, worker and verifier | Baseline completes; resource burn measured; sealed export collected; model-free simulations meet the acceptance criteria below |
| C Scientific coverage | October 19, then complete remaining coverage promptly | Coordinator and workers | Separate completed baseline runs cover T1, T2 and T3; evaluation protocols and limitations recorded |
| P2 Scored feedback decision | At first eligible completed candidate, with a review at least every six hours while P2 remains open | Coordinator and uploader | Upload or justified defer within established authority; no dependence on completing all-task coverage |
| D P3 research readiness | At the October 20 release | Coordinator and operator | Actual test contracts, allowed inputs and panels verified; new configurations prepared |
| E First P3 decision | Within 48 to 72 hours of the actual opening | Coordinator and operator | Per-board submit-or-defer decisions with evidence, expected value of waiting, and next review date; eligible candidates may be sent earlier |
| N Nomination readiness | In parallel with P3 research; target the first week of P3 | Uploader maintainer | Nomination contract, authority, captain access and state readback verified; local recovery rehearsal passes or a specific external blocker is recorded |
| F Improvement cycles | Throughout P3 | Coordinator and workers | Completed comparisons, updated candidate registry, cost observations and submission decisions |
| G Final attempt decisions | November 27 | Coordinator and operator | Decide remaining second attempts and final nominations; all selected artifacts verified |
| H Delivery buffer | Aim to finish selected uploads by November 29 | Operator | Remote prediction and evidence status reconciled; preserve remaining time before December 2 for recovery |

### Review decisions and acceptance criteria

All four final-review findings are accepted because they prevent loss of useful
research, allowance or final eligibility. Their implementation order follows
the earliest operation that depends on them:

| Finding | Relevance and required timing | Observable acceptance evidence |
| --- | --- | --- |
| Shared model budget | High: required before the first campaign model session | Every session type acquires a durable reservation; concurrent requests cannot exceed the configured admission cap; a denied request launches no model |
| Complete run persistence | High: required before the scientific pilot | A disposable simulator container can be removed while its full run tree, trajectory, frozen configuration, input manifest and available receipts remain intact on durable storage; a partial copy is never selected |
| Orphan timeout enforcement | High for unattended operation; rehearse before enabling it | Killing the simulated harness parent terminates its research children without extending the frozen deadline; restarting the supervisor neither reruns the directory nor invents a completion receipt |
| Final nomination | High for final eligibility; implement alongside P3 research | The uploader verifies the intended team, board and submission identity in remote nominated state; an uncertain response is reconciled before retrying, as demonstrated against a simulator |

Also inject repeated completion/rank events: they must produce one pending
coordinator decision per deduplication key, not additional model admissions.
Run these failure checks with local simulators, without model calls or official
submissions. Nomination readiness is not a dependency of the pilot, P3 research
or otherwise eligible authorised uploads unless current rules explicitly make
it one. Research quality remains the primary uncertainty after these gates pass.

### Environment and handoff

Install the official [veckit scorer](https://github.com/aristoteleo/veckit) and
only the research libraries needed for the first methods. Pin versions before
`prepare`. Verify architecture compatibility and scoring dependencies before
running a model; do not assume packages can be installed inside an offline run.
Exercise representative scoring, since training memory alone understates usage.

Start with one numerical worker. The two T1 input matrices alone would require
about 4 GiB if both were densified to float32. Measure peak resident memory,
elapsed time and scorer copies, then admit a second worker only when the combined
peak leaves operating headroom. Set container CPU/RAM limits and numerical
library thread limits. Keep large arrays sparse or process them in bounded
chunks where the method allows it. Two workers are a ceiling to test, not a
guarantee that any pair of jobs fits.

Include all sandbox VMs, the verifier and macOS in that measurement. The current
16 GiB sandbox observation is not a 16 GiB allowance for each of four sandboxes
on the 32 GB host. Keep worker B disabled when aggregate pressure is excessive.

The user's subsequent Activity Monitor snapshot reports WebStorm at 6.90 GB,
Chrome-named processes summing to approximately 12.8 GB, `node` processes to
3.35 GB, and two `containerd-shim-nerdbox-v1` rows at 2.32 and 1.55 GB. These are
reported process figures, not additive physical headroom or measured research
peaks. The user can close most desktop applications. Save work and close the
unused IDE/browser and identified development services before calibration;
do not indiscriminately terminate `node`, sandbox processes or system services.

Use these provisional guest-memory profiles after that cleanup:

| Profile | Coordinator VM | Worker A VM | Verifier/uploader VM | Worker B VM | Total guest allocation |
| --- | --- | --- | --- | --- | --- |
| Initial three-role pilot | 3 GiB | 10 GiB | 3 GiB | Off | 16 GiB |
| Four roles, after calibration | 3 GiB | 7 GiB | 3 GiB | 7 GiB | 20 GiB |

These are proposed settings, not installed limits or demonstrated requirements.
Keep roughly 8 GiB for macOS and essential applications plus approximately
4 GiB of additional host/VM overhead and contingency at the 20 GiB profile.
Confirm that the installed sandbox version enforces outer VM memory settings;
inner-container limits alone do not bound the VM. Leave space inside each worker
for its OS, launcher, Docker daemon and Codex process. Nested container memory
counts within its parent VM allocation, not as another host allocation.

Admit two heavy jobs only after representative fitting, scoring and verification
fit their individual profiles and the combined host remains healthy. Monitor
host Memory Pressure and swap growth as well as guest/container peaks; a Linux
guest's free-memory reading does not establish available Mac memory. Existing
nonzero swap alone is not a rejection criterion. Apple's
[memory guide](https://support.apple.com/guide/activity-monitor/actmntr1004/mac)
explains the host indicators. Feed host observations to the supervisor through
a read-only sampler configured at startup. Without reliable host telemetry,
default to one memory-heavy job, including verification.

Sustained memory pressure stops new heavy-job admission. Large training or
verification jobs receive a measured exclusive profile, with the second worker
off; do not force every method into 7 GiB or every verifier into 3 GiB. Select
and record limits before a new run starts. Never resize an active locked run
or cancel it to make room for another assignment. Smaller profiles must be
measured again before enabling concurrent work.

Use the first useful pilot to demonstrate this path:

1. Write one bounded assignment with allowed inputs and expected artifacts.
2. Prepare and execute inside the worker; finish successfully within its budget.
3. Verify and bundle there while the original input paths remain available.
4. Transfer the complete sealed `ai/runs/<id>/` and `ai/exports/<id>/` directories
   unchanged to the uploader's corresponding locations. Verify hashes; never
   overwrite a conflicting destination or rewrite frozen paths.
5. Exercise `submissions.load_bundle` locally for the selected board, without
   sending a request. It checks the copied run with `inputs=False`; the ordinary
   `verify` command still needs access to the original input paths.
6. Exercise supervisor restart, duplicate delivery, uncertain worker status and
   interrupted collection with a simulator before enabling unattended production.
   This rehearsal makes no official submission.

These requirements follow the existing
[bundle loader](embryo_harness/submissions.py) and
[run lifecycle](embryo_harness/core.py). Keep `ai/submissions/` exclusively under
the central operator, including durable request identities and retry records.

### Completed baselines before improvements

Create a short, independently completed baseline run for each required target
or compatible target group. Completion includes selected predictions, reports,
source disclosures, reproduction files, receipts and a verified bundle.
A prediction written during a failed run is not an eligible fallback.

Use the calibration to set total wall time and a finalisation allowance in the
initial prompt; initially reserve at least the last quarter for output
generation, validation and completion. Bound expensive subprocesses so the
agent can recover before the outer timeout. The wall-time kill remains a last
resort and does not produce a successful bundle. Never resume an attempted run
directory under the current harness.

Create separate new T2 configurations when embryo and heart work can be isolated;
the existing template requires all three validation predictions. Keep
`competition.md`, `common.md`, then task instructions in `prompt_files`.
Refine shared instructions through new snapshots only.

### First experiment card

Start with T2 embryo because the released stages support a bounded temporal
interpolation proxy. This is a calibration choice, not a claim that T2 is the
most valuable task. Queue T1 and T3 immediately behind it; do not let repeated
pilot refinements consume their first research cycle.

| Item | Frozen pilot specification |
| --- | --- |
| Development target | Withhold released E7.25 from fitting; use E6.75 and E8.0 for constructing predictions |
| Comparators | Nearest-stage resampling; one time-weighted mixture with coordinate normalization estimated only from the two fitting stages. No pairing of unrelated cells by row index |
| Evaluation | Pinned official T2 embryo scorer, E7.25 pseudo-target and E6.75 reference; the same panel, cell budget and fixed seeds for both methods |
| Selection | Before lock, record exact primary metric names and directions from the pinned scorer. If verified local skill calibration exists, use its fixed aggregate; otherwise use mean rank across those metrics, ties favoring the simpler comparator. Retain every component and label rank aggregation as a local heuristic |
| Final fit | A run dispatched under P2 makes all three permitted embryo stages available and applies its selected fixed method to E7.5: nearest E7.25, or an E7.25/E8.0 mixture. Its target stays frozen if the phase changes during execution. New P3 runs use separately verified target/input contracts; the uploader checks current eligibility |
| Deliverable | One agent-selected prediction, evaluation report, resource/usage measurements, reproduction commands, provenance and complete verified evidence |
| Budget | A short predeclared wall-time budget including at least one quarter for finalisation; no broad parameter search or additional method family |

Use E7.25 results to select the pilot's method, then label them development
results. They are not an independent estimate of hidden-target accuracy. If the
second comparator fails its fixed resource budget, the agent can complete with
the valid baseline and disclose the failure. This is preferable to losing both
methods to an outer timeout. Phase changes never alter an active assignment.

## Scientific evaluation and method search

Before each research run, freeze the allowed data, development/evaluation roles,
preprocessing restrictions, baseline comparators, metric definitions, seeds,
resource limits and candidate selection procedure. The agent may adapt its
experiments autonomously within those constraints.

Compare methods under identical conditions and retain component metrics, cost,
failures and selection rationale. Learn preprocessing, alignment, feature
selection and parameter choices from the permitted training side of each
evaluation. Repeated tuning makes an evaluation set part of development; do not
continue calling it independent. Numerical seed or cell-sampling variation is
not an estimate of variation between embryos.

| Task | First comparators and bounded hypotheses | Validation limits and decision evidence |
| --- | --- | --- |
| T1 | Nearest-stage population/resampling baseline; then regularised distribution shifts, population-composition models or transport methods as resource limits allow | Audited RNA `obs` contains only `celltype`; embryo-independent splits cannot be claimed from it. Before release, two stages give limited temporal validation. After release, withhold E10.5 from fitting for a temporal proxy; record the longer E12.5 forecast horizon and lack of independent stage replicates. Measure distribution and covariance preservation alongside expression changes. |
| T2 embryo | Nearest-stage and interpolation comparators; then joint expression and coordinate transport | Keep embryo separate from heart. After release, predict E7.5 without fitting to its answers and document transfer assumptions for E7.75, whose truth remains hidden. Registration and coordinate scaling must not use the withheld target. Inspect sampling, geometry and local-neighbourhood sensitivity. |
| T2 heart | Nearest-stage, interpolation and extrapolation comparators; then regularised temporal changes with spatial structure | Use released earlier stages for temporal proxies and, after release, withhold E10.5 for extrapolation evaluation. E8.5 interpolation success does not establish E12.5 forecasting. Spatial sections or cells are not automatically independent embryos. |
| T3 | WT identity and simple perturbation-transfer comparators; then bounded biological hypotheses and response models | A cell split within Mab21l2 does not test unseen-gene generalisation. After release, withholding Gata4 provides one cross-perturbation proxy, not proof of beta-catenin accuracy. Check effect directions, magnitudes, population shifts and sensitivity to assumptions. |

These are experiment candidates, not commitments to an untested winning method.
Use official metric code with explicit reference inputs. Record raw and
normalised scores distinctly and verify what calibration is available locally;
do not assume a local pseudo-target score equals the official hidden score.
Format checks establish file validity only.

After selecting a method, the agent may refit it using all inputs permitted by
the frozen target contract, including released validation stages where allowed.
Keep the earlier evaluation results separate from this final fit; evaluating on
data used in refitting does not provide a new independent validation result.

When enough genuinely distinct evaluation units exist, reserve a final check
that the search does not inspect. Otherwise document the missing independence
and use conservative selection, biological plausibility and controlled
ablations. Do not invent a holdout by relabelling cells from the same sample.

After an initial baseline, compare a small number of materially different
methods before expanding parameter search. Advance a method when its gain over
the comparator persists under the available checks and is not explained by
leakage or sampling noise. If two comparable completed iterations yield no
credible gain, review the method family and task allocation before spending
another large run. This review happens between runs.

### External sources

Give a bounded early audit to published methods and permitted external data,
especially for T1. For each candidate source record version, licence, measured
stages, genotypes, assays, provenance, preprocessing and proposed scientific
value. For pretrained models, inspect training-data provenance too. Apply the
current stage-window and genotype rules before accepting a source; unresolved
eligibility excludes it from the run.

Current templates allow no external measured data. Add an eligible source only
through a new explicit allowlist and frozen input hashes, with disclosures in
the method report. Source data stays under `datasets/`; public availability or
Git staging does not establish publication permission. Consult
[the data policy](prompts/competition.md) and
[Official Rules section 10](https://virtualembryo.ai/challenge/rules).

## Budget and dispatch decisions

Use one representative completed pilot to calibrate the chosen model. Record
allowance before and after, elapsed time, model/effort, trace usage where
available, peak RAM, failures and useful scientific output. Avoid other model
work during this measurement, or mark its allowance attribution as uncertain.
One pilot is an estimate; update it after subsequent runs.

The initial 10/65/25 allocation is superseded. Before each batch, record the
actual remaining allowance when observable, reset time, running commitments and
reserve needed for final generation, independent review and failure recovery. Admit a new run
only if the observed burn suggests those commitments fit. Initially dispatch
one calibration run; later batches contain at most two research runs. If
allowance cannot be observed reliably, use the startup manifest's finite cap on
dispatches per confirmed allowance window, count failed/model-check calls too,
and keep sequential admission. Do not depend on an undocumented quota API or ask
the user to inspect usage after each run. Exhausting the cap stops new model
work until a confirmed reset or an authorised budget update; unknown reset dates
do not replenish it. Wall time and token counts do not establish a hard
subscription limit. Numerical work already admitted may finish without another
model call only if its completion procedure permits it.

Every model-backed session passes the same admission gate: coordinator decisions,
reviews, engineering, smoke checks and research all debit the shared ledger.
The single supervisor atomically reserves the session's admission allowance and
concurrency slot before launch. Reconcile observed usage at exit; after a crash,
retain uncertain commitments until reconciliation instead of resetting the
ledger. Internal model turns count toward that session's observed consumption;
the reservation is not a hard token cap inside Codex. Apply this rule to the
pilot too, using its declared initial allowance before calibration is available.
Coalesce related events into one bounded coordinator turn and persist its
decision. Do not invoke it again for the same unresolved event without new
evidence or a due review. When model admission is paused, deterministic phase
checks, collection, monitoring and authorised upload recovery remain available.

Do not count the three reported resets until their type, availability and expiry
are confirmed. All workers share the account allowance. Use
[the current usage guidance](https://learn.chatgpt.com/docs/pricing), rather
than API dollar prices, to interpret subscription consumption.

Select models by cost per useful completed result. Escalate a future assignment
to Astra when completed evidence suggests Sol is missing important reasoning;
use Sol/medium for bounded engineering when adequate. Change effort only in a
new run. Batch numerical experiments in scripts and send compact summaries to
the agent, while retaining full logs on disk.

Keep API migration and extra compute outside the baseline budget. Consider
additional compute only after profiling shows a specific scientific bottleneck
and a concrete cost proposal fits the user's spending authorisation.

## Submission decisions and leadership monitoring

Follow [the operating procedure](prompts/competition.md) and live quotas.
Currently P2 allows eight scored attempts per task per UTC day, shared across
all T2 boards. P3 allows two official attempts per board for the entire phase.
An empty quota is a stop condition. Coordinate one uploader across all agents
and processes; current per-run locking does not reserve a team-wide attempt.

**P2:** send a selected completed candidate only when its scientific information
or plausible leadership value justifies the remaining time and evidence space.
The first defensible official score is a priority while this window remains
open: it measures competitive quality and exercises scoring/evidence latency
before P3's scarcer decisions. Review at the first eligible bundle and at least
every six hours; record submit, defer-to-a-specific-experiment, or blocked. Do
not wait for complete infrastructure or all-task coverage. Limit each deferral
to the next bounded experiment or phase refresh, whichever comes first; do not
exhaust quota for its own sake. Coverage in the candidate registry does not
itself require uploading every baseline. Once P3 opens, transition immediately.

**First P3 attempt:** consider a board as soon as its contract is confirmed,
the run and bundle pass verification, the method has survived the stated local
checks, and the coordinator has compared sending now with waiting for the next
bounded experiment. Record the selection, transfer uncertainty, remaining team
coverage and the cost of using one of two attempts. At Gate E, decide explicitly
for every board; do not defer indefinitely for an undefined stronger candidate.
Technical validity alone is insufficient reason to send a weak baseline.

**Second P3 attempt:** choose the best defensible remaining use of the board's
last attempt. Evidence may be a reproducible local improvement, a diagnosed
defect corrected in a fresh run, or a materially different method with a clear
transfer hypothesis that the available proxy cannot adequately test. Strict
improvement on every local metric is not required. A comparable proxy result
alone is not enough: explain why this candidate could outperform the submitted
one and what its failure modes are.

The board retains the better score, so a lower second score does not erase the
first; it spends the remaining opportunity. Compare using it now with waiting
for a named, bounded research cycle, considering the aggregate gap and calendar.
A competitor overtaking the team triggers this review, not an automatic upload.
At Gate G, evaluate every unused attempt and all eligible completed alternatives.
Do not discard a defensible alternative solely because imperfect local proxies
cannot prove its superiority; do not upload an arbitrary duplicate merely to
consume the allowance. The best-of rule is in
[Official Rules section 11](https://virtualembryo.ai/challenge/rules).

Before either attempt, verify the current phase, board, account, consent, team
quota and evidence capacity, and confirm that submission is within the user's
authorised scope. Preserve selected outputs byte-for-byte. Use existing durable
request state for retries; do not replace an uncertain request with a fresh one.
Account for the team-wide 600 MB evidence limit, including repeated archives
attached to different submissions. Never trim trajectories to fit.

Final nomination is a separate delivery operation. The current harness has no
nomination command; implement and test it in the uploader before the final
delivery window. The public API exposes `POST /challenge/teams/final` and a
`FinalRef` carrying `submission_id` and `bundle_key`, with one nomination per
board. At Gate N, verify captain permissions, accepted identifiers and how the
actual nominated state can be read back. Rehearse requests, uncertain
responses and reconciliation against a simulator, not a real test nomination.
Persist the intended submission identity and confirmed remote nomination; an
HTTP success alone or a leaderboard row does not prove the correct nomination.
Do not assume the platform nominates the best entry automatically.

The [API schema](https://api.virtualembryo.ai/openapi.json) description for this
endpoint still says test scores are hidden during competition, conflicting with
the current Official Rules and timeline's scored P3 board. Treat that description
as an unresolved documentation discrepancy, not a different submission policy.
Recheck at the phase release; report any remaining material uncertainty before
the affected action. If the discrepancy concerns only nomination, keep that
operation pending while permitted research and otherwise authorised uploads
continue. Do not use an official request to discover which interpretation is
correct. Keep public best-score standing and confirmed final nomination as
separate records.

After the minimum persistent monitor is implemented and exercised, sample the
public Agent Team aggregate approximately every six hours and on phase or submission
events. Store UTC time, phase, board coverage, leader, our score/rank and the
gap. Wake the coordinator for a rank change, phase change, completed result or
due decision; unchanged polls require no model call. Sampling does not prove
continuous leadership between checks. Keep local proxy results separate from
official scores; never reconstruct hidden targets
from score returns. Verify final nominations and evidence before the deadline.

## Durable coordination and memory

Implement a persistent deterministic supervisor around bounded coordinator
turns. It dispatches frozen assignments, observes process exit, collects sealed
outputs and invokes the model for the next decision. Persist the decision and
assignment before delivery. Only one supervisor may hold campaign ownership;
only one uploader may hold submission ownership, including after restart.

Use explicit assignment states: `queued`, `claimed`, `running`, `completed` or
`failed`, then `collected` for verified successful artifacts. Store the worker,
claim generation, run ID, process/container identity and last health observation.
A duplicate message cannot create another execution. An expired heartbeat marks
the state uncertain; it does not authorise assigning the same work elsewhere.
Reconcile the original process, `started.json`, exit status and receipts first.
Observe a surviving harness process without re-executing it. Persist the frozen
execution deadline and enforce it with an outer watchdog as well as the harness:
the current harness timeout runs inside its parent process. If that parent dies,
terminate orphaned research children, preserve the failed run and never extend
its budget or reconstruct a completion receipt. Rehearse parent loss explicitly.
For a confirmed failed run, create a fresh assignment/run if admission allows.

Copy outputs into a staging location, verify manifests and hashes, then publish
the collected state atomically. The coordinator selects only from verified
completed runs. Never overwrite a conflicting artifact or repeat an uncertain
upload under a fresh request identity. A supervisor restart is not research
resume. Keep numerical children under process/resource limits so detached jobs
cannot escape the assignment budget.

| Event | Automatic response |
| --- | --- |
| Terminal closes or coordinator process restarts | Persistent service reconciles ownership, assignments and receipts before dispatch; no replay of completed model decisions |
| Worker disappears or host/Docker restarts | Mark observation uncertain, reconcile on return, preserve partial artifacts; start a fresh run only after the original is known to have ended |
| Method fails or times out | Record failure; one revised fresh assignment may address a diagnosed cause within budget. After two consecutive failures of the same cause, suspend that family and allocate elsewhere |
| Subscription exhaustion or known reset | Stop new model calls, preserve state, retry only after the confirmed allowance window; no retry loop or unannounced paid API fallback |
| Transient upload interruption | Reconcile remote state and use the original durable request identity under existing uploader rules |
| Unknown phase, quota, consent or evidence capacity | Block the affected dispatch/upload; refresh and resolve without spending an attempt to test the condition |
| Phase opens, rank drops or a review becomes due | Queue a bounded coordinator decision using completed evidence; leave active research unchanged |

Keep a readable `ai/campaign/status.md` with current rank observation, active
runs, last completed result, next action and blockers. Terminate new research
admission early enough for the verified final cutoff; finish authorised uploads
and nomination checks, then retain evidence for audit. Reaching rank 1 changes
the allocation toward maintaining the lead; it does not stop the campaign.

Proposed private operator records belong under `ai/campaign/`. Exclude them from
Git, locked research mounts and harness evidence before enabling dispatch. The
current source snapshot whitelist excludes that directory; preserve this separation.

| Record | Minimum content |
| --- | --- |
| Startup manifest | Authority, models, resource/admission limits, reset observations, persistent mounts, tested recovery and campaign cutoff |
| Queue | Assignment/run IDs, task/board, owner, claim generation, config identity, process identity, health, dependencies, admission and dispatch state |
| Candidate registry | Run ID, selected output hashes, evaluation protocol, local metrics, uncertainty, cost, official result and per-board coverage |
| Budget ledger | Allowance observations, reset information, attribution limits, running commitments and remaining reserve |
| Submission ledger | Operator ownership, current quota response, decision, durable request identity, remote status and evidence footprint |
| Decision log | Completed evidence considered, next hypothesis, rejected alternatives and next review date |
| Leaderboard observations | Timestamped public standings, phase and coverage; no private account details |

Within each run, the agent maintains experiment records, current state,
reproduction commands and failures in `work/`, and declares the necessary files
for evidence packaging. Carry lessons into the next run through explicit frozen
prompts and permitted source files, with their provenance. Do not expose the
live campaign directory as shared research memory.

Use [memory and compaction guidance](https://developers.openai.com/cookbook/examples/agents_sdk/building_reliable_agents_memory_compaction)
for concise working state. [Compaction](https://developers.openai.com/api/docs/guides/compaction)
does not replace saved artifacts or add restart support to this harness.
[Agents API](https://developers.openai.com/api/docs/guides/agents-api/overview)
would introduce API billing and a different session lifecycle; retain CLI for
the initial campaign. Borrow the controlled experiment loop from
[Harness2](https://github.com/google-research/harness2) between completed runs;
full framework integration needs demonstrated benefit before it receives time.

## Execution checklist

- [ ] Record the one-time startup manifest, including existing upload authority and budget limits.
- [ ] Verify the three sandbox roles, local mailboxes, inner research isolation and persistent supervision.
- [ ] Verify shared admission for every model session and persistence of the complete run tree before the pilot.
- [ ] Pin the minimal scorer and research environment before preparing runs.
- [ ] Run the first T2 embryo pilot through the automatic launch/collection path.
- [ ] Pass the model-free recovery and event-deduplication criteria above; enable unattended production.
- [ ] Record measured consumption and reset details; enable worker B only if the shared budget and host fit.
- [ ] Freeze task-specific evaluation protocols and baseline assignments for T1, T2 and T3.
- [ ] Complete and seal baseline candidates; preserve failed attempts separately.
- [ ] Make the timed P2 scored-feedback decision if that phase is still open.
- [ ] Audit useful external sources and freeze only eligible additions.
- [ ] Refresh actual P3 contracts and create new configurations after release.
- [ ] Make per-board first-attempt decisions with scientific and operational evidence.
- [ ] Complete Gate N alongside P3 research; resolve nomination-specific blockers before final delivery.
- [ ] Monitor standings and phase with deterministic polling; invoke models only for actionable events.
- [ ] Review aggregate progress and reallocate work after completed experiments.
- [ ] Decide remaining attempts by the internal final review and verify nominations.

The bootstrap and finite pilot implement part of these gates. The checklist
tracks acceptance of the full campaign, not merely the existence of code.
Existing and pilot commands are documented in [README.md](README.md) and
[START.md](START.md). No research resume, continuing research scheduler or
automatic submission capability is implied by this plan.
