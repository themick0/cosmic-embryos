# Starting the Cosmic Embryos team

Updated: 2026-10-19. This guide is for repository maintainers performing initial
setup. Read [competition.md](prompts/competition.md) first. The research strategy
and acceptance criteria are in [PLAN.md](PLAN.md); individual-run commands are
in [README.md](README.md).

## What is ready

The repository can prepare and execute individual frozen research runs, validate
their outputs and package evidence. It also has a separate submission client.
The bootstrap utility below creates isolated source workspaces and prints host
commands for three outer roles. It makes no model calls, reads no credentials,
copies no datasets and creates no Docker sandboxes itself.

The finite pilot has a Mac supervisor and a single-use Linux worker launcher.
`python3 ai/campaign.py prepare` builds and checks its execution environment;
`start` admits at most two sequential model sessions: an access/shell smoke check
and one 30-minute T2 embryo research assignment. See section 4 for the commands.
The pilot makes no official uploads. The continuing three-role research queue,
automatic method selection between runs, team-wide submission reservations and
nomination service remain unimplemented. Passing a mailbox check alone does not
establish model access or complete campaign readiness.

Final integration review on 2026-10-18: all 196 repository tests passed. Real
ARM64 Docker checks passed for the pinned CLI, filesystem/network isolation and
official scorer on synthetic data. A synthetic execution through the production
worker also confirmed retained outputs, terminal container state, watchdog exit
and duplicate-job rejection. Review fixes covered CLI compatibility, source
mount layout, error propagation, usage retention and delayed Docker launch
handling. Reports are retained under `ai/campaign/image-validation/revision5/`
and `ai/campaign/worker-validation/`; the suite log is
`ai/campaign/pilot-final-tests.log`. These checks made zero model calls and no
official uploads. OpenAI authentication in the final worker remains for the
budgeted smoke check; the synthetic rehearsal does not attest it.

Latest code review on 2026-10-19: all 213 repository tests passed. Regression
coverage includes stop/admission races, changed launcher code and assignments,
ledger recovery, interrupted or expired launches, watchdog storage failures and
explicit setup retries. Cached release manifests now match the independently
computed source inventory, worker release metadata is checked, and job snapshots
copy only manifested source files. Corrupted metadata is rejected and preserved,
never repaired in place. These fixes received independent review.

The latest suite log is `ai/campaign/final-audit-20261019-4def611c-tests.log`;
earlier test logs remain unchanged under `ai/campaign/`. The code reviews used
local and mocked checks without model calls, official uploads or a repeated real
Docker rehearsal. The scientific protocol and declared reproduction evidence
were reviewed. Final worker model access, actual scientific completion and
quality, Mac resource use and subscription consumption still require the bounded
pilot. The current acceptance is for that pilot; the continuing campaign gates
in section 5 remain open.

## 1. Prepare the Mac once

Use a terminal on the **Mac host** for the commands in this section. A shell
inside the existing Linux sandbox cannot manage sibling Mac sandboxes. Its
`docker` command manages an inner Docker Engine instead.

Save work and close unused desktop applications, especially WebStorm and most
Chrome windows. Keep the Mac on power, with a stable connection and sleep
disabled for the eventual campaign. The process list alone does not establish
available RAM: observe macOS Memory Pressure and swap growth under load.

The initial resource allocation is:

| Sandbox | CPU allocation | RAM cap | Function |
| --- | ---: | ---: | --- |
| `cosmic-coordinator` | 1 | 3 GiB | Queue, completed-result review and scheduling |
| `cosmic-worker-a` | 4 | 10 GiB | One research launcher and its execution environment |
| `cosmic-uploader` | 1 | 3 GiB | Evidence verification and submission ownership |

These are initial limits, pending measurement, not measured workload sizes.
Account for **all** running sandboxes, including the current maintenance sandbox.
Do not add these allocations to another busy 16 GiB sandbox and assume that the
32 GB host has enough headroom. After saving maintenance work, inspect `sbx ls`
and stop unused sandboxes by their actual names. Preserve every active locked
run until it finishes. Start with one heavy numerical job; add worker B only
after calibration. Idle outer roles need no continuously running model session.

From the Mac terminal:

```bash
cd /Users/tenshou/Projects/pets/cosmic-embryos
python3 ai/sandbox_setup.py doctor
```

