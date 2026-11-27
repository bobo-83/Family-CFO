# ADR 0081: AI runtime Apply as a validated, recoverable transaction; manager protocol v2

## Status

Accepted (2026-11-21, issue #170). This ADR promotes `model-manager` to a
versioned operational component by exercising ADR 0074 Decision 4's escape
hatch, amends ADR 0013's non-durable/no-rollback statements, and clarifies
ADR 0071's topology-validation authority. Implementation is owned by the
ordered work in
`docs/plans/ai-runtime-apply-validation-recovery-2026-11-20.md` and must not
begin before the Spec Kit gate this ADR anchors is accepted.

## Context

Issue #170: the dashboard's Apply reported success after recreating containers
while the replacement never served a request — and the household's settings
had already been repointed. Today's flow (ADR 0013) accepts a swap, repoints
every matching household immediately after 202, and runs
`scripts/swap-model.sh` with no readiness probe and no rollback: `.env` is
mutated in place before containers are recreated, and manager status is
process-local memory that a restart resets to `idle`.

The new ordering guarantee cannot coexist with that protocol: a previous API
repoints households the moment the manager accepts, so bidirectional v1
mutation compatibility must be deliberately broken under an accepted ADR —
exactly the escape hatch ADR 0074 Decision 4 requires. Two prior decisions
shape the boundaries this ADR extends: ADR 0013 keeps the Docker socket in the
manager alone and keeps `setup-cluster.sh` an excluded independent `.env`
writer, and issue #167 made a shared directory flock the one swap-ownership
primitive for dashboard and CLI.

## Decisions

### 1. The manager owns authoritative launch planning

The model-manager resolves the deployment. It inspects the actual Compose
deployment and running image IDs, resolves repository IDs to immutable
revisions, normalizes chat/photo placement, runs the installed-image
compatibility checker, constructs and evaluates candidate topologies, and
freezes one valid plan — or refuses with an actionable reason naming the
limiting resource or missing evidence. The API supplies authorized intent plus
the household's cluster consent; clients supply desired roles, never launch
flags, memory numbers, image names, or peer addresses — their estimates are
presentation hints only. This extends ADR 0013's socket-holder boundary
instead of giving the API Docker access.

The installed-vLLM gate is version-adapted and config-only: an ephemeral
checker container from the exact installed immutable image ID constructs the
installed release's effective model configuration — including the automatic
Transformers-backend fallback — with no CUDA, no weights, no socket, no
privileged mode, and caps of 2 CPUs and 4 GiB. Registry allowlists and version
strings alone are not evidence; unsupported adapter versions fail closed
(actionable refusal, never a silent runtime upgrade).

### 2. Durable authority: manager journal + PostgreSQL receipt, never two job state machines

Execution progress, decisions, and snapshots live in a manager filesystem
journal (`PROJECT_DIR/.runtime-swap/v2/`; atomic writes; corruption fails
closed with `runtime_state_corrupt`, never an automatic reset to idle).
PostgreSQL owns household settings plus a small idempotent commit receipt
(`ai_runtime_apply_receipts`, migration `0099`): job identity, runtime key,
plan hash, initiator attribution, source, committed model, and commit time —
no mutable status column, no copied journal, no execution state machine in
SQL. API replicas reconcile the manager's durable decisions without an active
client and without a mutating status GET; the receipt guarantees exactly-once
settings commits. Full job rows in SQL were rejected: they duplicate state the
journal already persists across manager restarts and invite divergence between
two state machines.

### 3. Decision/receipt ordering: verify → decide → commit → ack → finalize

The only path to a settings change is: the target passes exact verification
(identity, routing, ranks, bounded synthetic probes) → the manager durably
records `decision=commit` (`commit_pending`) → an API reconciler commits
the household transaction (config upsert, matching-household repoint, receipt
insert, audit) under a PostgreSQL advisory lock, reading verification evidence
no older than 30 s → the manager is acknowledged → finalization re-verifies the frozen
candidate with a fresh bounded runtime verification (bounded by the
remaining phase budget, or the ≤3-attempt fallback horizon when none
remains) before recreating `api`/`worker` with their current application
images and verifying routing/health (`succeeded`; a failure here is
forward-repairable `commit_repair_required`, never automatic rollback).
The fresh verification is what keeps the replay path honest: a candidate
that died after the SQL commit is never reported `succeeded`. The decision is
monotonic — a committed decision never becomes restore — and SQL commits
happen only for durable decisions. The acknowledgement wait is bounded by
the persisted phase deadline: expiry without a receipt lands in
`commit_repair_required` (forward repair relaunches the frozen candidate
from the journal's durable target/plan record when it stays down,
re-verifies it bounded, and then re-awaits the receipt — never finalizing
an uncommitted decision and never rolling back; an exhausted relaunch/verify
budget remains `commit_repair_required` with recovery available), and a
receipt that has already landed is acknowledged regardless of evidence age —
freshness gates only the decision to commit. Forward repair is
forward-only in every case: restore remains exclusively the pre-decision
failure path. There is no atomic transaction across
Docker and PostgreSQL; the durable decision + receipt provide restart-safe
ordering, not instantaneous cross-system atomicity.

### 4. Rollback boundaries: restore an immutable, verified snapshot — never more

Before accepting a replacement the manager verifies the currently active
runtime and captures an immutable deployment snapshot: model revisions, exact
image IDs, structured launch arguments, the presence and value of every
runtime-owned `.env` key, service placement/scale, application photo
routing, peer settings required by that deployment, and cache references.
Restoration reinstates exactly that snapshot and repeats the full readiness +
inference verification; `restored` is persisted only after verification, and
restoration that fails or fails verification is `recovery_failed`, never
`restored`.

Never: restore the whole `.env` (only runtime-owned keys, including removing
keys absent previously), roll back application or database versions, prune
caches, or roll back after a durable commit — post-commit runtime failure is
an operational failure recovered forward (or by a new explicit Apply), because
household settings and routing flip together. A first deployment with no
previous runtime has no snapshot: its failure is
`failed`/`no_previous_runtime` after returning to the explicit empty
baseline, never a false "restored". These snapshots preserve the deployment
specificity the MiniMax investigation recorded: restoring model IDs alone
would lose pinned profiles and operational settings.

### 5. Lock ownership spans the full lifecycle

The #167 directory flock now covers validation, staging, launch, verification,
recovery, and commit acknowledgement — the whole transaction, not just script
execution. The manager acquires it non-blockingly during admission; the
separately supervised executor inherits the open file description and owns the
job until acknowledgement or verified failure (manager shutdown may leave the
executor alive, explicitly supported); journal mutations take a short
journal-file lock released **before** waiting on deployment ownership
(preventing lock-order inversion). A terminal record whose lock is still held
keeps admission at 409 until ownership actually closes. Ownership is never
inferred from a stored PID and a PID recovered from a stale record is never
signalled; only a child handle created by the current supervisor is
terminated/reaped, preserving #167's SIGTERM→SIGKILL grace-period discipline.
The `setup-cluster.sh` exclusion is unchanged: it remains an independent
`.env` writer that must not run concurrently with a swap.

### 6. Manager protocol major 2 (promotion under ADR 0074)

`model-manager` becomes a versioned operational component: manager protocol
major 2 and a `services/model-manager/BUILD` build identity file.

- `GET /v2/capabilities` returns protocol major, build, and journal schema.
- V2 mutation routes live under `/v2`: `POST /v2/jobs` (one-shot
  admission), `GET /v2/jobs/{id}`, `GET /v2/jobs/current`,
  `POST /v2/jobs/{id}/decision` (durable `commit`/`restore` from the API
  reconciler), `POST /v2/jobs/{id}/ack`, and `POST /v2/jobs/{id}/recover`
  (re-run the frozen direction of the durable decision). All are idempotent
  under their identity; there is no separate release mutator.
- Legacy `POST /swap` returns an upgrade-required error and performs **no
  mutation**. Legacy read-only `/status`, `/logs`, `/metrics` remain;
  `/status` projects the new lifecycle into the original coarse
  idle/running/succeeded/failed/unavailable enum so shipped clients keep
  decoding `AiSwapStatus`.
- A new API against an old manager disables Apply with an actionable 503
  (`manager_upgrade_required`) and never falls back to `/swap`.
- `shared/schemas/model-manager-v1-contract.json` stays immutable as
  historical evidence; `shared/schemas/model-manager-v2-contract.json`
  freezes the v2 surface.

The v1→v2 transition is the deliberate exception to bidirectional
api↔manager mutation compatibility: during the ordered migration a mixed pair
must **fail safely without mutation**, replacing the former "both mixed
versions can apply" test expectation. Within protocol v2, the rolling-deploy
and rollback compatibility expectation resumes.

### 7. v2 mutators authenticate with a deployment-scoped internal credential

v2 adds decision/ack/recover mutators that flip commit decisions and release
deployment ownership; network-trust-only (today's `/swap` posture) is no
longer adequate. All v2 mutators require a deployment-scoped internal
credential: an env-provided shared secret held by the API and the local CLI,
never stored in job state, journals, or the public status. The manager stays
internal-network only with no published port.

### 8. Admission is one-shot

The POST performs preflight (bounded at 180 s) → durable acceptance →
executor start → 202 in one synchronous call. No reserve/prepare-then-start
protocol and no reservation expiry can exist: a prepared-but-never-started
reservation is not a state. Repeated `request_id` with an identical
normalized request returns the existing job; different content is a 409. A
severed transport is safe — the identity makes the retry idempotent — and the
explicit transport budget chain (client ≥ 210 s, API→manager ≈ 190 s,
reverse-proxy read timeout verified ≥ 210 s) is a prerequisite of contract
activation, because every severed apply re-runs the full preflight.

### 9. Ordered migration

1. Accept this ADR and complete the Spec Kit gate.
2. Disable Apply operationally; drain any existing v1 swap; confirm the
   deployment flock is free.
3. Create the receipt/audit-deduplication schema (no backfill — historical
   applies get no fabricated receipt).
4. Deploy manager v2, then API/reconciler; during the skew, mutation fails
   closed.
5. Bootstrap the previous-working snapshot through inspection + bounded
   verification (never inferred from the old `_status`).
6. Deploy generated-client-backed web/iOS under the new contract.
7. Enable Apply only after protocol, baseline, and deployment smoke gates
   pass.

Rollback restrictions: drain/resolve every unfinished v2 job before reverting
the manager/API; never downgrade a journal while a job is pending; never
rewrite journal schema 2 as schema 1; API rollback must not re-enable legacy
`/swap` against manager v2; receipt-table downgrade only after jobs are
drained and the older application no longer references it; keep old
images/checkpoints until the rollout is accepted.

### 10. Remaining resolved decisions

- **CLI plumbing**: the CLI (`swap-model.sh`) invokes the same shared Python
  planner/journal/executor host-side with the existing `swap_lock.py`
  supervisor; the manager service wraps the same modules. The CLI stays a
  first-class citizen that works with the manager service unavailable; the
  modules import without FastAPI/Pydantic. (A thin-API-client CLI was
  rejected: an unavailable API/manager must not block host-side swaps.)
- **Old-client status skew**: the additive legacy projection above plus a
  server-synthesized `request_id` for legacy bodies without one. Shipped
  iOS/web keep decoding `AiSwapStatus` and applying; full-shape consumers
  are new clients only (ADR 0074 Decision 5).
- **Post-commit finalization**: `api`/`worker` are recreated only after the
  SQL commit, during manager finalization — never during `launching` — so a
  failed candidate never touches application routing and the application
  never restarts twice for one failed apply.
- **Cancellation**: no user/operator cancel of an in-flight job this issue.
  Recourse is the interruption/recovery story (durable journal + verified
  restore); pre-mutation cancel is a cheap, well-scoped follow-up if
  operators need it.

## Invariant

> A model replacement changes household settings only after the exact
> requested configuration is verified serving, and any earlier failure returns
> the deployment to a verified previous snapshot — or reports
> `recovery_failed`. Every mutation is journaled before it happens, every v2
> mutator is authenticated and idempotent, and a mixed-protocol pair mutates
> nothing.

## Consequences

- Swaps become long but bounded transactions with an exclusion window: another
  Apply blocks until commit or restoration; overlap would invalidate snapshots
  and recovery.
- Conservative refusal is intended: unfamiliar architectures or estimators may
  reject models that would have fit; the remedy is a verified deployment
  profile, not a skip-validation switch. Admission estimates remain estimates
  — exact inference verification and recovery stay mandatory.
- Clients may briefly observe a serving target while SQL still names the
  previous model; status reports `commit_pending` and reconciliation closes
  the gap.
- Versioned checker adapters need maintenance as installed vLLM versions
  change; unsupported versions fail closed instead of guessing.
- The manager gains journal/executor complexity but loses the "containers
  recreated = success" lie; operators gain `recovery_failed`,
  `commit_repair_required`, and an apply-state metric instead of a mismatch
  banner as the only recovery story.
- No blue/green guarantee and no visual-accuracy guarantee: memory-constrained
  replacement can interrupt AI serving, and photo smoke verifies transport,
  not accuracy.

## Rejected alternatives

- **Keep v1 and layer recovery on top.** The old API repoints households at
  acceptance; no recovery story can be layered under a protocol whose clients
  commit settings before verification. Deliberate breakage under this ADR is
  the honest path.
- **Full job tables in PostgreSQL.** Duplicates the journal's durable state
  machine and risks divergence; the receipt alone guarantees exactly-once
  settings commits.
- **Two-phase admission (reserve + expiry).** Unused complexity for a
  single-operator deployment; the one-shot window is bounded and made safe by
  the transport budget chain and request identity.
- **Blue/green serving.** Memory-constrained GB10-class boxes cannot hold two
  runtimes; staged replacement with verified restoration is the honest design.
- **Client-supplied launch flags, memory numbers, or topology.** Clients
  cannot see installed images, cache, or peer state; server-owned resolution
  is the point of the fix.
- **Automatic cancellation of in-flight jobs.** A cheap follow-up if operators
  need it; explicitly out of scope here.
