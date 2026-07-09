# ADR 0011: Chat image support via describe-then-ground vision routing (M21)

## Status

Accepted.

## Context

Users want to attach a photo (or take one) in chat — a price tag, a bill, a
statement — and ask "can I afford this?". The main chat model
(Qwen2.5-32B-Instruct by default) is text-only; vision-capable variants exist
(Qwen2.5-VL). The image must be understood somewhere, and every number the
advisor states must remain traceable (ADR 0003/0009's grounding guardrail).

## Decision

### 1. Describe-then-ground, always

An attached image is **always converted to a text description first**, and only
the description enters the chat pipeline:

1. If the deployment marks the main model vision-capable
   (`FAMILY_CFO_AI_SUPPORTS_VISION=true`), the **main model** produces the
   description (one multimodal completion, no tools).
2. Otherwise, if a **vision describer** is configured
   (`FAMILY_CFO_AI_VISION_*`, a second small vLLM model, e.g.
   Qwen2.5-VL-7B-Instruct), it produces the description.
3. Otherwise the chat proceeds without the image and says so (a response
   warning) — never a silent drop.

The description is appended to the user's message for the existing text-only
tool-calling loop, and the description's numbers are added to the guardrail's
grounded set — they trace to a real source (the photo, via a deterministic
pipeline step), unlike numbers a model invents mid-answer.

Rejected alternative: passing the image directly into the tool-calling loop as
multimodal content. Two reasons: (a) the grounding guardrail cannot know which
numbers are legitimately "in the image", so either it blocks every price the
model reads off the photo or it must be disabled around images — both wrong;
(b) multimodal + tool-calling in one request is the least-supported corner of
current runtimes. Describe-then-ground keeps one battle-tested text loop and a
sound guardrail. Direct multimodal tool-calling can be revisited later.

### 2. The describer is a second on-box vLLM service

`vllm-vision` runs alongside the main runtime with explicit GPU memory
fractions (`--gpu-memory-utilization`; defaults 0.60 main / 0.20 vision) so the
two share one GPU. Same privacy posture as ADR 0008: the photo never leaves the
box, and it is processed in memory only — not persisted to disk or the
database; the conversation stores the *description*, marked as coming from an
attached photo.

### 3. On-device (iPhone) describing is out of scope for the web app

Safari cannot expose Apple's on-device Vision/Foundation Models to a web page.
"The iPhone describes the photo and sends text" is recorded as the preferred
design **for the native iOS app** (see `docs/specs/08-mobile-spec.md` backlog);
the web dashboard uses the server-side describer above.

## Consequences

- New env knobs: `FAMILY_CFO_AI_SUPPORTS_VISION`, `FAMILY_CFO_AI_VISION_ENABLED/
  BASE_URL/MODEL`, and GPU fractions `VLLM_GPU_FRACTION`/`VLLM_VISION_GPU_FRACTION`.
- `ChatRequest` gains optional `image_base64` + `image_media_type` (additive).
  The client downscales/re-encodes to JPEG before sending; the server enforces
  the existing upload cap and a media-type allowlist.
- Applying the GPU fraction to the existing main runtime requires one vLLM
  restart (model reloads from the local cache; no re-download).
- Two models on one GPU trade some main-model KV-cache headroom for vision.
  Deployments without the GPU headroom set `FAMILY_CFO_AI_VISION_ENABLED=false`
  and scale `vllm-vision=0` — images then get the graceful no-analysis warning.
