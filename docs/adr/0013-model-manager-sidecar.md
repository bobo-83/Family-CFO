# ADR 0013: One-click model apply via a model-manager sidecar; HF-sourced catalog (M23)

## Status

Accepted. Partially supersedes ADR 0012 (decision 1: command-only apply).
Amended 2026-11-19 for issue #167: shared model-swap lock protocol, specified
before implementation in the [M23 gate](../specs/11-milestone-roadmap.md#m23-hugging-face-model-search--one-click-apply).

## Context

ADR 0012 kept Docker control out of the API and had the dashboard generate a
`swap-model.sh` command. Product direction now requires one-click apply from
the dashboard — select a model (searched live from Hugging Face), click Apply,
watch it download/load/become active — without shell access.

## Decisions

### 1. A narrow, privileged **model-manager sidecar** performs swaps

The Docker socket still never enters the `api` container. Instead a dedicated
`model-manager` service (internal network only, never published) mounts the
socket and the project directory, and exposes exactly one mutating operation:

- `POST /swap {main_model, vision_model|null}` → validates the ids against a
  strict repo-id pattern (`org/name`, safe charset) → runs the already-tested
  `scripts/swap-model.sh` → tracks the run's state.
- `GET /status` → last swap state (idle/running/succeeded/failed + log tail).

Blast-radius reasoning: the sidecar accepts a constrained request shape from
the internal network only — never arbitrary commands — and the API (the only
caller) gates the trigger behind the `owner` role. Compromise of the API now
lets an attacker *swap models*, not run containers. That residual risk is
accepted in exchange for one-click UX; operators can remove the sidecar
(`--scale model-manager=0`) to fall back to ADR 0012's command flow.

### 2. The model list comes from Hugging Face Hub, proxied by the API

`GET /ai/models/search?q=` proxies the HF Hub API (`/api/models`, text-gen and
image-text-to-text pipelines, sorted by downloads) and maps results into the
same `AiModelInfo` shape. Specs are **estimated**: parameter count parsed from
the model id (`…-14B-…`), memory ≈ 2×params (bf16), disk ≈ 2×params, parser by
family, vision by pipeline/`-VL-` naming. The curated catalog (ADR 0012)
remains as "recommended" entries with hand-checked numbers.

Privacy note: only the operator's search string goes to huggingface.co — no
household data. Model downloads already depend on HF, so this adds no new
external party. Search degrades gracefully (503 → curated list only) when
offline.

### 3. Live apply status reuses the serving probes

Apply = manager swap + household-config update. The dashboard then polls the
existing `GET /ai/runtime/status` (plus the manager state via
`GET /ai/runtime/apply/status`) until `served_model` matches the selection —
rendering downloading/loading/active without new streaming infrastructure.

### 4. Dashboard and CLI share directory-flock ownership (issue #167)

The manager currently marks its own job `running` before invoking the script;
the script's manager `/status` probe can therefore reject that very job.
Process-local status also cannot atomically exclude a standalone CLI swap.
Replace that probe with one OS-level protocol, not a manager-only bypass.

#### Identity, admission, and inheritance

- Open the deployment directory with `O_RDONLY | O_DIRECTORY`, then acquire
  exclusive non-blocking `fcntl.flock` (`LOCK_EX | LOCK_NB`). The host path and
  sidecar `/project` bind mount must identify the same directory inode. The
  production guarantee requires the native Linux host/container shared kernel.
  macOS Docker Desktop virtiofs does not propagate host/VM flock: this dev-only
  topology must not run host CLI and container manager swaps concurrently
  expecting exclusion. Its explicit platform test skip does not replace the
  required Linux CI contention gate. Lock
  neither `.env` (rewritten during apply) nor a lock file; nothing is unlinked.
  The deployment directory must not be replaced during a swap. Verify directory
  flock on supported hosts; if portability makes this impossible, report back
  and amend the spec/ADR before using a dedicated `.swap-model.lock` fallback.
- The manager acquires ownership before publishing `running` or returning 202;
  synchronize admission and state changes for simultaneous dashboard requests.
  Contention returns 409 without starting a script, in either CLI-first or
  dashboard-first order. The API handles manager 409 before updating the
  initiating household config, repointing any other household, or writing the
  apply audit record; all remain unchanged on rejection.
- Launch with `Popen(start_new_session=True)`, `pass_fds=(lock_fd,)`, and
  `FAMILY_CFO_SWAP_LOCK_FD` set to that fd number. The script validates that the
  inherited fd names its deployment directory, then re-flocks the same open
  file description (no-op for the already-held lock). The environment marker
  alone never authorizes skipping the lock. Invalid descriptors fail closed.
- Without an inherited marker, the standalone shell opens and retains its own
  directory fd; a small `python3`/`fcntl` helper implements the same non-blocking
  acquisition. This works with the sidecar disabled. Contention exits nonzero
  with a retry message; other acquisition errors also fail closed. Remove the
  `/status` probe entirely; it is not an ownership test.

#### Critical section and failure lifecycle

Ownership spans configuration writes and Compose recreation until the swap
subprocess completes, not the asynchronous model download/load that continues
in vLLM afterward. Move vision-argument rejection and all hardware-fit “won't
fit” failures before the first `set_env` write, so these validation failures
cannot leave partially changed `.env` values. Script success alone does not
establish that the selected model is serving.

On timeout or controlled interruption, terminate the swap process group with
SIGTERM, escalate to SIGKILL after a bounded grace period if necessary, and
reap the subprocess before releasing the manager's descriptor. Record terminal
`failed`. Thread-start failure and subprocess-launch `OSError` must also become
terminal `failed`, not leave status `running`. Close the manager fd in `finally`
after completion/termination and reaping, or immediately when no subprocess
started. Standalone interruption must likewise terminate its child process
group and reap before releasing ownership, preventing a retry from overlapping
surviving swap work.

Close descriptors; do not explicitly unlock the shared open file description
while another inherited copy may be active. Ownership is released when the last
copy closes. This preserves exclusion if an orphaned script survives manager
termination. After restart, manager status resets to process-local `idle`:
there is no durable job state or job recovery. A surviving orphan keeps the
inherited lock until it exits and closes its descriptors, so a new request
must still get 409 even when `/status` says `idle`. The container runtime may
kill the script too; neither orphan survival nor successful completion is
promised. `idle` is not proof of an unlocked deployment.

#### Scope and explicit non-goals

This protocol covers model swaps only, not every `.env` writer.
`setup-cluster.sh` is explicitly excluded and remains an independent writer;
operators must not run it concurrently with swaps. There is no new automatic
rollback or transactional reconciliation after an accepted swap fails: 202 may
already have led to config repointing, and the existing desired-vs-serving
mismatch banner plus another Apply remain the recovery story. Do not promise
that the old model always keeps serving.

Advisor-tool access is N/A and an explicit non-goal: this infrastructure fix
adds no family-visible data domain and no M16 tool. No schema, public OpenAPI
shape, or generated-client change is required. Existing owner gating and the
socket-free API boundary are unchanged.

#### Rollout, mixed versions, and rollback

The script is bind-mounted; manager code is baked into an image. Ship them as
a matched pair. Quiesce dashboard and CLI swaps and let active work finish;
stop the manager, install the matching script, rebuild/recreate the matching
manager image against the same deployment directory, then verify both versions
before re-enabling swaps. Do not replace the deployment directory or bypass a
held lock during rollout.

Mixed versions are not a supported mutual-exclusion mode: a legacy manager
cannot pass ownership to the new script; a legacy script with the new manager
can still reject its own `/status` job; a legacy standalone script does not
honor directory-flock ownership. A script-only update is not a completed fix.
Rollback requires the same quiescence and restoration of both the script and
manager image as a matched pair; do not roll back one while swaps are active.

#### Verification gate

Implementation must cover both contention orders, simultaneous dashboard
admission, inherited fd/marker validation, thread-start/launch failure,
timeout/interruption group termination and reaping, release and retry, and
restart with `idle` status while an orphan holds the lock. API tests must show
that manager 409 leaves initiating config, other households, and audit untouched.
Real cross-process tests run the real script with synthetic `.env`/model ids
and stubbed Docker commands, including manager-disabled CLI and validation
before writes; no live runtime or weight downloads. Portable tests run on
macOS and CI. A Docker bind-mount test must prove host/container contention;
Docker must be declared/installed in CI with `FAMILY_CFO_REQUIRE_DOCKER=1`
(unavailable means failure, not silent skip). If CI integration is
disproportionate, report back before substituting documented operator
verification on the Spark boxes. Rollout docs follow in the implementation
work, separately from this spec/ADR gate.

## Consequences

- New service (`services/model-manager`), compose entry with docker socket +
  project mount; a fresh deploy is required to add it (not just an app patch).
- `POST /ai/runtime/apply` + `GET /ai/runtime/apply/status` +
  `GET /ai/models/search` join the contract.
- HF estimates are approximate; hand-checked numbers only exist for curated
  models. The fit verdict for arbitrary HF models is an estimate and labeled so.
