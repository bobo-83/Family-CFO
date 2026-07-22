# 0058 — A reasoning model's empty answer never reaches the user

Date: 2026-07-21
Status: Accepted

## Context

Qwen3.6 (ADR-era model swap) thinks by default: vLLM's `--reasoning-parser
qwen3` splits its output into `reasoning_content` (chain-of-thought) and
`content` (the visible answer). On open-ended questions with no matching tool
("What about my social security?"), the model spent the entire
`_ANSWER_MAX_TOKENS` (1200) budget thinking and returned `content: null`.

The empty string then slid through every layer unchallenged: the vLLM adapter
coerced `null` to `""`, the tool-calling loop returned it as a *completed*
answer, chat.py only rejected `answer is None`, and the grounding guardrail
passed it (no numbers, no violations). The empty answer was persisted to
`recommendations`/`conversation_messages` and — worst of all — the hands-free
voice session "spoke" it as dead air with no error, because the speech
synthesizers no-op on empty text. Separately, the memory extractor crashed on
`completion.text = None` (`'NoneType' has no attribute 'strip'`).

## Decision

Layered, failing toward a real answer:

1. **Adapter** (`vllm_adapter.complete`): `content: null` → `""`; callers
   always get `str`.
2. **Tool loop** (`run_tool_calling_loop`): a blank final text is *not* a
   final answer. The loop appends a corrective user turn ("Answer now in plain
   text, briefly…") and continues; the iteration cap bounds repeats, and
   exhaustion returns `completed=False` as before.
3. **chat.py**: rejects blank answers (`not result.answer`), not just `None`,
   on both the first pass and the guardrail retry — falling back to the
   deterministic snapshot, which always has text.
4. **Token budget**: `_ANSWER_MAX_TOKENS` 1200 → 2400 so thinking *and* the
   visible answer fit; the tool runtime's HTTP timeout rises to 90s to match
   (~50 tok/s on the box GPU).
5. **iOS voice**: if an answer is ever unspeakable anyway, the session speaks
   an explicit apology instead of looping silently — a hands-free user has no
   screen to notice an empty bubble.
6. **Memory extractor**: `parse_extracted_memories` accepts `None`/empty and
   yields no memories instead of crashing.

## Rejected options

- **Speak/return `reasoning_content` when `content` is empty** — it is
  chain-of-thought: unpolished, often self-contradictory mid-stream, and not
  guardrail-grounded. Never user-facing.
- **Disable thinking (`enable_thinking: false`)** — thinking is why the 35B
  model plans multi-tool strategies well (the reason it won the A/B); trading
  answer quality everywhere to fix truncation in one place is backwards.
- **Immediate deterministic fallback on first empty** — the nudge usually
  recovers a real model answer for one cheap extra turn; the fallback stays
  as the terminal safety net.

## Invariant

No advisor turn visible to the user — persisted, displayed, or spoken — is
ever empty. Every layer that could produce one must either recover a real
answer or fall back to deterministic text.
