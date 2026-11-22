# AI runtime Apply: pre-launch validation + readiness-tracked swap with recovery (issue #170): Plan

## Goal

Make `POST /ai/runtime/apply` resolve a launch plan on the server and reject selections the installed runtime and hardware cannot serve **before** touching the running deployment, then track the swap through a verified-ready state (exact requested chat + photo configuration plus bounded synthetic inference) and only then commit household settings. On startup failure or timeout, automatically restore the previous working runtime and verify it answers; report a distinct recovery failure if restoration also fails. This replaces today's "containers recreated = success" reporting and the repoint-households-before-verified ordering. It must work identically for catalog models and uncatalogued Hugging Face search selections — the regression case is `nvidia/Qwen3.8-Flash-Next-NVFP4`.

## Background

### The incident (issue #170)

- The reported selection `nvidia/Qwen3.8-Flash-Next-NVFP4`: the web picker had **no usable memory estimate** for the ID, so it requested a single-box launch despite the household's enabled second-box (cluster) setting. The installed **vLLM 0.24.0** rejected the checkpoint's `qwen4_exp` architecture. The swap job reported success after recreating containers, although the replacement never served requests. Household settings had already been repointed.
- The exact checkpoint was later verified on two DGX Sparks with **vLLM 0.32.0, tensor parallelism 2, `gpu_memory_utilization` 0.72, 8,192-token context** — structured tool call + correct image description. Those settings are specific to that hardware/runtime and must become a reviewed deployment profile, **not** generic defaults. Checkpoint requirements: https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#usage
- Related: #167 (shared swap lock — closed, merged PR #178) and #168 (search relevance — implemented on branch `fix/168-ai-runtime-model-search`, unmerged as of 2026-11-20; this plan must not disturb it).

### Current flow (verified against code 2026-11-20)

