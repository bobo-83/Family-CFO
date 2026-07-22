# 0060 — MTP speculative decoding for the main model

Date: 2026-07-21
Status: Accepted

## Context

Voice-mode latency audit (user report): each advisor turn runs 3–5 model
rounds, and a thinking model generates most of its tokens invisibly, so
decode throughput dominates wall-clock. On the GB10 (bandwidth-bound,
~273 GB/s) Qwen3.6-35B-A3B-FP8 decoded at ~53 tok/s. The audit also ruled
out the usual suspects: no memory growth anywhere (API 103 MiB, vLLM steady,
KV cache peaking at 4%), history capped at 8 messages, memory extraction
already off the request path. Prefix caching is auto-disabled by vLLM 0.24
for this model's hybrid GDN layers and cannot be enabled — that lever does
not exist here.

The Qwen3.6 checkpoint ships a 1-layer MTP (multi-token prediction) head
(`mtp_num_hidden_layers: 1`) that vLLM supports as a speculative decoder,
and it was sitting unused.

## Decision

1. The vLLM service's compose `command` moved from a static YAML list to a
   single shell line so an optional `VLLM_EXTRA_ARGS` env var can inject
   model-specific flags — a static list cannot express an optional argument.
   Compose interpolates values before the shell parses them; only
   `VLLM_EXTRA_ARGS` may contain spaces, and any JSON in it must be
   space-free and single-quoted in `.env`.
2. The box's `.env` sets
   `VLLM_EXTRA_ARGS=--speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":2}'`.

Measured on the box (512-token fixed prompt, temperature 0): 53.4 → 71.9
tok/s (+35%), draft acceptance 85–87% (per-position 0.93 / 0.80). A grounded
"when can I retire" E2E through the full agentic pipeline: 15.8 s with tool
calls, guardrail, and reasoning parsing all intact. Speculative decoding is
draft-verify and therefore lossless — outputs are the model's own tokens.

## Rejected options

- **Prefix caching** — unsupported for this hybrid architecture in vLLM 0.24
  (`qwen3_5` is not in the mamba-prefix-caching allowlist); the 0% hit rate
  is a platform constraint, not a misconfiguration.
- **More speculative tokens (3+)** — the 1-layer head re-runs per draft
  position; acceptance already falls to 0.80 at position 2. Diminishing and
  untested; revisit if latency still hurts.
- **NVFP4 quantization swap** — likely another large decode win on Blackwell,
  but changes numerical quality (unlike MTP) and deserves its own A/B.

## Invariant

`VLLM_EXTRA_ARGS` is per-model state, like `VLLM_TOOL_PARSER`: anything that
swaps the main model (scripts/swap-model.sh, the AI-runtime apply flow) must
clear or update it — a speculative config referencing an MTP head the new
checkpoint lacks will crash-loop the runtime.
