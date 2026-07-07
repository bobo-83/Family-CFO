# AI Orchestrator

The AI orchestrator coordinates local reasoning models, financial engine results, conversation history, vector context, and structured document outputs.

Initial runtime target: vLLM with an OpenAI-compatible API.

The runtime must be abstracted so future adapters can support Ollama, llama.cpp, and other OpenAI-compatible runtimes.

## M4 Scope

Implemented as the `family_cfo_ai_orchestrator` package. It has no dependency on `apps/api` or `family_cfo_financial_engine` — callers (see `apps/api/src/family_cfo_api/llm_explanation.py`) depend on this package, never the reverse, keeping the runtime replaceable (ADR 0004, ADR 0007).

- `RuntimeAdapter`: a `Protocol` with a single `complete(messages, *, temperature, max_tokens) -> RuntimeCompletion` method. Implementations must retry internally and raise `RuntimeUnavailableError` only once retries are exhausted — callers never need to handle transient HTTP errors themselves.
- `VLLMAdapter`: the first `RuntimeAdapter` implementation, calling an OpenAI-compatible `/v1/chat/completions` endpoint over HTTP via `httpx`. Retries are immediate (no backoff), since the target runtime is expected to be co-located on the same private network rather than a remote cloud endpoint.
- `prompts.py`: versioned prompt templates. `PURCHASE_EXPLANATION_PROMPT_VERSION` and `build_purchase_explanation_prompt(facts: PurchaseFacts)` render a system + user message pair that lists only the numeric facts the caller supplies — the prompt itself instructs the model not to invent figures.
- `guardrails.py`: `validate_recommendation(text, known_values)` extracts numeric substrings from generated text and flags any that don't appear in `known_values`. `known_values_from_facts(facts)` derives that set from the exact fact strings sent in the prompt, so guardrail validation can never drift from what the model was actually told. This is a conservative, string-based check — it exists to catch invented figures, not to validate arithmetic, and false positives are expected to fail closed into a deterministic fallback rather than risk a fabricated number reaching a user.

## Assumptions and Limitations

- Only the vLLM adapter ships; Ollama and llama.cpp adapters are future work behind the same `RuntimeAdapter` interface.
- No API-key/secret handling — the runtime is assumed to be a private, self-hosted, unauthenticated (or network-isolated) endpoint. Cloud-hosted OpenAI-compatible endpoints requiring credentials are out of scope until an explicit opt-in ADR permits sending sensitive data off-host.
- The guardrail is deliberately conservative: it does not understand units or semantics, only whether a number was present in the supplied facts. It will reject some fabrications a smarter check would catch, and could in principle flag pre-existing numbers phrased differently than in the facts (e.g. rounding) — the fallback path exists precisely so this doesn't produce a hard failure for the user.
- This package has no database, HTTP server, or FastAPI dependency; it can be unit tested entirely with mocked HTTP transports (`httpx.MockTransport`) and mocked `RuntimeAdapter` implementations, no real vLLM server required.

## Tests

```bash
cd services/ai-orchestrator
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
```