The bootstrap requires Python 3.11+ and the current standalone `sbx` CLI. Its
doctor checks the host and required CLI flags with bounded, read-only probes.
If it reports a missing or incompatible CLI, follow the official
[Docker Sandbox setup documentation](https://docs.docker.com/ai/sandboxes/)
before continuing. Do not replace these commands with guessed
`docker sandbox` equivalents from another version.

If authentication has not already been configured, use Docker's documented
host-side subscription flow:

```bash
sbx login
sbx secret set openai --oauth
```

OAuth opens a browser on the host. Use the existing Codex subscription; an API
key is a different billing path. Sandbox agents do not inherit the host's Codex
configuration. See the [Docker Codex integration guide](https://docs.docker.com/ai/sandboxes/agents/codex/).
The user has confirmed completing OpenAI login; do not repeat it unless the
worker's deterministic credential-mode check reports a binding problem. Local
`sbx` execution does not require a paid Docker subscription; cloud execution
and model-provider billing are separate. See the
[Docker Sandbox FAQ](https://docs.docker.com/ai/sandboxes/faq/).
Model IDs and reasoning effort must be explicit in each future assignment and
verified using a bounded, budgeted smoke check before production research.

## 2. Generate isolated workspaces

Run preparation **once**, from the repository root on the Mac or the current
maintenance sandbox. If preparation has already succeeded in this checkout,
use `commands` directly; do not overwrite the generated workspaces.

```bash
python3 ai/sandbox_setup.py prepare
python3 ai/sandbox_setup.py commands
```

`prepare` refuses an existing destination. It creates the following ignored
runtime tree under `ai/`:

```text
ai/campaign/bootstrap/
  control/                 # Setup manifest and generated command arguments
  workspaces/
    coordinator/           # Independent source copy
    worker-a/              # Independent source copy
    uploader/              # Independent source copy
  mailboxes/
    coordinator/inbox/
    coordinator/outbox/
    worker-a/inbox/
    worker-a/outbox/
    uploader/inbox/
    uploader/outbox/
```

Source copies deliberately exclude `.env*`, local configuration variants,
datasets, Git metadata, virtual environments, existing runs and symlinks. They
are separate working copies, not live synchronization with the main repository.
The CodeGraph scope document is copied to preserve the repository instructions;
the shared CodeGraph index and server integration are not installed in the roles.
Do not independently edit the same source path from several agents. During
implementation, designate one maintainer to integrate changes into the main
checkout; publish reviewed source revisions to idle roles before new runs.

Execute the **three `sbx create` commands printed by `commands` on the Mac**.
They include the correct absolute paths, quoted for the shell, explicit CPU/RAM
limits and `--skills off`. Inspect the resulting environments with:

```bash
sbx ls
```

The coordinator receives the whole mailbox tree. Worker A and the uploader each
receive only their own read-only inbox and writable outbox. The main checkout
and `control/` are not mounted into any role. Thus competition credentials in
the main checkout are not exposed by this setup. These mounts provide a
transport location; the preflight protocol below checks one request/reply per
role. The finite pilot uses host-to-worker dispatch; a continuing multi-role
queue still needs implementation.

Bootstrap preparation copies no data. Pilot preparation later copies only
E6.75, E7.25 and E8.0 embryo inputs and binds them read-only into a fresh inner
research container. That container receives no mailbox, other runs, competition
credentials or Docker socket. Its smoke check tests model access through the
same Docker OAuth route used by the subsequent research container.

Current syntax and resource options are documented in
[sbx create](https://docs.docker.com/reference/cli/sbx/create/). Existing sandbox
names are not automatically removed or replaced. Resource or mount changes may
require recreation; never recreate a sandbox containing an active locked run.

## 3. Check communication between the created sandboxes

After creating the three sandboxes, run this command in the **Mac host terminal**:

```bash
cd /Users/tenshou/Projects/pets/cosmic-embryos
python3 ai/team.py check
```

This installs a small, versioned Python probe inside each role's
`ai/campaign/preflight/` directory, preserving its existing harness sources and
the original bootstrap manifest. It sends a unique request through each inbox,
invokes a finite handler with `sbx exec`, and validates the returned ID, nonce,
role, workspace, request hash and probe source hash. Requests, replies,
deployment receipts and reports remain on disk. Duplicate messages cannot
overwrite a different message with the same ID.

The check starts stopped sandboxes as needed. It runs sequentially and leaves
them available afterwards; account for other running sandboxes before starting
it. In particular, the reported `codex-themick0` and `codex-cosmic-embryos` both
consume host resources. The user has confirmed that this maintenance session
runs in `codex-themick0`; keep it running. A repository's working directory does
not identify the enclosing sandbox. Stop another sandbox only after saving its
work and confirming it has no active job. Do not infer that it is idle from its
name or mounted workspace.

The check invokes no model, training, submission API or dependency installation.
It checks file visibility, Python version and whether Codex and Docker are on
PATH. It does not test authentication, Docker daemon access, dataset permissions,
installed scientific packages or inner research-container isolation.

- `communication_ok: true` means every selected role returned a matching reply.
- `environment_checks_ok: true` also requires Linux, Python 3.11+, the required
  harness files, Codex on PATH and Docker on the research worker's PATH.
- `runtime_ready: false` is intentional: this command checks communication only.

To inspect the latest report or repeat only one role's check:

```bash
python3 ai/team.py status
python3 ai/team.py check --role coordinator
```

Each invocation uses fresh request IDs. A timeout preserves the failed attempt;
it does not replay it. A late response may still arrive, but it cannot satisfy a
later invocation. The default timeout is 45 seconds per role. The result JSON
prints the report path under `ai/campaign/bootstrap/control/preflight-reports/`.
The `install` and `status` commands also work in the maintenance sandbox; the
real `check` requires the Mac host. Never run these maintenance commands inside
a locked research run.

## 4. Prepare and start the finite pilot

Run these commands in the **Mac host terminal**, after a successful three-role
`team.py check`. They publish a new content-addressed source release under the
worker workspace, preserving the original bootstrap sources and all old jobs.

```bash
cd /Users/tenshou/Projects/pets/cosmic-embryos
python3 ai/campaign.py prepare
```

Initial preparation calls no model. It checks the live P2 board contract,
attests managed OpenAI OAuth in `cosmic-worker-a`, builds an ARM64 image from a pinned base with
Codex CLI 0.163.0 and a pinned official `veckit` commit, then checks actual CLI
and sandbox behavior and scores synthetic data offline. Package downloads may
take several minutes. Inspect `ai/campaign/control/pilot-setup.log` if it fails;
no model admissions are created until setup succeeds. An incomplete image build
is retained and is not overwritten or retried automatically. After resolving
the setup failure, explicitly run `prepare` again to create a new setup-attempt
directory. A successful matching setup is verified and reused. This does not
reset an existing pilot ledger or retry a model session.

Once `ai/campaign/control/pilot.json` exists, `prepare` only returns that preserved
pilot. It does not refresh its phase observation, authentication, image checks
or source from the current checkout. Dispatch checks the live phase and frozen
source hashes again; preparation is not a way to apply later code edits to an
existing pilot. Preserve prepared jobs and their ledger when inspecting a failure.

The inner container uses the reviewed
[`resources/pilot/seccomp.json`](resources/pilot/seccomp.json), based on a pinned
Docker syscall allowlist. Its small namespace/mount extension enables Codex's
filesystem and network isolation while retaining non-root execution, dropped
capabilities, `no-new-privileges` and the default-deny syscall policy. Preparation
verifies the profile hash and tests allowed workspace writes, denied home and
`/tmp` writes, and denied tool networking. The worker uses the same profile;
its bytes and command are included in the retained execution evidence.

After successful preparation:

```bash
python3 ai/campaign.py start
python3 ai/campaign.py status
```

`start` detaches the supervisor and uses macOS `caffeinate -is` when available.
Closing the terminal does not end it. Keep the Mac powered and awake; this does
not provide reboot recovery or guarantee operation with a closed laptop lid.
The supervisor log is `ai/campaign/control/supervisor.log`. `status` only reads
local state and spends no model usage. If foreground output is preferred, use
`python3 ai/campaign.py run` instead of `start`; do not launch both intentionally.

| Pilot step | Model / effort | Limit | Persistent result |
| --- | --- | --- | --- |
| Access and shell smoke | `gpt-6-astra` / high | One session, 120 seconds | Command, prompt, trace and shell output |
| T2 embryo development and prediction | `gpt-6-astra` / high | One session, 1,800 seconds | Frozen run, selected prediction, validation and evidence bundle |

Research starts only after a successful smoke. Each inner container is limited
to 3 CPUs, 8 GiB RAM and 512 processes, with a fresh home. The outer watchdog
allows an additional 60 seconds for smoke cleanup or 300 seconds for research
preparation, verification and packaging; it also survives loss of the launcher.
These are initial resource limits, not measurements of the host's free memory.

The local SQLite ledger is `ai/campaign/control/pilot-sessions.sqlite`. It permits
two admissions total and one active session. Failures consume their admission;
uncertain outcomes retain the active slot. Re-running a command never refunds
or duplicates a session. A fresh phase/board check precedes each admission.
The ledger does not meter this maintenance conversation, other independent Codex
sessions, subscription resets or actual token charges. Captured usage is the
usage reported by the CLI, not a hard token cap or a guaranteed share of the
remaining weekly allowance. No API billing path or alternate model is selected.

To prevent the next session while allowing the current locked run to finish:

```bash
python3 ai/campaign.py stop
```

This persists `STOP_AFTER_CURRENT` under the same short lock used for session
admission and launcher creation. A stop recorded during phase checks or input
verification prevents the pending session from being admitted; a launcher
created before the stop owns the current session and may finish. The stop
command does not wait for that session to end, rewrite it or interrupt research.
There is no automatic retry, reset, resume of a locked run, official upload or
continuous search after this pilot. A stopped or failed pilot requires inspecting
its retained state before deciding on a new, separately budgeted assignment.

Each job lives in the worker workspace at
`ai/campaign/bootstrap/workspaces/worker-a/ai/campaign/jobs/<job-id>/`. Its
`output/runs/`, `output/exports/`, `output/checks/` and `output/report/result.json`
are host-visible bind mounts. The complete run, including failed traces, remains
after the inner container exits. `worker-state.json`, the deadline and outer logs
are outside the agent's mounts. The containers are retained for diagnosis.

The coordinator and uploader sandboxes remain idle during this finite pilot;
their communication has been checked, but no model is kept running there. The
Mac supervisor performs deterministic dispatch, and the worker verifies and
packages its selected artifact. Independent uploader review and official
submission belong to the next campaign stage.

## 5. Complete the continuing campaign controller

The finite pilot implements the initial admission, isolation and persistence
path. Complete the following broader acceptance gates before claiming a
continuing unattended competition campaign. Do not spend subscription allowance
keeping idle chat agents awake.

Deliver these capabilities in dependency order:

1. **Startup configuration and admission:** record model/effort choices, finite
   campaign allowance, reset information, parallel-session cap and submission
   mandate. Atomically reserve allowance before every model-backed session,
   including development, coordinator and review calls. Unknown reset timing
   must not imply an unlimited refill. Start with at most two model sessions
   total and one heavy numerical job; calibrate the pilot with one model alone.
2. **Dispatch and recovery:** one coordinator owns the queue; immutable messages
   carry IDs, assignment generations and hashes. Use atomic publication,
   acknowledgements and duplicate detection. Idle services wait without model
   calls. A durable supervisor owns role processes and restarts eligible services
   after failures; it never resumes or rewrites a failed locked research run.
3. **Research isolation and persistence:** create and prepare inside the final
   Linux execution environment. Persist the entire `ai/runs/<id>/` tree and
   exports, including logs and receipts outside `work/`. An external watchdog
   enforces the persisted deadline if the harness process dies. Completed
   artifacts alone return to the coordinator.
4. **Verification and release:** validate selected files without editing them.
   The uploader exclusively reads competition credentials and owns team-wide
   attempt reservations and durable request identities. Refresh live phase,
   boards, account and quotas before each authorized official upload. Recover
   uncertain responses before retrying. Implement final nomination separately,
   early in P3, with read-back verification.
5. **End-to-end rehearsal:** use fake model and submission services to exercise
   dispatch, duplicate messages, process death, deadline enforcement, exhausted
   allowance, full evidence persistence and restart. Then perform one bounded,
   authorized real-model pilot with measured RAM and usage. The first scientific
   assignment is the T2 embryo baseline described in `PLAN.md`; T1 and T3 remain
   queued for coverage.

The initial model candidates are Astra/high for coordinator decisions,
Sol/high for research and deterministic verification with optional Luna/medium
review. These are hypotheses, subject to actual account access and calibration.
Do not silently substitute models or change an active run's effort setting.

The one-time startup configuration must distinguish research permission from
official submission permission. This setup guide does not authorize an upload.
Once specific submission work is authorized, the uploader may proceed within
that mandate without asking for each routine step. Additional paid resources
require a separately specified spending limit; the existing subscription is
the default.

## Operating details that affect reliability

- Install dependencies independently inside each final Linux environment with
  `uv sync --project ai --locked`. Do not run Mac `uv sync` against a Linux
  sandbox's mounted `ai/.venv`. Frozen runs record executable identities and
  absolute paths, so preparing on the Mac and executing in Linux is invalid.
- `sbx run -d --name NAME` keeps a sandbox running; it does **not** start a model
  session, the dispatcher or a research assignment. `sbx exec` is foreground,
  so unattended services need a persistent supervisor. See
  [sbx run](https://docs.docker.com/reference/cli/sbx/run/) and
  [sbx exec](https://docs.docker.com/reference/cli/sbx/exec/).
- Service recovery after a Mac reboot and remaining allowance after a reset
  must be tested explicitly. Do not assume container persistence provides
  either. The operator should receive a compact status artifact and exception
  reports; routine queue handling must not depend on a human reading messages.
- Never send corrections into active locked research based on intermediate
  results. A new idea belongs in a new frozen assignment after completion.
- Maintain one authoritative source revision. Bootstrap source copies do not
  include the main checkout's full Git history or synchronize later fixes.
  Refresh or replace only idle environments, preserving existing artifacts.

The continuing campaign is ready only when the real pilot passes and the broader
queue, recovery, verification and submission gates are met. The commands in
section 4 launch only the explicitly bounded pilot; they do not claim or achieve
first place by themselves.
