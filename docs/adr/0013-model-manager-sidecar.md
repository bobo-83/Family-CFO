# ADR 0013: One-click model apply via a model-manager sidecar; HF-sourced catalog (M23)

## Status

Accepted. Partially supersedes ADR 0012 (decision 1: command-only apply).

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

## Consequences

- New service (`services/model-manager`), compose entry with docker socket +
  project mount; a fresh deploy is required to add it (not just an app patch).
- `POST /ai/runtime/apply` + `GET /ai/runtime/apply/status` +
  `GET /ai/models/search` join the contract.
- HF estimates are approximate; hand-checked numbers only exist for curated
  models. The fit verdict for arbitrary HF models is an estimate and labeled so.
