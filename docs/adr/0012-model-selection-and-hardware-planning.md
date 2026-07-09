# ADR 0012: In-app model selection with hardware planning (M22)

## Status

Accepted.

## Context

Operators should pick their main chat model — and, when it isn't
vision-capable, a vision describer — from a curated list in the dashboard, with
live feedback on whether the selection fits their hardware. A selection
*replaces* the current models (vLLM serves one model per instance), it does not
add to them.

## Decisions

### 1. The API never controls Docker; the UI generates the apply command

Actually swapping a model requires restarting the vLLM container(s) with a new
`--model`. Giving the API that power means mounting the Docker socket into the
`api` container — root-equivalent host access from the most exposed service.
Rejected. Instead:

- The dashboard saves the **desired** main model via the existing
  `PUT /ai/runtime` (household config), and shows the exact operator command —
  `scripts/swap-model.sh <main> [vision|none]` — which updates `.env` and
  recreates the runtime containers.
- The status endpoint exposes enough (`served_model` vs configured `model`) for
  the UI to show a "selected but not yet serving" mismatch state.

A privileged "model-manager" sidecar could make this one-click later; that
needs its own ADR.

### 2. A curated, versioned model catalog lives in the backend

`GET /ai/models` returns a static catalog (id, label, parameters, est. memory
GB, est. disk GB, tool parser, `supports_vision`, role main/vision/both,
gated). Backend-owned so iOS and web share it and estimates are updated in one
place. These are planning estimates for bf16 serving, not measurements.

### 3. Hardware profile is best-effort and honest about unified memory

`GET /ai/hardware` reports what the API container can actually know: system
memory (`/proc/meminfo`), free disk (`shutil.disk_usage`), and GPU memory only
if the operator/deploy script provides it (`FAMILY_CFO_GPU_MEMORY_GB`) —
`nvidia-smi` is not available in the api container, and on unified-memory
systems (DGX Spark/GB10) it reports N/A anyway. When GPU memory is unknown the
UI uses system memory as the budget with an explicit "unified/unknown" note.

### 4. Fit math treats the selection as a replacement

Required memory = selected main + selected vision (if any) + ~15% KV/runtime
headroom, compared against the budget — never "current + new". Disk shows the
new weights' download size against free space (previously downloaded models
remain in the cache; pruning is out of scope).

## Consequences

- Two new read-only endpoints (`/ai/models`, `/ai/hardware`) + additive
  `AiRuntimeStatus.vision_enabled`; contract + clients regenerated.
- `scripts/swap-model.sh` becomes the single supported way to change served
  models; it also keeps `VLLM_TOOL_PARSER`/vision env vars consistent.
- Catalog estimates need occasional manual upkeep as models change.