**Web** — `apps/web/src/app/pages/ai-runtime/ai-runtime.ts` (1186 lines; the issue's L305–333 pin is stale — #168 inserted ~180 lines before the placement logic):

- Placement logic `planFor()` **L481–538**: vision-role model keeps current main as chat (L483–487); vision-capable main replaces both slots, vision=null (L488–492); text-only main keeps the currently served vision model (L493–499). Combined main+vision memory with `HEADROOM=1.15` for fit verdicts (L505–512).
- Cluster decision: `isClusterModel` L375, `needsCluster` L384–392 (catalog `min_nodes>=2` OR est. memory × headroom > this box's budget), `servesOnCluster` L393–394, `clusterActive` L371–373 (household toggle ON + peer reachable).
- Triggers: `applyModel()` L592–600, `applyAsVision()` L603–619, `applyVisionModel()` L1095–1119 (Advanced section, no cluster flag). `postApply()` L622–648 POSTs `applyAiModelSelection({main_model, vision_model, cluster?})`, then polls every 5 s (L651–672).
- `applyLive()` L566–590: UI phases `failed`/`active`/`working`; `active` requires only runtime `ready` && `served_model === lastAppliedMain` — **main-model equality is treated as success, ignoring the photo configuration**. Comment L584 admits `succeeded` = containers recreated, not serving. No "restored"/"recovery failed" states exist.

**API** — `apps/api/src/family_cfo_api/api/ai_runtime.py` (781 lines):

- `POST /ai/runtime/apply` L582–594 (202; documents 409/503). Handler `apply_ai_model_selection` L595+: 503 if manager URL unset (L601–605); `_REPO_ID` regex 422 (L606–609); hub pre-check `_hf_model_exists` 422, skipped when hub unreachable (L611–619); vision==main collapses to `vision_model=None` (L620–624, M51); vision-slot memory guard 422 (L626–641, `_vision_slot_gb`/`_model_estimate_gb` L438/447); cluster apply 409 if `cluster_peer_status()` peer down (L644–653, ADR 0071).
- Manager call L654–668: `httpx.post {manager}/swap`; transport error → 503, manager 409 → 409, other ≥400 → 503.
- **Household repointing happens immediately after 202** L670–690: upsert caller's row (preserves `cluster_enabled`), then `repository.repoint_ai_runtime_configs` repoints **all** households on the same provider/base_url (`repository.py` L3254–3272, one UPDATE). Audit `ai_runtime.model_applied` L697–705 — marked IRREVERSIBLE (`undo_actions.py`:128). Returns manager `AiSwapStatus` verbatim (L707).
- `GET /ai/runtime/apply/status` L711–732: thin **read-only** proxy to manager `GET /status`; `state="unavailable"` when unset/unreachable. `GET /ai/runtime/status` L170–256 probes vLLM `_probe_served_model` (L100–118) and classifies manager `/logs` into `loading_phase` (L154–168, M50) — useful diagnostics, not readiness authority.

**Model-manager** — `services/model-manager/src/family_cfo_model_manager/main.py` (266 lines):

- `POST /swap` L229–266: repo-id validation; 409 if `_status.state == "running"`; non-blocking directory `flock` on `PROJECT_DIR` (L240–247); sets `_status` running; daemon thread `_run_swap` (thread-start failure → failed + 503, L259–264).
- `_run_swap` L77–118 (issue's L54–78 pin stale; completion is `finally` L111–118): builds `bash scripts/swap-model.sh <main> [--cluster] [vision|none]`, `Popen(cwd=/project, start_new_session=True)`, `communicate(timeout=1800s)`, `state="succeeded"` iff rc 0, timeout → `_terminate_swap` SIGTERM→SIGKILL process group (L63–75). `GET /status` L223–226 returns in-memory `_status`.
- `scripts/swap-model.sh` does the real work: vision-capability probe — the script's only curl, reading `config.json` → `vision_config` (L112); the HF existence check lives API-side in `_hf_model_exists` (`ai_runtime.py:611–619`) — then GPU-fraction budgeting + fit refusal (L187–228), `.env` writes via `set_env` (L231–245), `docker compose up -d … vllm vllm-vision api worker` (L246–247). **No readiness probe, no rollback**: `.env` is mutated in place before containers are recreated; a failed `docker compose up` just becomes `state=failed` + log tail.

**Job state**: in-memory only. `SwapStatus {state: idle|running|succeeded|failed, main_model, vision_model, log_tail}` (main.py L42–46), module global `_status` (L52); manager restart resets to `idle`. API keeps no fallback memory. Durable state = target config only (`ai_runtime_configs`, models.py L618–632, migration 0017). `shared/schemas/model-manager-v1-contract.json` freezes the current /swap, /status, /logs contract.

### What #167 already built (merged, PR #178)

Shared **directory-flock** swap lock between dashboard and CLI: `scripts/lib/swap_lock.py` (`check_lock` :26–29 samestat-validates the fd, `supervise` :50–96 spawns the script with `FAMILY_CFO_SWAP_LOCK_FD` + `pass_fds`, `main` :99–129), CLI re-validates and re-flocks the inherited fd (`scripts/swap-model.sh:48–53`), manager flocks before publishing running (main.py :243–246), API 409-before-side-effects ordering with explicit tests (`test_ai_apply_api.py`:101). Orphaned scripts keep the inherited lock — a new request gets 409 even when `/status` says `idle`. Test matrix: `services/model-manager/tests/test_swap_lock.py` (12 tests incl. restart-with-idle, orphan fd, SIGTERM→SIGKILL grace + reap), `test_cli_lock_helper.py` (11), `scripts/tests/test_swap_model.py` (9, incl. real host/container flock :302, Docker-required per AGENTS.md), `apps/api/tests/test_ai_apply_api.py` (26).

### ADR surface

- **ADR 0013** (`docs/adr/0013-model-manager-sidecar.md`, 179 lines): model-manager sidecar is sole Docker-socket holder, internal-network-only; owner-gated apply endpoints; `GET /ai/models/search` HF proxy with **estimated** specs. Amended 2026-11-19 for #167 (Decision 4: shared directory-flock; admission before 202; inherited-fd ownership; terminal-failed lifecycle; matched-pair script/image rollout). **#170 must amend** the explicit statements "there is no durable job state or job recovery" (~L118) and the non-goal "no new automatic rollback or transactional reconciliation after an accepted swap fails; the mismatch banner plus another Apply remain the recovery story" (~L128–131; mirrored in the M23 gate Non-Goals, `docs/specs/11-milestone-roadmap.md`:1287–1331). The same non-goals section also states "No schema, public OpenAPI shape, or generated-client change is required" (~L134) — this design falsifies that statement too, which is why the contract release in `Detailed design `1 is coordinated under ADR 0074. **Must respect**: model-swaps-only scope (`setup-cluster.sh` excluded and must not run concurrently); manager 409 precedes config repointing + audit; the lock does not span the async download/load; script success is not proof of serving.
- **ADR 0071** (`docs/adr/0071-two-spark-cluster.md`): household explicit `ai_runtime_configs.cluster_enabled` toggle a swap must **never** flip; cluster-tier catalog `min_nodes: 2`; fit vs combined budget; serving = vLLM TP-over-Ray via `VLLM_EXTRA_ARGS`; peer loss degrades to single-node loudly. Invariant: cluster-tier selectable only while peer reachable AND toggle on; losing the peer never breaks single-node serving.
- **ADR 0074** (`docs/adr/0074-per-component-build-numbers.md`, Decision 4): model-manager is a deliberate exception outside the version scheme — its api↔manager interface must stay **bidirectionally compatible across rolling deploys and rollback** (manager-side replay in `services/model-manager/tests/test_compatibility.py`:30; API-side contract match in `apps/api/tests/test_model_manager_compatibility.py`:45–87); an incompatible apply/status contract change requires a follow-up ADR first promoting model-manager to a versioned component. Decision 5 contract bumps apply to any public OpenAPI change released clients begin to require.

### Spec Kit mechanics

Order in `docs/specs/README.md`: 01-prd → 02-adrs → 03-domain-model → 04-openapi → 05-database-schema → 06-security-model → 07-ai-orchestration → 08-mobile-spec → 09-angular-dashboard-spec → 10-docker-spec → 11-milestone-roadmap; tasks in 12-implementation-tasks.md. There are no per-milestone spec dirs — feature specs are sections in the numbered docs; milestone gates live in 11-milestone-roadmap.md; acceptance is recorded as `## Issue #NNN` sections at the end of `docs/specs/README.md` (pattern: :243–261, the #162 gate). Recent example: `docs/specs/09-angular-dashboard-spec.md`:230 "## AI runtime 'Find a model' search results (issue #168)". Gate checklist: Scope, Non-goals, API behavior, Data model changes, Advisor access (M16 registry or explicit non-goal), Security impact, Test expectations, Documentation impact. **Advisor access is N/A here** — this infrastructure fix adds no family-visible data domain (mirror #167's gate wording).

### External research: vLLM capability detection (verified against v0.32.0 source)

- Supported architectures: `ModelRegistry.get_supported_archs()` (`vllm.model_executor.models.registry`, exported as `vllm.ModelRegistry`). **No `vllm list-supported-models` CLI exists.** The registry alone can produce false negatives — with `model_impl="auto"` a Transformers-backend fallback is attempted first. The faithful gate is constructing `ModelConfig` in a subprocess (fetches config.json only — no CUDA, no weights; the exact path `vllm serve` runs at startup).
- Unknown-architecture failure (what the issue hit with `qwen4_exp` on 0.24.0): `ValueError: Model architectures ['qwen4_exp'] are not supported for now. Supported architectures: […]`, raised at `ModelConfig` construction before any weight load; serve exits non-zero. Variants: removed arch ("was supported in vLLM until vX…"), plugin arch, inspection failure.
- Installed version: `importlib.metadata.version("vllm")` (what `vllm --version` uses); `vllm collect-env` for diagnostics.
- 0.24.x → 0.32.x relevant deltas: arch removals between releases; 0.32 drops Python 3.10 and pins transformers ≥5.16.1,<5.20 (the v4→v5 cutover); 0.30 removed GPTQ `g_idx` ordering and aligned YaRN (derived `max_model_len` can drop); default `gpu_memory_utilization` 0.92 at 0.32; `--device-ids` replaced internal `CUDA_VISIBLE_DEVICES` handling at 0.24.
- Memory estimation: Hub `GET https://huggingface.co/api/models/{repo_id}` returns `config` (architectures, quantization_config), `safetensors.parameters` per-dtype counts (BF16 ≈ 2 B/param, FP8 ≈ 1 B, NVFP4 ≈ 0.5625 B — U8 entries are FP4-packed byte pairs), `siblings`, `usedStorage`; `?blobs=true` adds per-file sizes. KV cache per token ≈ 2 × layers × kv_heads × head_dim × bytes/dtype. Per-rank TP overhead: replicated embedding/lm_head, activation buffers, CUDA-graph pools, NCCL buffers (GBs). Multi-node: TP = GPUs per node, PP = nodes; Ray is the default multi-node runtime; weights must be present at the same path on every node. vLLM honors `HF_HUB_OFFLINE` — a server-side checker must respect the same convention.

### Prior art

- `docs/plans/ai-runtime-model-search-2026-11-19.md` (#168 plan) — explicitly excluded the apply flow; its prior-art note records that no plan covered the apply flow. Critique: `docs/reviews/ai-runtime-model-search-plan-critique-2026-11-19.md`.
- `docs/investigations/minimax-m31-runtime-upgrade-2026-11-08.md` — the "verified deployment profile" precedent: pinned checkpoint/image/revision, per-model KV-cache budgets, `--max-num-seqs=4`, Ray object-store bounds (two-Spark topology).
- `docs/investigations/qwen35-2b-peer-vision-2026-11-08.md` — peer vision deployment: explicit 256 MiB KV cache, dedicated describer via `FAMILY_CFO_AI_SUPPORTS_VISION=false` + `FAMILY_CFO_AI_VISION_BASE_URL` cluster-overlay override.
- Reconciliation prior art: ADR 0077 (cadence-independent maintenance pass under a lock; idempotent retries), ADR 0078 (transport-ambiguous persistence → reload + reconcile before retry), ADR 0019 (restart-safe background sync). ADR 0072 rejects a durable background-work queue for advisor jobs — a different domain, but naming matters here: this design is a deployment state machine, **not** a general-purpose job queue.

## Resolved design decisions

The four open questions from exploration, resolved with rationale. These were reconciled from three independent design passes that agreed on the architecture; disagreements and their resolutions are listed in `Reconciliation notes`.

| Decision | Choice | Rationale and tradeoff |
|---|---|---|
| Launch-plan ownership | **Model-manager owns authoritative resolution.** API supplies authorized intent + household cluster consent; clients supply desired roles, not launch flags, memory numbers, image names, or peer addresses. | Installed image, Docker deployment, cache, and topology are manager-side facts. Extends the existing socket-holder boundary (ADR 0013) instead of giving the API Docker access. Client estimates become presentation hints only. |
| Installed-vLLM gate | **Version-adapted, config-only `ModelConfig` checker run in an ephemeral container from the exact installed runtime image ID**, using pinned revisions and the same relevant options as the planned serve command. | Registry allowlists miss the Transformers-backend fallback and version-specific behavior; version strings alone prove nothing. Adds bounded metadata/checker work, not a runtime upgrade or candidate weight allocation. Unsupported adapter versions fail closed. |
| Durable authority | **Manager filesystem journal owns execution progress, decisions, and snapshots. PostgreSQL owns household settings plus an idempotent commit receipt.** No duplicate execution state machine in SQL. | These are different facts, not competing job authorities. API replicas reconcile manager decisions without an active client and without a mutating status GET. Alternative (full job rows in SQL, as two design passes proposed) rejected: it duplicates state the manager journal already persists across manager restarts and invites divergence between two state machines. |
| Rollback contents | **Immutable, verified deployment snapshot**: model revisions, exact image IDs, structured launch arguments, runtime-owned `.env` keys (presence + value), service placement/scale, application photo routing, peer settings required by that deployment, cache references. Restore it and repeat exact readiness + inference verification. | Restoring model IDs alone loses pinned profiles and operational settings (the MiniMax investigation shows how deployment-specific these are). Never restore the whole `.env`, never roll back application/database versions, never prune caches. |
| CLI plumbing | **Unify on shared Python engine modules; CLI (`swap-model.sh`) invokes the same planner/journal/executor host-side** with the existing `swap_lock.py` supervisor; the manager service wraps the same modules. | Preserves ADR 0013's first-class CLI status and #167's dual-path design; no dependency on the manager service for host-side operations; both paths get identical validation/journal/recovery. Modules must stay importable without FastAPI/Pydantic. Rejected alternative (CLI as thin client of the API workflow): simpler contract-wise but makes an unavailable API/manager block all host-side swaps — a regression for operators, and #167 explicitly treats the CLI as an equal citizen. |
| Admission shape | **One-shot**: the POST performs preflight → durable acceptance → executor start → 202 in one synchronous call (preflight ≤180 s). No reserve/prepare-then-start two-phase protocol. | Simpler state machine and route inventory; the synchronous window is bounded and made safe by an explicit transport budget chain (below). Two-phase admission (reserve + expiry, per one design lane) rejected as unused complexity for a single-operator deployment. |
| API↔manager v2 trust | **Deployment-scoped internal credential** (env-provided shared secret held by the API and the local CLI) required on all v2 mutators; never stored in job state or journals. | v2 adds decision/ack/recover mutators that flip commit decisions and release the deployment lock — network-trust-only (today's `/swap` posture) is no longer adequate. |
| Old-client status skew | **Additive legacy projection**: the status response keeps the coarse `AiSwapStatus` enum (idle/running/succeeded/failed/unavailable — new states projected: staging/launching/verifying/commit_pending → running, restored/recovery_failed → failed, succeeded → succeeded) alongside the new lifecycle fields; the apply request accepts legacy bodies without `request_id` (the server synthesizes one from the normalized request). | iOS ships via App Store with lag; an old build must keep decoding status and applying. Full-shape consumers are new clients only. Contract decision under ADR 0074 Decision 5. |
| Post-commit finalization | **`api`/`worker` are recreated after the SQL commit**, during manager finalization — never during `launching`. | Routing flips together with household settings; a failed candidate never touches application routing (no double app restart on failed applies); and the one post-commit recreation that can fail is precisely what `commit_repair_required` forward-repair re-runs. |
| Cancellation | **Explicit non-goal for this issue**: no user/operator cancel of an in-flight job. | Recourse is the interruption/recovery story (durable journal + verified restore). Pre-mutation cancel is a cheap, well-scoped follow-up if operators need it; recorded in the spec gate. |

### Version the manager before changing semantics (prerequisite)

The new ordering guarantee cannot coexist with the old protocol: a previous API repoints households immediately after manager acceptance (`ai_runtime.py:670–690`), so bidirectional v1 mutation compatibility must be **deliberately broken under an accepted ADR**, per ADR 0074's escape hatch. Accept a new ADR:

- Path: `docs/adr/0081-ai-runtime-apply-transactions.md` (0081 is the next unused number — latest existing is 0080; verify at write time).
- Promote model-manager to an independently versioned operational component: introduce **manager protocol major 2** and `services/model-manager/BUILD`.
- Permit the deliberate v1 mutation incompatibility during the ordered migration; mixed-version pairs must **fail safely without mutation** (replacing the former "both mixed versions can apply" test expectation).
- Amend ADR 0013's non-durable/no-rollback statements; extend lock ownership through validation, staging, startup, recovery, and commit acknowledgement; preserve the `setup-cluster.sh` exclusion.
- ADR 0071 and 0074 receive clarifying amendments (topology validation authority; promotion record).

New manager protocol behavior:

- `GET /v2/capabilities` returns protocol major, build, and journal schema.
- V2 mutation routes live under `/v2`; legacy `POST /swap` returns an upgrade-required error and performs **no mutation**.
- Legacy read-only `/status`, `/logs`, `/metrics` remain; `/status` projects the new execution state into the original coarse enum so old consumers degrade gracefully.
- New API against an old manager disables Apply with an actionable 503 (`manager_upgrade_required`); it never falls back to `/swap`.
- Keep `model-manager-v1-contract.json` immutable as historical evidence; add `shared/schemas/model-manager-v2-contract.json`.

V2 route inventory (caller → semantics; all mutators require the deployment-scoped credential; all are idempotent under their identity):

| Route | Caller | Semantics |
|---|---|---|
| `GET /v2/capabilities` | API, CLI | Protocol major, build, journal schema; read-only. |
| `POST /v2/jobs` | API, CLI | One-shot admission: validate + resolve + capture snapshot + persist + start executor; returns the job. Idempotent by `request_id` (replay returns the existing job). |
| `GET /v2/jobs/{id}` | API | Read-only execution state + monotonic sequence. |
| `GET /v2/jobs/current` | API | Active/latest job projection (backs the no-`job_id` status query). |
| `POST /v2/jobs/{id}/decision` | API reconciler | Durable `commit`/`restore` decision from the reconciler; same decision replays OK, opposing decision 409. |
| `POST /v2/jobs/{id}/ack` | API reconciler | Acknowledge the SQL receipt for a committed job → manager persists `succeeded` + releases ownership. Replay-safe. |
| `POST /v2/jobs/{id}/recover` | API (admin retry), manager startup scan | Re-run the frozen direction of the durable decision (`restore` → restoration; `commit` → forward repair). |

There is no separate `release` mutator: release happens as part of `ack` (terminal) or within `recover` failure handling. The journal-file lock + directory flock remain the concurrency primitives.

## Detailed design

### 1. Public API contract (OpenAPI first)

Edit `shared/openapi/family-cfo.v1.yaml` before implementation/generation; coordinated contract release under ADR 0074 (bump to the next unused minor at release time — do not guess from historical docs).

#### Apply request

Keep the public route and operation ID `POST /ai/runtime/apply` (202). New conceptual shape:

```text
AiApplyRequest
  request_id: UUID            # required; idempotency identity
  main_model: repository ID   # required chat target
  vision_model: repository ID | null
  photo_mode: auto | main | separate | off   # default auto
  topology: auto | single | cluster          # default auto
  cluster: boolean            # deprecated alias; 422 when contradictory with topology
```

Photo semantics (server-resolved, replacing per-client interpretation):

- `auto` with a distinct `vision_model` → `separate`; `auto` without one → `main` if installed-runtime metadata verifies multimodal support, else `off`.
- Equal main/vision IDs normalize to one instance and `photo_mode=main`; reject if the model cannot support images.
- `separate` requires a distinct vision ID with verified image-input support; a deliberate separate describer is allowed beside a multimodal main — the selected mode controls routing, capability alone never overrides intent.
- A vision-only picker action must preserve the current chat model; a missing current chat produces an actionable error, never a vision-model-as-main fallback. A text-only main preserves the current separate photo model; an all-in-one selection clears it.
- No omitted/null value ever means "use the script's default photo model."

Topology semantics:

- `auto` chooses a **validated** topology (single preferred; TP2-over-Ray only when household consent is on and the plan is valid). It never grants cluster permission.
- `single` prohibits automatic escalation; `cluster` requires consent + peer readiness + a valid two-node plan.

All selection entry points — catalog, HF search, raw ID, web, iOS, CLI — enter the same resolver. `request_id`: one UUID per user confirmation, retained across transport-ambiguous retries.

#### Responses, reads, recovery

- `POST /ai/runtime/apply` → **202 `AiApplyJobStatus`** only after successful preflight and durable acceptance. Repeated `request_id` + identical normalized request returns the existing job (never launches twice); different content → 409 `request_id_conflict`.
- `GET /ai/runtime/apply/status?job_id=<UUID>`: that job; without `job_id`: current job → latest retained terminal job → explicit idle. **GET is read-only**: never commits settings, launches recovery, acknowledges jobs, or creates rows.
- `POST /ai/runtime/apply/{job_id}/retry-recovery` (new `request_id`): re-runs the frozen direction of the durable decision — `restore` → the same frozen restore plan (for `recovery_failed`); `commit` → post-commit forward repair (for `commit_repair_required`). Never accepts a new model or upgrades images.
- All mutations require `ai_runtime.manage`; job diagnostics require the same right; ordinary runtime availability stays on the existing runtime-status endpoint.

#### Transport budget chain for the synchronous preflight

The 180 s preflight inside the POST requires an explicit budget chain — the current call budget is far smaller (`_HF_TIMEOUT_SECONDS = 6.0`, `ai_runtime.py:421`): client timeout ≥ 210 s (iOS needs an explicit long-timeout URLSession configuration — its 60 s default would sever a legal preflight); API→manager ≈ 190 s (new dedicated setting in `config.py`, not the shared `_HF_TIMEOUT_SECONDS`); reverse-proxy read timeout verified ≥ 210 s in the deployment spec. A severed transport is safe — the request identity makes the retry idempotent — but every severed apply re-runs the full preflight, so the chain is a prerequisite of contract activation (implementation step 9).

#### Status shape

```text
AiApplyJobStatus
  job_id, request_id: UUID
  sequence: monotonically increasing integer
  state: accepted | staging | launching | verifying | commit_pending
       | restoring | succeeded | failed | restored | recovery_failed
       | commit_repair_required        # idle is a read representation, not a stored state
  requested_selection                  # normalized
  resolved_plan: safe summary | null   # before resolution
  previous_selection: safe summary | null
  created_at, updated_at, deadline_at: UTC timestamps
  reason_code: nullable stable code
  message: nullable redacted diagnostic
  recovery_available: boolean
```

Safe plan summary: main/photo IDs + immutable revisions, photo mode + placement, selected topology, installed runtime version + safe image digest, context/concurrency limits, per-node memory/disk requirements **with evidence classifications**, non-fatal warnings. Never expose `.env`, full Compose output, tokens, SSH details, raw checker tracebacks, or arbitrary container logs. Public diagnostic text bounded (≤2 KiB); `log_tail` retained as a redacted summary only.

#### Legacy-client projection

The response keeps the coarse legacy enum additively: `idle|running|succeeded|failed|unavailable` projected from the new lifecycle (staging/launching/verifying/commit_pending → `running`; restored/recovery_failed → `failed`; succeeded → `succeeded`; manager unreachable → `unavailable`). Old iOS/web builds keep decoding `AiSwapStatus`; new fields are additive (ADR 0074 Decision 5). The apply **request** similarly accepts legacy bodies without `request_id` — the server synthesizes the idempotency identity from the normalized request; new clients always send one.

#### Error mapping (structured public envelope; preserve specific safe causes)

| HTTP | Stable reasons | Behavior |
|---|---|---|
| 401/403 | existing auth/authz errors | No manager mutation. |
| 409 | `swap_in_progress`, `cluster_consent_required`, `cluster_peer_unreachable`, `deployment_drift`, `previous_runtime_not_ready`, `request_id_conflict`, `recovery_required` | No household-setting or apply-audit changes. |
| 422 | `invalid_model_id`, `model_not_found`, `model_access_denied`, `runtime_incompatible`, `hardware_incompatible`, `unsupported_photo_mode`, `insufficient_memory`, `insufficient_disk`, `insufficient_evidence` | Rejected before active-deployment mutation. |
| 503 | `manager_unavailable`, `manager_upgrade_required`, `preflight_unavailable`, `journal_unavailable`, `executor_start_failed` | Preserve current configuration; retry per request identity when acceptance is ambiguous. |
| 404 | unknown retained job ID | Never substitute another job. |

### 2. Authoritative launch planning (model-manager)

New modules under `services/model-manager/src/family_cfo_model_manager/`: `contracts.py` (protocol DTOs + closed enums), `planning.py` (immutable `LaunchPlan` + resolver), `runtime_check.py` (installed-image checker adapters), `deployment.py` (Docker/Compose/peer adapter + `DeploymentSnapshot`), `journal.py`, `verification.py`, `executor.py`, `profiles.json` (reviewed declarative model/architecture policy). Keep protocol DTOs separate from immutable execution values; neither owns mutable global status.

#### Resolution algorithm (under the directory flock; overall preflight deadline 180 s)

1. **Check durable admission state** — return a matching existing request before launching anything; reject another unfinished job, unresolved recovery, or another flock owner.
2. **Inspect the actual deployment** — effective Compose service config, running image IDs, managed services, runtime arguments, cache mounts, routing, declared peer. Compare runtime-owned `.env` keys against the running deployment; unexplained drift → reject (`deployment_drift`).
3. **Resolve pinned model metadata** — repository IDs → immutable revisions; read architectures, quantization, multimodal config, weight manifest, tokenizer/chat-template metadata, required auxiliary files. Use existing authenticated Hub/cache conventions; respect `HF_HUB_OFFLINE` (complete cached metadata acceptable; missing required offline evidence rejects without touching the deployment).
4. **Normalize chat/photo placement** — validate every requested role; determine application routing and separate-service scale.
5. **Run the installed-image checker** — every distinct image/model combination.
6. **Construct candidate topologies** — `single`: one node; `cluster`: TP2-over-Ray only; `auto`: prefer a valid single-node candidate, else TP2 only when API-supplied consent is true. Reviewed `min_nodes >= 2` policy excludes the single-node candidate.
7. **Evaluate every node's memory and cache disk** (policy below).
8. **Select and freeze one valid plan** — never silently reduce context, drop photos, change quantization, or upgrade an image to force a fit. Return an actionable refusal naming the limiting resource or missing evidence.

Time is linear in weight-manifest entries plus the small bounded topology set. Download staging uses streaming transfers, never weights in process memory. No user-supplied shell command, image name, arbitrary URL, filesystem path, or extra vLLM argument is ever accepted into a plan.

#### Evidence classifications and memory policy

Every resource/capability fact carries **verified** (observed hardware/image identity, immutable revision, manifest bytes, successful config construction), **estimated** (predicted resident weights, KV demand, overhead, resulting fit), or **unknown**. Unknown required evidence is never converted to zero, "fits," or a single-node assumption.

- Use actual GPU capacity; treat unified-memory hosts (DGX Spark) as **one pool**, not RAM + VRAM.
- Active weight files come from the selected format/index — never total repository storage or MoE active-parameter counts; never read an `U8` Hub count as uncompressed parameters.
- Architecture-aware KV calculation or an explicit reviewed KV-byte budget; unknown nonstandard cache layouts fail validation.
- Conservative resident-weight conversion + overhead coefficients from reviewed architecture/quantization adapters; apply a 15% safety margin to weight residency. Overhead ≥ 4 GiB per main rank and ≥ 2 GiB per separate photo instance; profiles may raise, never silently lower.
- Reserve `max(8 GiB, 8% of node memory)` outside managed runtime allocations; total managed fractions ≤ 0.92 and the reserve must hold independently. Round assigned fractions upward to the next percentage point; reject if rounding invalidates the plan.
- Count separate-vision and main allocations per placement — never divide combined memory by two. Include any already-resident managed peer describer exactly once.
- Disk: missing candidate artifacts + 10% download overhead + reserve `max(5 GiB, 5% of that cache filesystem)` on every node requiring a local copy; incomplete cached files are not usable.
- An architecture without a supported estimator or reviewed bounded profile returns `insufficient_evidence`.
- Generic candidate settings for an uncatalogued model: **at most 8,192 context tokens and one concurrent sequence**, bounded by the model's limits; reviewed profiles may specify other values. Without this cap the KV estimate — and therefore every memory verdict — is undefined.
- These are admission estimates, not an allocator simulator — exact inference verification and recovery remain mandatory. Do not copy the Qwen deployment's vLLM version, context length, GPU fraction, or parser settings into generic defaults.

#### Installed-image compatibility check

A temporary checker container per image/model combination:

- Exact installed **immutable image ID** (not a mutable tag); no implicit pull, no runtime upgrade.
- Same relevant platform/environment and model options as the frozen launch; existing model cache; **config-only retrieval — no candidate weight allocation, no CUDA, no Docker socket, no privileged mode**; read-only root filesystem; bounded temporary storage/output; **caps of 2 CPUs and 4 GiB memory** (the checker must not contend with the serving runtime for the box's unified memory pool); dropped capabilities; fixed checker entry point (no request-supplied Python/shell); candidate revision pinned; remote model code disabled unless a reviewed local profile explicitly permits it; individual checker subprocess ceiling 60 s, clipped to the remaining preflight budget.
- Version-specific adapters must construct the installed release's effective model configuration **including the automatic Transformers-backend fallback**, and validate parser availability and quantization/hardware requirements. Initial adapters cover the installed versions represented by accepted deployment fixtures — at minimum 0.24.x and 0.32.x. A development/vendor build (e.g. the MiniMax image) needs an explicitly tested adapter/profile; semantic-version guessing must not approve it. Unsupported adapter versions → actionable refusal; never a static architecture list, never a silent runtime upgrade.

#### Cluster constraints (ADR 0071)

- API reads `cluster_enabled` and passes it to the manager as **authorization evidence** — never taken from the public request; the manager independently verifies declared peer availability, image compatibility, Ray membership, GPU count, and per-node memory/cache.
- TP2 over the declared QSFP/Ray topology only; no pipeline parallelism, no LAN discovery, no automatic enrollment. Node-exporter reachability alone is insufficient — verify actual Ray resources/ranks.
- Preserve `cluster_enabled` on every settings commit. A photo-only change re-resolves the existing main's topology instead of accidentally launching it single-node.
- Peer inspection mechanism (designed here — today the only peer channel is the node-exporter TCP probe, `ai_catalog.py:315–327`, and enrollment establishes SSH keys via `scripts/setup-cluster.sh`): the manager runs a **bounded SSH command allowlist** over the enrolled peer (host-key-verified), executing only read-only probes — Ray cluster status, per-GPU memory query, installed image ID. Output is parsed structurally; any allowlist, timeout, or parse failure rejects the cluster topology. If a required peer service cannot be safely inspected/controlled, reject that topology rather than inventing partial rollback.

### 3. Durable journal, executor, state machine

#### Journal (manager filesystem; the execution authority)

`PROJECT_DIR/.runtime-swap/v2/`: `requests/<request_id>.json` (tombstones), `jobs/<job_id>/{record.json, previous.json, target.json, diagnostics.log}`, `current.json`. Directory mode 0700, files 0600; excluded from Git and diagnostic exports. Job records hold operational data only — never financial data or credentials. Writes: temp file → fsync → atomic rename → directory fsync. A failed required journal write prevents the next deployment mutation. Retention: unresolved jobs and rollback artifacts indefinitely; terminal jobs 30 days; request-ID tombstones keep replays from restarting old operations (expired-detail replay → 409 `request_expired`). The current last-known-working snapshot is preserved regardless of job retention.

`record.json`: `schema_version: 2`, `job_id`/`request_id` UUIDs, monotonic `sequence`, `origin: api|cli`, nullable `initiator_household_id`/`initiator_user_id` (opaque), `runtime_key`, `request_hash` (normalized intent digest), `state`, `decision` (`undecided|commit|restore` — **monotonic**: a committed decision never becomes restore), timestamps, `phase_deadline_at`, elapsed, `failure_code`, `safe_message`, `previous_snapshot_hash`, `target_plan_hash`, `config_receipt` (nullable UUID). SQL holds no copy of this state machine.

#### Execution ownership

Replace the daemon-thread orchestration with a **separately supervised executor process**; `main.py` remains the HTTP facade.

- Manager acquires the existing directory descriptor, performs bounded preflight, then spawns the executor with that descriptor and the immutable job ID. The executor inherits the open file description and owns the job until acknowledgement or verified failure. Manager shutdown may leave the executor alive — explicitly supported.
- Status reads immutable journal snapshots (sequence numbers protect against stale reads).
- CLI uses the same planner/executor modules through `swap-model.sh` with the existing `swap_lock.py` supervisor; the shell supplies the package source path so host execution never imports FastAPI/Pydantic.
- No new queue service, no database credentials in the manager, no generic job framework.
- All journal mutations additionally take a short journal-file lock, released **before** waiting on deployment ownership (prevents lock-order inversion).
- Corruption handling: reject symlink/path traversal and unsupported journal schema versions; corrupt active state blocks new mutations with `runtime_state_corrupt` — never reset to `idle` automatically.

#### States and transitions

| Durable state | Meaning | Allowed next state |
|---|---|---|
| `accepted` | Validated plan + previous snapshot durable; executor work may begin. | `staging`, `failed` |
| `staging` | Downloading/verifying artifacts; old runtime not yet replaced. | `launching`, `failed` |
| `launching` | Target runtime values being installed; services being replaced. | `verifying`, `restoring` |
| `verifying` | Exact identity/routing/inference checks running. | `commit_pending`, `restoring` |
| `commit_pending` | Durable `decision=commit`; target passed verification; household transaction unacknowledged. | `succeeded` |
| `restoring` | Durable `decision=restore`; previous deployment being reinstated. | `restored`, `recovery_failed` |
| `succeeded` | Household transaction acknowledged; replacement complete. | Terminal |
| `failed` | Failure before active-deployment mutation, or failed first deployment with no previous runtime. | Terminal |
| `restored` | Replacement failed; previous runtime restored **and verified**. | Terminal |
| `recovery_failed` | Restoration or its verification failed. | `restoring` via explicit retry |
| `commit_repair_required` | Commit already occurred; forward convergence failed. | Forward repair only — never automatic rollback |

`idle` is a read response, not a stored state. Preflight rejection creates no accepted job and no public apply audit (sanitized local diagnostics only).

#### Admission ordering

1. Validate authorization/input.
2. Acquire directory flock non-blockingly.
3. Check durable unfinished jobs.
4. Resolve and validate.
5. Verify/capture baseline.
6. Persist accepted job, request mapping, snapshot, current pointer.
7. Start executor.
8. Return 202.

409 always precedes household-setting changes and apply-audit writes. Replays of accepted requests are reads and may return their original job without acquiring replacement ownership. If executor spawn fails: durably mark `failed`, release ownership, return 503 — polling still finds the same terminal job if acceptance was already delivered.

#### Restart and interruption

On manager startup and on a periodic recovery scan:

- If another process holds the directory flock, **do not kill it and do not start another executor** — read its journal and report progress or stale-owner diagnostics.
- If the flock is free and an unfinished job exists, acquire it and resume that job before admitting another.
- Never identify ownership from a stored PID; never signal a process group recovered from a stale PID record. Terminate/reap only a child handle created by the current supervisor, preserving #167's full grace-period discipline.

Recovery rules per state: `accepted`/`staging` → resume staging (no rollback needed if no deployment mutation occurred); `launching`/`verifying` with `decision=undecided` → conservatively choose restore; `restoring` → repeat restore idempotently; `commit_pending` → **never roll back** — restore/re-verify the target if necessary and await configuration acknowledgement; terminal record persisted but lock still held → admission stays 409 until ownership actually closes. HTTP disconnect or client navigation never cancels a job. CLI interruption before a commit decision stops/reaps child work and attempts restoration; after `decision=commit`, interruption only detaches the caller — it cannot request rollback.

### 4. Staging, exact readiness, restoration

#### Previous working snapshot

Add `verification.py` and an immutable `DeploymentSnapshot` in `deployment.py`. Before accepting a replacement:

- Verify the currently active managed runtime using the same identity + synthetic-probe machinery; adopt it as the initial last-known-working snapshot when upgrading an existing installation.
- If a managed runtime exists but cannot be verified, reject with `previous_runtime_not_ready` — do not label it a recoverable baseline.
- A genuinely empty first deployment may proceed with `previous=null`; its failure reports `failed` with `no_previous_runtime`, never "restored."

Snapshot contents: main/photo repository IDs + revisions + served-name policy; exact local image IDs + peer image identity; structured serve arguments, parser settings, context/concurrency/KV settings; **presence and value** of every runtime-owned environment key; service placement, scale, restart policy, required networking/Ray settings; application photo enablement, support mode, configured route; runtime service configuration hashes + compose-overlay identity; references to retained model-cache artifacts. Maintain an explicit runtime-key allowlist in one module, tested against the effective Compose inputs (existing script's model/parser/fraction/vision keys plus the revision/context/cache/topology keys required by supported profiles). Never snapshot credentials, database settings, household data, or complete application environments. If `.env` and running service settings cannot be mapped to a reproducible snapshot, reject with `deployment_drift`.

#### Staging (old runtime still active)

1. Download missing pinned artifacts to existing model caches; stage on **both** nodes when TP requires local files on both.
2. Verify manifest completeness and provided hashes.
3. Verify rollback images/checkpoints remain locally available.
4. Write the fully materialized target plan durably.

Bounds: staging 60 min; target launch + readiness 30 min; restoration + readiness 30 min. Each external operation's timeout is clipped to the remaining phase deadline. Persist phase deadlines and elapsed time — restart never grants a fresh unlimited budget. There is no reservation-expiry bound: admission is one-shot (see `Resolved design decisions`), so a prepared-but-never-started reservation cannot exist. Staging may update cache contents but must not change active runtime configuration or delete rollback artifacts.

#### Applying runtime values

- Persist `launching` **before** the first active mutation.
- Write runtime-owned `.env` keys through atomic replacement, preserving unrelated keys, file mode, and ownership. Compare runtime-key hashes before mutation; a conflicting excluded writer causes failure/restoration rather than blind overwrite.
- Build command arguments structurally; never source `.env` or concatenate user input into a shell command.
- Launch using frozen image IDs and model revisions. The manager itself is never recreated by a swap. **`api`/`worker` are NOT recreated here** — application routing flips only during post-commit finalization (see `5), so a failed candidate never touches application routing and never restarts the application twice.
- This is staged replacement, **not blue/green serving**: candidate GPU allocation may require stopping the old runtime. Remove UI promises that it keeps answering throughout.

#### Exact target verification

Poll lightweight observations every 5 s; run synthetic inference only after identity/health prerequisites pass. Required checks:

1. Expected containers/ranks exist and are running.
2. Image, revision arguments, structured options, topology, and job/config hash match the frozen plan.
3. Main `/health` succeeds.
4. Main `/v1/models` advertises the exact requested served name.
5. Photo mode matches exactly — `off`: no managed separate describer + application photo routing disabled; `main`: no separate describer, routing targets main; `separate`: exact describer identity and endpoint ready, routing targets it.
6. TP2 verifies **two expected GPU ranks on the declared nodes**, not merely node-exporter reachability.
7. Bounded synthetic probes: text completion (deterministic request, ≤32 generated tokens); tool call (one synthetic no-op tool, ≤128 generated tokens — validate the parsed function name and schema-valid arguments **without executing any household tool**); photo (one embedded synthetic PNG, ≤64 generated tokens, on the exact intended photo route).
8. Identities/restart counters unchanged across the verification sequence.

Probe timeout 30 s per request; at most 3 complete verification attempts, 5 s apart, within the phase deadline. Require a valid text answer and a parsed tool response for advisor-main admission. Additionally, **planning-time rejection**: when the reviewed profile establishes the model/architecture has no working tool protocol, refuse the advisor-main role before any launch (the probe remains the final authority for everything the profile cannot establish). A known tool-less model may serve as a photo model but is never silently accepted as advisor main. Photo smoke verifies accepted image inference and a nonempty structurally valid answer, **not visual accuracy** — never require a particular color word. Once all checks pass: persist `decision=commit`, `state=commit_pending`, and verification evidence atomically; expose the durable decision to API reconciliation. Never mark `succeeded` merely because inference passed.

#### Finalization (post-commit)

After the reconciler's SQL commit succeeds and `ack` is delivered, the manager promotes the planned runtime-owned `.env` values, recreates `api`/`worker` **using their currently deployed application images** (never pulling or upgrading them), and verifies their health plus the effective chat/photo routing against the committed plan. Failure here is concrete and forward-repairable: persist `commit_repair_required` (state stays `running`, `recovery_available` signals retry) — `POST /ai/runtime/apply/{job_id}/retry-recovery` re-runs **finalization** (env promotion + app recreation + routing/health verification), never a restore. Never silently restore the old runtime while PostgreSQL points at the new one: household settings and routing flip together, which is the point of post-commit finalization.

#### Restoration

For any failure after active mutation but before durable commit:

1. Persist `decision=restore` + the original failure cause.
2. Stop candidate-owned temporary services/processes.
3. Restore snapshot runtime keys — including removing keys absent previously.
4. Recreate the previous managed **runtime** services with frozen image IDs, revisions, placement, scale, routing (application `api`/`worker` were never touched pre-commit, so no app recreation is needed on the restore path).
5. Repeat the exact readiness + synthetic-probe sequence against the previous snapshot.
6. Persist `restored` only after verification.

**Empty baseline (first deployment)**: with `previous=null`, restoration means the explicit off/empty baseline — stop candidate-owned services, remove the runtime keys the job added, restore `vllm-vision` scale/routing to off — then report `failed`/`no_previous_runtime`. There is no snapshot to re-verify; the cleanup itself is the restore, and the matrix row "First deployment fails without baseline" exercises exactly this path.

Preserve the original candidate failure separately from restoration failure; a restore command returning zero is insufficient. If restoration fails, persist `recovery_failed`, keep previous household settings, snapshots, and diagnostics; new Apply requests receive `recovery_required`. "Retry recovery" repeats the same frozen restore plan — it never accepts a new model or implicitly upgrades images. No model-cache pruning, financial-data mutation, database rollback, or restoration of unrelated `.env` values ever occurs.

### 5. Household commit and reconciliation

#### API-owned persistence

Add a small **receipt** table — not an execution-job table — via migration `database/migrations/versions/0099_ai_runtime_apply_receipts.py` (parent to current head 0098; verify head at write time):

```text
ai_runtime_apply_receipts
  job_id: UUID primary key
  runtime_key: bounded string, not null
  plan_hash: fixed digest, not null
  initiator_household_id: nullable ID
  initiator_user_id: nullable ID
  source: api | cli
  committed_model: string, not null
  committed_at: timezone-aware timestamp, not null
```

No mutable status column, no copied manager journal. The manager records initiating IDs and the configured runtime key at acceptance; only the trusted API submits API-origin metadata; CLI-origin records carry no invented household/user identity.

#### Reconciler

Add `apps/api/src/family_cfo_api/ai_runtime_apply_reconciliation.py`, owned by the API application lifespan in `main.py` (lifespan exists at main.py:38–39) — not by a page or request:

- Run immediately at startup and every 5 s (matching the client poll cadence).
- Poll manager v2 for unresolved commit decisions and unrecorded terminal outcomes.
- Multiple API replicas are allowed; a PostgreSQL advisory lock elects the committing replica, and receipt uniqueness serializes the actual commit.
- Network calls happen outside database transactions; manager acknowledgement happens only after SQL commit. The manager stores decisions until acknowledged, so a dropped poll cannot lose work.
- **Evidence freshness**: commit only on verification evidence ≤ 30 s old; older evidence triggers manager re-verification first (a target that crashed and restarted after verification must not be committed blindly).
- **Household deleted mid-swap**: if the initiating household no longer exists at commit time, skip its upsert (nullable receipt attribution already tolerates this), still repoint matching rows, still record the receipt. Recovery never depends on those rows existing.
- **CLI-origin jobs**: the reconciler talks only to the manager v2 surface; CLI-origin jobs are resumed by the manager's startup/periodic recovery scan reading the shared `PROJECT_DIR/.runtime-swap/v2/` journal. If the manager service is down, a CLI-origin commit decision waits until it returns (documented operator behavior, not a silent failure).

#### Atomic settings transaction

For an API-origin `commit_pending` job:

1. Validate protocol, runtime key, immutable plan hash, and durable decision.
2. Begin SQL transaction.
3. If the matching receipt exists, perform no repeated writes.
4. Otherwise: upsert the initiating household's runtime config (preserving its `cluster_enabled`); repoint all configs matching the managed `provider/base_url` (preserving other households' `enabled` flags and cluster toggles; leaving unrelated providers/URLs untouched); insert the receipt; write the successful `ai_runtime.model_applied` audit referencing the job ID.
5. Commit.
6. Send the receipt/job/plan identity to manager acknowledgement.
7. Manager finalizes post-commit (promote env, recreate `api`/`worker` with their current application images, verify routing/health), then persists `succeeded` and releases ownership. Finalization failure → `commit_repair_required` with forward repair via `retry-recovery`.

Extend the existing repository operations (`upsert_ai_runtime_config`, `repoint_ai_runtime_configs`) to accept one transaction connection — do not compose independently committed helpers. The existing immediate-repoint semantics identify the required affected households; only their timing changes. CLI-origin commits repoint existing matching configurations without creating an arbitrary initiating household config, and record a system-origin operational audit per the project's system-audit convention — never a fabricated end-user actor.

#### Failures at the commit boundary

- SQL failure → remain `commit_pending`; retry with bounded backoff. Ambiguous commit outcome → read the receipt before retrying.
- SQL committed but acknowledgement lost → the receipt makes acknowledgement replay safe.
- API recreated mid-swap → manager/executor continue; startup reconciler resumes.
- No API available after target verification → target keeps serving; status reports "Verified; waiting to save household selection"; new swaps stay blocked.
- Manager restart after SQL commit → remain committed; never restore the previous runtime.
- Runtime failure **after** durable commit is an operational failure, not grounds to reverse a completed selection — recover the committed target or require a new explicit Apply. Post-commit forward-convergence failure reports `commit_repair_required` (state stays running, `recovery_available` signals retry) instead of silently restoring the old runtime while PostgreSQL points at the new one.

There is no atomic transaction across Docker and PostgreSQL. The durable decision + receipt provide explicit, restart-safe ordering — not instantaneous cross-system atomicity.

#### Concurrent configuration writes

Prevent Advanced saves from racing delayed repointing:

- Serialize runtime-config PUTs and receipt commits with the same primitive: `pg_advisory_xact_lock` on a fixed deployment key, taken by both the receipt-commit transaction and managed-config PUTs.
- While the manager reports an unfinished replacement, PUTs affecting managed runtime selection or cluster consent return 409. Detection is a manager-status query cached ≤1 s in the API process (bounded added latency in the PUT path); if manager state cannot be established, fail such writes closed with 503.
- Preserve existing non-managed-provider behavior. No apply or reconciliation path ever changes `cluster_enabled`.

#### Audit outcomes

- No apply audit or config change on admission conflict or preflight rejection.
- After durable acceptance, one idempotent `ai_runtime.model_apply_started`.
- Distinct final actions: `model_applied`, `model_apply_failed`, `model_apply_restored`, `model_apply_recovery_failed` — deduplicated by job ID + action via an internal uniqueness key.
- Undo classification: `ai_runtime.model_applied` stays IRREVERSIBLE; the new actions (`model_apply_started`, `model_apply_failed`, `model_apply_restored`, `model_apply_recovery_failed`) are registered **explicitly IRREVERSIBLE** in `undo_actions.py` — they are operational records, not user-undoable state changes, and must not fall into an UNDOABLE default.

### 6. Web and iOS behavior

#### Shared client rules

Both clients treat server job status as authoritative:

- Generate one `request_id` per user confirmation; retain it across transport-ambiguous retries — never create a new UUID and retry a possibly accepted request.
- On screen entry, fetch the current/latest job and resume observation. Poll by job ID every 5 s with at most one poll in flight; accept only matching job IDs and increasing sequence numbers; guard completions by authenticated session, presentation lifetime, and request generation.
- Destroying a view stops observation — never the server job. A transient status error shows "Progress unavailable; the operation may still be running" and retains the known job.
- Re-enable new Apply only after an allowed terminal state; refresh runtime config and live runtime status after terminal completion.

Presentation (matches the issue's required distinction of validation / loading / active / failed / restored / recovery failed):

| Server state | User-facing meaning |
|---|---|
| Preflight request in flight | "Checking model and deployment compatibility…" |
| `accepted`/`staging` | Preparing/downloading; current deployment not yet replaced. |
| `launching`/`verifying` | Replacing/validating; advisor may temporarily use deterministic fallback. |
| `commit_pending` | Verified target; waiting to save household selection. |
| `succeeded` | Requested chat/photo configuration active and selection saved. |
| `failed` | Replacement failed before mutation, or no previous runtime existed. |
| `restoring` | Replacement failed; restoring previous runtime. |
| `restored` | Replacement failed; previous configuration verified and running. |
| `recovery_failed` | Runtime recovery needs attention; show Retry recovery + safe cause. |

Never infer success from main-model equality alone — this replaces the incomplete current web success condition (`ai-runtime.ts:566–590`). Fit badges remain estimates, labeled as such, and must not prevent requesting authoritative preflight solely because a name-derived client estimate says "too big." Permission, in-flight state, missing required input, and known server rejection may disable Apply.

#### Angular (`ai-runtime.ts` + `.html` + `.scss` + `.spec.ts`)

- Keep `planFor()` as **placement preview only**; convert `applyModel()`, `applyAsVision()`, `applyVisionModel()` into one request/observation lane; send `topology=auto` from ordinary selections (existing cluster placement indicators stay advisory).
- Preserve photo-off/main/separate intent explicitly. Replace `lastAppliedMain` and serving-based `applyLive` success inference with job ID + sequence + state.
- Replace overlapping `setInterval` requests with a completion-scheduled polling loop; catch thrown network failures as well as generated-client error results; apply generation/session ownership to start, poll, refresh, and recovery requests.
- Preserve all #168 browse/search state — Apply never resets search. Disable Advanced writes while a replacement is unfinished.
- Render failures with `role=alert`, progress with `role=status`; use text as well as color. Remove "the current model keeps answering meanwhile." Add Vietnamese/Lithuanian entries for all new strings (`apps/web/src/locale/`).

#### iOS (`AIRuntime`)

Keep `AIRuntimeAPI: Sendable` and the `@MainActor @Observable` view model (`AIRuntimeAPI.swift`, `AIRuntimeViewModel.swift`). Interface changes:

```text
apply(request: AiApplyRequest) async throws -> AiApplyJobStatus
applyStatus(jobID: UUID?) async throws -> AiApplyJobStatus
retryRecovery(jobID: UUID, requestID: UUID) async throws -> AiApplyJobStatus
```

- Update `LiveAIRuntimeAPI`, its mock, and both apply call sites (list + detail).
- Build role intent correctly: vision-only model pairs with the current main; dual-capable main uses `photo=main`; text-only main preserves the explicit current photo mode.
- Use `servedModel`, not configured `status.model`, for "answering now."
- Move observation into a view-model-owned cancellable task, independent of the initiating button task; attach on presentation, detach on dismissal, resume via current-job lookup on return. Treat `CancellationError` as observation cancellation, not replacement failure; keep stale-session completions from updating the new session.
- Add explicit recovery presentation/action; change "Tight fit — it will load" to estimated language; update confirmation copy to explain verification and possible interruption without promising duration or uninterrupted service.

No financial calculations move into either client; regenerate both clients from OpenAPI — never hand-edit `apps/web/src/app/api-client/` or `apps/ios/FamilyCFO/FamilyCFOShared/APIClient/Generated/`.

### 7. Security and operational boundaries

- API remains socket-free; manager alone controls Docker; no public manager port, no new cloud service. Only existing Hub metadata/model downloads leave the box; synthetic probes contain no household data.
- Keep financial determinism unchanged: synthetic tool tests invoke no financial tool, and LLM output never becomes a sole calculator or financial ground truth. **Advisor-tool access: N/A — this infrastructure issue adds no family-visible data domain and no M16 tool.**
- Fixed runtime IDs, service allowlists, declared peer targets, structured arguments, strict repository IDs, and pinned revisions prevent arbitrary execution/SSRF expansion. Credential files and tokens stay in existing credential channels; never copy an entire HF cache credential store to a peer. All v2 mutators (`POST /v2/jobs`, `decision`, `ack`, `recover`) require the **deployment-scoped internal credential** — an env-provided shared secret held by the API and the local CLI, never stored in job state, journals, or the public status.
- Capture subprocess output through bounded streaming files rather than unbounded `communicate()` buffers. Strip ANSI/control sequences and credential-bearing values; expose at most a bounded safe diagnostic (public ≤2 KiB), not raw logs. Private diagnostics rotate at 1 MiB; journal records retain structured reasons and hashes rather than full output.
- Checker containers and staging helpers carry job labels and are cleaned up by exact recorded container IDs — never clean unrelated containers, caches, or peer services. Test all fixtures with synthetic identities and secrets.
- Operator visibility: the manager's existing `/metrics` gains an apply-state gauge (`family_cfo_apply_state{state,origin}` plus a blocked flag) so `recovery_required`/`commit_repair_required`/held-lock conditions are observable without a dashboard open.

## Spec Kit documentation, in order

Update and obtain acceptance in this order before any production code (per `docs/specs/README.md` and AGENTS.md):

| Order | File | Change |
|---|---|---|
| 1 | `docs/specs/01-prd.md` | Add the safe replacement/recovery goal, availability limits, and scope. |
| 2 | `docs/adr/0081-ai-runtime-apply-transactions.md` **new** | Record ownership, protocol promotion (major 2 + `BUILD`), decision/receipt ordering, lock-extension through the full lifecycle, rollback boundaries, ordered migration. |
| 2 | `docs/adr/0013-model-manager-sidecar.md` | Replace the obsolete "no durable job state or job recovery" and "no automatic rollback" statements; retain socket and unrelated-writer boundaries. Depends on the new ADR. |
| 2 | `docs/adr/0071-two-spark-cluster.md` | Clarify authoritative topology validation and the distinction between peer detection and rank readiness; preserve the toggle/TP invariants. |
| 2 | `docs/adr/0074-per-component-build-numbers.md` | Record the manager promotion and the explicit protocol-v1 transition exception. |
| 3 | `docs/specs/02-adrs.md` | Index the accepted ADR. |
| 4 | `docs/specs/03-domain-model.md` | Define the operational job, frozen plan/snapshot, commit decision, receipt; non-financial scope. |
| 5 | `docs/specs/04-openapi.md` | Specify request/status/recovery shapes, error reasons, read-only GET semantics, the legacy additive projection for shipped clients, contract release. |
| 6 | `docs/specs/05-database-schema.md` | Specify the receipt table + audit-deduplication constraint, migration, downgrade restrictions. |
| 7 | `docs/specs/06-security-model.md` | Privileged execution, peer control, checker confinement, journal access, redaction. |
| 8 | `docs/specs/07-ai-orchestration.md` | Exact chat/photo verification, bounded synthetic probes, unchanged financial determinism; advisor access N/A. |
| 9 | `docs/specs/08-mobile-spec.md` | iOS request construction, lifecycle, progress/recovery, generated-client dependency. |
| 10 | `docs/specs/09-angular-dashboard-spec.md` | Add #170 behavior without changing #168 search rules. |
| 11 | `docs/specs/10-docker-spec.md` | Durable journal mount, immutable runtime artifacts, sidecar protocol/version, peer checks, restart behavior, deployment ordering. |
| 12 | `docs/specs/11-milestone-roadmap.md` | Supersede the M23 no-recovery non-goal for #170 and add the acceptance gate. |
| 13 | `docs/specs/12-implementation-tasks.md` | Ordered, testable work items matching the implementation order below. |
| 14 | `docs/specs/README.md` | Append `## Issue #170` gate: scope, non-goals (including: no user-initiated cancel of an in-flight job this issue — recourse is interruption/recovery; legacy-client additive projection), API/schema/security/tests/docs, explicit advisor N/A. |

Supporting docs: update this plan's open-questions section to the accepted decisions (done in this revision); `docs/guides/ai-advisor.md` gains preflight refusals, recovery states, retry, CLI behavior, and the no-uninterrupted-serving guarantee; add `docs/investigations/ai-runtime-170-deployment-validation.md` to record the deployment-specific regression evidence (no secrets).

## File-by-file impact

### Manager and CLI

| File | Change |
|---|---|
| `services/model-manager/src/family_cfo_model_manager/main.py` | Replace `_status` authority + thread orchestration with v2 routes, journal reads, bounded preflight, executor admission, acknowledgement, startup recovery. Keep logs/metrics. |
| `…/contracts.py` **new** | Protocol DTOs/enums; precedes route/client work. |
| `…/planning.py` **new** | Immutable intent/plan/evidence values; authoritative topology + resource resolution. |
| `…/runtime_check.py` **new** | Installed-image adapters + bounded checker launch. |
| `…/profiles.json` **new** | Reviewed model/architecture policy; replaces authoritative shell family heuristics. |
| `…/deployment.py` **new** | Structured Docker/Compose/peer inspection, staging, atomic runtime-key updates, launch + snapshot restoration. |
| `…/journal.py` **new** | Durable records, request mapping/tombstones, atomic persistence, sequencing, retention. |
| `…/verification.py` **new** | Exact identity/routing/rank checks + synthetic probes. |
| `…/executor.py` **new** | Durable state machine, deadlines, cleanup, recovery, commit wait. |
| `…/cli.py` **new** | CLI intent parsing/output; invokes the shared planner/executor. |
| `scripts/swap-model.sh` | Retain argument compatibility + lock entry; translate positional intent, then delegate to the shared Python engine. Remove duplicate parser/memory/topology/mutation logic. |
| `scripts/lib/swap_lock.py` | Preserve descriptor validation + cleanup behavior; adjust supervision only for durable cleanup/detach semantics. |
| `services/model-manager/BUILD` **new** | Manager build identity under the promotion ADR. |
| Manager Dockerfile + `docker-compose.yml` / `docker/vllm-cluster/` overlays (validate exact filenames) | Package new modules/checker resources; wire durable journal + revision/structured runtime settings; retain internal-only manager access; prevent manager recreation during apply. |
| `.gitignore` | Exclude operational journal/staging files (`.runtime-swap/`). |

### API, persistence, contracts

| File | Change |
|---|---|
| `apps/api/src/family_cfo_api/api/ai_runtime.py` | Replace the `/swap` call + immediate repointing with v2 admission/status/recovery; gate managed config writes. |
| `apps/api/src/family_cfo_api/ai_runtime_apply_reconciliation.py` **new** | Lifespan-owned discovery, idempotent commits/outcome audits, acknowledgements, retries. |
| `apps/api/src/family_cfo_api/model_manager_client.py` **new** | One typed manager-v2 transport: protocol verification, bounded timeouts, safe error conversion, **no legacy mutation fallback**. |
| `apps/api/src/family_cfo_api/main.py` | Start/stop the reconciler task with the application lifespan (hook exists at main.py:38–39). |
| `apps/api/src/family_cfo_api/repository.py` | Add receipt/outcome-dedupe access + one transaction-scoped household commit helper; extend existing config helpers rather than duplicating them. |
| `apps/api/src/family_cfo_api/models.py` | Declare the receipt table + audit idempotency constraint. |
| Schema module defining `AiSwapStatus` (`apps/api/src/family_cfo_api/schemas.py`:1662–1667 — verify at edit time) | Replace apply DTOs with the OpenAPI-defined request/status union; retain unrelated runtime DTOs. |
| `apps/api/src/family_cfo_api/config.py` | Add the dedicated API→manager budget (~190 s), the v2 internal credential setting, and reconciler cadence; no household-configured execution limits. |
| `apps/api/src/family_cfo_api/undo_actions.py` | Register the new operational audit actions; successful apply remains irreversible. |
| `database/migrations/versions/0099_ai_runtime_apply_receipts.py` **new** | Receipt + audit-deduplication schema; parent to actual current head (0098 — verify). |
| `shared/openapi/family-cfo.v1.yaml` | Authoritative public request/status/recovery contract; precedes generation. |
| `shared/schemas/model-manager-v2-contract.json` **new** | Freeze v2 request/status/decision/acknowledgement fixtures. Never edit the v1 fixture. |
| `apps/web/src/app/api-client/` + `apps/ios/…/APIClient/Generated/` | Regenerate TypeScript + Swift clients. |
| `VERSION` + component `BUILD` files | Coordinated contract release under the accepted ADR. |
| `scripts/check-client-compatibility.sh` + manager compatibility tests | Include the promoted sidecar + v2 fixtures; validate exact existing stamping/patch entry points before editing. |

### Clients and tests

| File | Change |
|---|---|
| `apps/web/src/app/core/api.service.ts` | Update apply/status signatures; add recovery operation wrappers. |
| `apps/web/src/app/pages/ai-runtime/ai-runtime.ts` / `.html` / `.scss` / `.spec.ts` | Shared apply lane, UUID identity, server states, sequence/session guards, single-flight polling, resume/recovery, preview-only topology; accessible recovery/commit-wait UI; replace main-ID success assumptions in tests; retain #168 coverage. |
| `apps/web/src/locale/` catalogs | Extract/update every changed string (vi/lt). |
| `apps/ios/…/AIRuntime/AIRuntimeAPI.swift` / `AIRuntimeViewModel.swift` / `AIRuntimeView.swift` / `AIRuntimeModelDetailView.swift` | New generated request/status/recovery calls + structured errors; role-aware intent; owned observation task; recovery action; truthful fit/confirmation copy. |
| `apps/ios/…/FamilyCFOTests/AIRuntimeDecodeTests.swift` / `AIRuntimeViewModelTests.swift` | Every new enum/nullable combination; deterministic polling/race injection. |
| `services/model-manager/tests/` | New: `test_planning.py`, `test_runtime_check.py`, `test_journal.py`, `test_executor.py`, `test_verification.py`. Update `test_manager.py`, `test_compatibility.py` (protocol-2 contract + deliberate legacy mutation refusal), `test_swap_lock.py` (preserve real-flock/orphan/reap tests across new executor seams). |
| `apps/api/tests/` | Update `test_ai_apply_api.py` (replace immediate-update expectations; assert rejection purity, accepted-no-config-write, commit-once), `test_ai_runtime_api.py` (PUT exclusion + toggle preservation), `test_ai_runtime_status.py` (serving observation vs durable status; GET purity). New: `test_ai_apply_reconciliation.py` (PostgreSQL decision races, ambiguity, restart, cross-household). |
| `scripts/tests/` | `test_swap_model.py` + `test_cli_lock_helper.py`: CLI unified workflow, argument compatibility, descriptor invariants. |

No changes to model-search ranking/filtering, financial calculations, advisor tool definitions, or the historical investigations.

## Implementation order

1. **Documentation-only gate** — Spec Kit updates in the order above; accept ADR 0081; record #170 acceptance criteria. No production code changes.
2. **Freeze contracts + schema design** — author public OpenAPI, v2 manager fixture, state-transition fixtures, migration tests; resolve the physical locations flagged above.
3. **Build journal + immutable planning foundations** — contracts, atomic journal, request idempotency, snapshot/profile types, pure resolver tests. No active runtime mutation.
4. **Add installed-image/hardware verification adapters** — config-only checks against pinned fixture images + mocked hardware; prove the old runtime is untouched on refusal.
5. **Add deployment staging + exact verification** — cache staging, structured execution, identity checks, bounded synthetic probes, snapshot restoration behind disabled mutation routes.
6. **Implement executor + restart recovery** — crash points, lock inheritance, interruption, orphan exclusion, deadlines, terminal-state persistence.
7. **Unify CLI + manager execution** — replace shell authority with the shared engine; preserve #167 locking tests. Land the v2 route/executor/CLI transition atomically.
8. **Add SQL receipt transaction + API reconciliation** — migration, transaction-scoped repoint/audit, lifespan task, acknowledgement retry, managed-config write exclusion.
9. **Activate the new API contract internally** — remove immediate repointing + legacy fallback. Backend DTOs, authoritative OpenAPI, generated clients, contract/build updates, compatibility fixtures land together (coordinated release).
10. **Update web + iOS** — role intent, request identity, resumable observation, exact terminal states, recovery, localization, stale-completion protection.
11. **Run integration + deployment gates** — the verification matrix below, then the ordered rollout and real two-Spark evidence.
12. **Enable Apply** — only after protocol-v2, baseline snapshot, SQL reconciliation, and recovery drills pass.

## Verification matrix

| Scenario | Required result |
|---|---|
| Catalog text main + separate photo | One frozen plan; both exact models pass probes; matching households update only after verification. |
| Dual-capable main | One main instance, no separate describer, correct main photo route. |
| Explicit photos off | No implicit default photo model; service scale and application routing both off. |
| Duplicate main/photo ID | Normalizes to one dual-capable instance or rejects capability mismatch. |
| Vision-only selection without current chat | Rejected without launch. |
| Uncatalogued model without parameter count | Uses metadata/profile evidence; never assumes single-node fit from a missing estimate. |
| Uncatalogued model with adequate metadata | Same resolver/checker/budget/probe path as catalog models. |
| Unsupported architecture on installed 0.24.x | 422 before active `.env` changes or service recreation; installed version + safe cause shown. |
| Supported Transformers-backend fallback | Accepted when faithful installed-image config construction passes; no registry-only false rejection. |
| Unsupported quantization/GPU/parser/remote-code requirement | Preflight rejection; current deployment unchanged; no implicit upgrade or trust escalation. |
| Unknown estimator/runtime adapter | `insufficient_evidence` or a compatible safe refusal — never a guessed fit. |
| Hub unavailable, complete offline metadata/cache | Validation + apply succeed from cache and pinned artifacts. |
| Hub unavailable, required evidence missing | Fail before deployment mutation; explain retry/cache requirement. |
| Gated model without access | Safe access error; no token/log leakage. |
| Model needs TP2, toggle off | Refuse; never enable the toggle. |
| Single-node invalid, cluster consent on, two-node plan valid | Server resolves TP2 regardless of any missing client estimate. |
| Model needs TP2, peer unavailable | Refuse before mutation. |
| Node exporter reachable, Ray/rank/image invalid | Refuse topology; reachability alone is insufficient. |
| Combined memory fits, one node does not | Refuse; include separate vision placement + replicated costs. |
| Photo-only change on cluster main | Preserve/revalidate the main's TP topology. |
| CLI owns flock, manager journal says idle | Manager returns 409; no config/audit mutation. |
| Dashboard owns flock, CLI starts | CLI refuses; no overlapping work. |
| Manager restarts, orphan holds descriptor | No second executor; status reads the durable journal. |
| Same request repeated or response dropped | Same job returned; exactly one launch. |
| Same request ID, different payload | 409; no second operation. |
| Staging disk/network failure | Old runtime unchanged; no household commit. |
| Crash after `launching`, before env replacement | Recovery restores/verifies the snapshot safely. |
| Crash after env replacement or Compose return | Recover the previous deployment; process exit is never success. |
| Main ready, wrong model/revision/image | Verification fails and restores. |
| Main ready, requested photo missing/wrong | Verification fails and restores. |
| Tool/image inference hangs | Request and attempt bounds enforced; restore starts within deadline. |
| Photo accepted but description inaccurate | Mechanical probe records transport success without claiming accuracy. |
| Container restarts during probes | Discard the attempt; no commit from mixed identities. |
| Candidate fails, previous verifies | `restored`; settings still previous; original cause retained. |
| Restore command succeeds, previous inference fails | `recovery_failed`, not `restored`. |
| Recovery retry interrupted/replayed | Same recovery attempt identity; no overlap; snapshot retained. |
| First deployment fails without baseline | `failed`/`no_previous_runtime`; no false restoration claim. |
| SQL fails before commit | `commit_pending`; no partial household repoint. |
| SQL commit result ambiguous | Reload the receipt; no duplicate audit/repoint. |
| SQL committed, acknowledgement lost | Replay acknowledgement; never roll back. |
| Manager crashes after commit decision | Recover the target, not the previous runtime. |
| API/worker recreated during swap | Executor survives; API startup reconciliation completes later. |
| No UI polls the job | Reconciliation + recovery still finish. |
| Two API replicas reconcile | One receipt, one settings transaction, one final audit. |
| Advanced PUT races Apply | Serialized exclusion; no stale full-object overwrite or toggle change. |
| Polls complete out of order / session changes | Older sequence/session cannot replace current state. |
| New API + old manager; old API + new manager | Mutation unavailable and safe; no legacy fallback/early repoint. |
| Journal write/full-disk failure | No subsequent unjournaled mutation; clear degraded state + retained evidence. |
| Journal corruption / path traversal / unsupported schema | Fail closed with `runtime_state_corrupt`; no automatic reset to idle; operator-visible. |
| Redaction/security tests | No secrets, raw `.env`, financial prompts, arbitrary services, or injected commands exposed. |

### Two-Spark deployment-specific regression gate

For `nvidia/Qwen3.8-Flash-Next-NVFP4`, use an explicitly reviewed deployment profile matching the verified hardware/runtime combination from the issue evidence — never generic defaults. Record: installed vLLM/image identities on both Sparks; resolved immutable checkpoint revision; TP2/Ray placement + per-node admission estimates; explicit reviewed context/GPU settings; a parsed synthetic tool call with schema-valid arguments; a synthetic photo request through the intended application route; household settings unchanged until the commit decision and updated exactly once afterward; a deliberately injected startup failure followed by verified restoration of the previously working runtime; manager/API restart drills on both sides of the commit boundary. Passing text/tool/photo transport smoke establishes operational readiness for this profile; it does not certify general visual accuracy or permit LLM-only financial calculation.

## Risks, migration, tradeoffs

### Migration (ordered rollout)

1. Accept the promotion ADR + complete the Spec Kit gate first.
2. Disable Apply operationally; drain any existing v1 swap; confirm the deployment flock is free.
3. Create the receipt/audit-deduplication schema (no backfill — historical applies get no fabricated receipt).
4. Deploy manager v2, then API/reconciler. During skew, mutation fails closed.
5. Bootstrap the previous working snapshot through inspection + bounded verification; never infer success from old `_status`.
6. Deploy generated-client-backed web/iOS under the new contract.
7. Enable Apply only after protocol, baseline, and deployment smoke gates pass.

Existing `ai_runtime_configs` data remains unchanged until a new verified commit.

### Rollback restrictions

- Drain/resolve every unfinished v2 job before reverting the manager/API; never downgrade a journal while a job is pending; do not rewrite journal schema 2 as schema 1.
- API rollback must not re-enable legacy `/swap` against manager v2.
- Receipt-table downgrade only after jobs are drained and the older application no longer references it; retain operational records normally.
- Keep old images/checkpoints until the rollout is accepted. Restoring runtime settings does not restore deleted external artifacts, unavailable peer hardware, or a failed disk.

### Principal tradeoffs

- **Conservative refusal**: unfamiliar architectures may be rejected despite possibly fitting — preferable to treating missing evidence as permission to destroy a working deployment. The remedy is a verified profile, not a "skip validation" switch.
- **Long exclusion**: the full-transaction flock blocks another Apply during loading and pending commit. Overlap would invalidate snapshots and recovery.
- **Availability, not atomicity**: clients may briefly observe a serving target while SQL still names the previous model; status explicitly reports `commit_pending` and reconciliation closes the gap.
- **Recovery is bounded**: disk failure, unavailable peer, or image corruption can defeat restoration — `recovery_failed` must remain distinct from verified restoration.
- **Versioned adapters require maintenance**: installed-vLLM APIs change; unsupported adapters fail closed instead of guessing.
- **No blue/green guarantee**: memory-constrained replacement can interrupt AI serving; deterministic fallback remains available.
- **Not a visual-quality guarantee**: generic image-inference readiness cannot establish OCR/color/financial-extraction accuracy.

## Reconciliation notes

Three independent design passes (two completed Oracle lanes at high effort, one at medium effort; a fourth lane failed on a provider limit) plus curated code exploration were reconciled into this plan:

- **Agreed core (accepted from all lanes)**: manager-owned launch planning; installed-image config checker instead of architecture allowlists; durable journal + snapshot-based verified restoration; commit only after exact readiness; idempotent reconciliation; distinct recovery-failure state; read-only status; server-authoritative clients; conservative memory policy with unknown-evidence refusal; Spec Kit order first; advisor access N/A.
- **SQL schema scope**: two lanes proposed full job tables in PostgreSQL; one proposed a receipt-only table. **Accepted: receipt-only** — the manager journal already persists execution state across manager restarts, so SQL job rows would duplicate a state machine and risk divergence. The receipt still guarantees exactly-once settings commits and records the irreversible decision.
- **Apply request shape**: one lane proposed a breaking discriminated-union request (`photo: off|main|separate(model)`); two kept `main_model`/`vision_model` with additive `photo_mode`/`topology`. **Accepted: additive** with a deprecated `cluster` alias — smaller client churn, explicit server semantics, breaking only where ambiguity existed.
- **CLI plumbing**: one lane kept the CLI host-side on shared engine modules; two made the CLI a thin client of the API workflow. **Accepted: shared engine modules host-side** (rationale in the decisions table).
- **Bounds**: reconciled to the conservative union (preflight 180 s; checker subprocess ≤60 s; staging 60 min; launch+readiness 30 min; restore+verify 30 min; probes ≤32/128/64 tokens, 30 s each, ≤3 attempts; poll 5 s; reconciler 5 s). Rejected lane figures: lane 3's tighter 120 s whole-reservation / 30 s-per-operation budgets (the only ones paired with a client-timeout story — superseded by the explicit transport budget chain in `Detailed design `1); the 10-minute reservation expiry (vestigial after choosing one-shot admission); lane 3's 2-probe attempt cap (kept 3).
- **State names**: consolidated to one set (``Detailed design `3`) covering every state any lane named, including `commit_repair_required` for post-commit forward-repair failure and `no_previous_runtime` as a failure detail of `failed`.
- **ADR name**: lanes proposed three different 0081 slugs; this plan uses `0081-ai-runtime-apply-transactions.md`.

## Post-critique decisions (2026-11-20)

The design critique (`docs/reviews/ai-runtime-apply-validation-plan-critique-2026-11-20.md`, 24 findings) was applied in full. Decisions it forced, beyond the reconciliation above: one-shot admission (the reserve-expiry bound is deleted); an explicit transport budget chain with a dedicated API→manager setting (the current `_HF_TIMEOUT_SECONDS = 6.0` cannot carry a 180 s preflight); v2 mutators authenticated by a deployment-scoped credential; a legacy additive status projection plus server-synthesized `request_id` so shipped iOS keeps working; post-commit `api`/`worker` finalization making `commit_repair_required` concrete (and `retry-recovery` direction-aware); an SSH command-allowlist as the designed peer-inspection channel; planning-time tool-less-main rejection; 30 s commit-evidence freshness; journal corruption fail-closed; specified empty-baseline restore; household-deletion tolerance at commit; new audit actions registered IRREVERSIBLE; an apply-state operator metric; cancellation recorded as an explicit non-goal.

## References

- Issue: https://github.com/bobo-83/Family-CFO/issues/170 · #167 (lock, PR #178) · #168 (search relevance)
- ADRs: `docs/adr/0013-model-manager-sidecar.md` · `docs/adr/0071-two-spark-cluster.md` · `docs/adr/0074-per-component-build-numbers.md`
- Spec gate examples: `docs/specs/11-milestone-roadmap.md`:1287–1331 (M23) · `docs/specs/09-angular-dashboard-spec.md`:230 (#168)
- vLLM: [registry.py @ v0.32.0](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/model_executor/models/registry.py) · [supported models](https://docs.vllm.ai/en/latest/models/supported_models.html) · [parallelism](https://docs.vllm.ai/en/latest/serving/parallelism_scaling.html) · [v0.32.0 release notes](https://github.com/vllm-project/vllm/releases/tag/v0.32.0) · [Hub API](https://huggingface.co/docs/hub/api#get-apimodelsid) · [checkpoint requirements](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4#usage)
- Prior plans: `docs/plans/ai-runtime-model-search-2026-11-19.md` · investigations `minimax-m31-runtime-upgrade-2026-11-08.md`, `qwen35-2b-peer-vision-2026-11-08.md`





## Execution log (orchestrated implementation, 2026-11-21)

Branch: `fix/170-ai-runtime-apply-validation-recovery` (stacked on `fix/168-ai-runtime-model-search`).

- [x] Item 1: Spec Kit documentation gate + ADR 0081 (docs-only, must land first)
- [x] Item 2: Contracts + migration 0099 + manager journal/planning foundations (contracts.py, journal.py, planning.py, profiles.json, receipt table, OpenAPI freeze)
- [x] Item 3: Manager execution — checker, deployment, verification, executor, v2 routes, CLI unification
- [x] Item 4: API layer + contract activation — reconciler, model_manager_client.py, generated clients, VERSION/BUILD, compatibility fixtures (commit 0b5ffbe1: apps/api 1340 passed on PG 17, manager 211, web 482/482, check-openapi/versions/fixtures OK)
- [ ] Item 5: Web + iOS presentation + full verification matrix (runnable portions)
- [ ] PR opened against main
