# Testing the AI advisor (agentic tool-calling)

This runbook brings up a local vLLM runtime and exercises the M16 agentic
advisor (`POST /api/v1/chat/messages`) end-to-end. Without a runtime the chat
endpoint still works — it returns a deterministic net-worth/emergency-fund
snapshot — so these steps are only needed to see the model orchestrate the
financial engine and answer open-ended questions.

See [ADR 0009](../adr/0009-agentic-tool-calling.md) for the design and
[`apps/api/README.md`](../../apps/api/README.md) (M16 Scope) for the runtime
behaviour.

## Prerequisites

- An NVIDIA GPU on the host plus the **NVIDIA Container Toolkit** (`nvidia-ctk`);
  the `vllm` service passes the GPU through via `deploy.resources.reservations`.
- Enough VRAM/unified memory for the model (the default `Qwen2.5-32B-Instruct`
  needs ~65 GB in bf16; use a smaller model or a 4-bit quant otherwise).
- A populated `.env` (copy from `.env.example`). The AI-runtime knobs:
  `VLLM_MODEL`, `VLLM_TOOL_PARSER`, and `HUGGING_FACE_HUB_TOKEN` (gated models only).

> Architecture caveat: the official `vllm/vllm-openai` image is primarily built
> for x86_64. On an aarch64 host (Grace/GH200/GB10) confirm the tag has an
> arm64 + your-GPU build, or pin a CUDA-arm64 tag / build vLLM from source.

## 1. Bring up the stack with the AI profile

The dev overlay publishes the API on `localhost:8000` (no TLS), which is the
easiest surface for `curl`:

The AI runtime is on by default (M17), so the vLLM service starts with the
stack — no profile flag needed:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

The first boot downloads the model from HuggingFace into the `model_cache`
volume — this can take several minutes and many GB. Watch it:

```bash
docker compose logs -f vllm      # wait for "Application startup complete"
```

Confirm vLLM is serving and tool-calling is enabled (the model should appear):

```bash
docker compose exec api curl -s http://vllm:8000/v1/models
```

## 2. (Usually not needed) Point a household at a specific runtime

With the Docker stack, AI is **on by default** (M17): every household inherits
the deployment default (`FAMILY_CFO_AI_*` → `http://vllm:8000` + `VLLM_MODEL`),
so you can skip straight to step 3. You only need this step to override the
default — e.g. to run a **different** model than `VLLM_MODEL`, or to disable AI
for one household. Log in as the demo owner and save an explicit config:

```bash
BASE=http://localhost:8000/api/v1
MODEL=Qwen/Qwen2.5-32B-Instruct

TOKEN=$(curl -s -X POST "$BASE/auth/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@family-cfo.local","password":"demo-password-123"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -s -X PUT "$BASE/ai/runtime" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"provider\":\"vllm\",\"base_url\":\"http://vllm:8000\",\"model\":\"$MODEL\",\"enabled\":true}"
```

(Updating the runtime requires the `owner` role; the demo user is an owner. You
can also do this from the dashboard's **AI Runtime** settings page.)

## 3. Ask an open-ended question

```bash
curl -s -X POST "$BASE/chat/messages" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"If I buy a $1,000 phone instead of investing it at 6% for 20 years, what does it cost me?"}' \
  | python3 -m json.tool
```

## 4. Confirm the agentic path actually engaged

A 200 response alone does **not** prove the model ran — a failure falls back to
the deterministic snapshot. Check which path answered:

- **API logs** show `source=agentic_tool_calling` (vs `deterministic_stub`):

  ```bash
  docker compose logs api | grep "chat recommendation created" | tail -1
  ```

- **The stored recommendation** records `explanation_source`:

  ```bash
  docker compose exec db psql -U family_cfo -d family_cfo \
    -c "select explanation_source, left(answer, 80) from recommendations order by created_at desc limit 1;"
  ```

If it fell back to `deterministic_stub`, the usual causes are: the model didn't
emit `tool_calls` (wrong/missing `--tool-call-parser` for the model family), the
loop hit its iteration cap, the runtime was unreachable, or the answer contained
a number that didn't trace to a tool output (grounding guardrail — fails closed
on purpose). The API logs above name the reason.

## Swapping models

Easiest: the dashboard's **AI Runtime** page — search Hugging Face, check the
hardware-fit verdict, click **Apply**, and watch the live status until the new
model is active (M23; requires the `model-manager` sidecar, on by default).

From the terminal (same effect):

```bash
scripts/swap-model.sh Qwen/Qwen2.5-14B-Instruct                       # keep default vision
scripts/swap-model.sh Qwen/Qwen2.5-VL-32B-Instruct                    # vision-capable main
scripts/swap-model.sh Qwen/Qwen2.5-32B-Instruct none                  # no photo analysis
```

The dashboard's **AI Runtime** page offers the same catalog with live
hardware-fit feedback and generates this command for you.

Manual alternative — override in `.env` and restart just the runtime:

```bash
# .env
VLLM_MODEL=Qwen/Qwen2.5-72B-Instruct
VLLM_TOOL_PARSER=hermes
# For a gated model (e.g. Llama-3.3): also set HUGGING_FACE_HUB_TOKEN and
# VLLM_TOOL_PARSER=llama3_json

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d vllm
```

Remember to re-run step 2 with the new `model` string so the household config
matches what vLLM is serving.

## Testing the logic without a GPU

The tool-calling loop, argument validation, grounding guardrail, and
deterministic fallback are all covered by tests against a scripted runtime — no
model or GPU required:

```bash
cd apps/api && python -m pytest tests/test_chat_agentic.py tests/test_ai_tools.py
cd services/ai-orchestrator && python -m pytest tests/test_tool_calling.py
```


## Photo attachments (vision)

Chat accepts a photo (the 📷 button; on iPhone it offers Camera or Photo
Library). Per [ADR 0011](../adr/0011-vision-image-routing.md) the photo is
always turned into a text description first — by the main model if
`FAMILY_CFO_AI_SUPPORTS_VISION=true`, else by the `vllm-vision` describer
(default `Qwen/Qwen2.5-VL-7B-Instruct`) — and only the description enters the
advisor pipeline; the image itself is never persisted. The chat status banner
shows "photos supported" when a vision path is ready.

Both runtimes share the GPU via `VLLM_GPU_FRACTION` (0.60) and
`VLLM_VISION_GPU_FRACTION` (0.20). To run without vision:
`FAMILY_CFO_AI_VISION_ENABLED=false` and `docker compose up -d --scale vllm-vision=0`
— attached photos then get a clear "could not be analyzed" warning.


## Live data (exchange rates & prices)

The advisor can fetch live public facts as tools (ADR 0014): currency
conversion via `get_exchange_rate` (on by default; only the two currency codes
leave the box) and web/price lookups via `web_search` when you run the
self-hosted search profile:

```bash
docker compose --profile search up -d          # SearXNG metasearch
# .env: FAMILY_CFO_SEARXNG_URL=http://searxng:8080
```

Ask things like "how much is $2,000 in VND right now?" — the fetched rate
arrives as a tool result, so the grounding guardrail accepts it; the model
still cannot invent numbers of its own.
