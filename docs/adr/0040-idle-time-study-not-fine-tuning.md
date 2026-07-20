# ADR 0040: The advisor learns by idle-time study, never by fine-tuning

## Status

Accepted.

## Context

The advisor didn't feel like it got smarter the more the family used it. The
model's weights are frozen (that is normal — every serving LLM works this way);
what it "knows" per conversation is whatever the app injects. The existing
memory extractor (ADR 0016) learns only from what members *say* in chat — the
transaction history itself was invisible except through per-question tool calls.

A family member proposed "training the AI on our data while the box is idle,"
with a visible progress metric ("80% trained on your data"). Actual LoRA
fine-tuning is feasible on the box's GB10, but it is the wrong tool for facts:

- Weights freeze knowledge at training time; financial facts change daily, and a
  model that memorized May confidently asserts May's numbers in July.
- Models *reconstruct* facts approximately — hallucinated dollar amounts.
- Training competes with vLLM for the GPU, taking the advisor down for hours.
- Weights can't be audited or selectively deleted — a privacy dead end for a
  privacy-first app (your data baked into the model everyone's chats use).
- Fine-tuning has no honest "percent of your data absorbed" metric.

## Decision

**While the box is idle, a worker job STUDIES the transaction history — one
complete calendar month per tick — and distills durable insights into household
memories. Knowledge lives in rows the advisor's prompt injects, never in model
weights.**

Mechanics (`ai_study.py`, `study_months` table, worker job every 5 min):

- **Study unit = one complete calendar month.** The current partial month never
  counts — it would go stale daily and make coverage a moving target.
- **Deterministic digest first.** Postgres computes the month's facts (income,
  spending, by-category, top merchants); the LLM only *interprets* the digest
  into insights. Numbers come from SQL, not model memory.
- **Stable insight keys** (`grocery_spending_pattern`, `income_rhythm`, …) with
  `source="study"`: re-studying UPDATES knowledge in place, so the injected
  context stays bounded (the chat cap of 50 memories holds) and never
  contradicts itself.
- **Staleness by fingerprint.** Each studied month records a digest hash;
  recategorizing or late imports change the hash and re-queue the month.
- **Idle-gated.** A tick yields when any household chat happened in the last 10
  minutes (a human owns the GPU) or when no runtime is usable — so selecting a
  model is exactly what starts studying, matching the family's mental model.
- **Honest coverage metric**: `studied complete months / complete months with
  data`, surfaced by `GET /ai/study` — "Studied 14 of 17 months — 82%" is a real
  claim, unlike a fake "percent trained." Shown on the web AI-runtime page and a
  new iOS Settings › Advisor knowledge screen (parity, ADR 0025).
- Study writes bypass audit/undo like all memory side effects (ADR 0016 pattern);
  insights are household-shared financial facts, consistent with ADR 0038's
  memory scoping, and remain individually deletable via the memory surface.

## Invariant

> The advisor's knowledge of household data lives in retrievable, deletable
> rows — memories, digests, tool queries — never in model weights. Any future
> "learning" feature must keep every learned fact auditable (where did this come
> from), current (re-derivable from the database), and erasable. Model
> fine-tuning on household data is out, absent a new ADR superseding this one.

## Rejected

- **LoRA fine-tuning on generated Q&A pairs** (even idle-time): stale-but-
  confident numbers, GPU contention, unlearnable personal data, no honest
  progress metric.
- **Unbounded per-month memory appends**: 17 months × 5 insights would blow the
  50-memory injection cap and evict user-stated facts; stable keys keep the
  profile compact.
- **Studying the current partial month**: permanent staleness churn for insights
  that change daily; the advisor's live tools already cover "this month."
- **A time-based "trained for 3 hours" metric**: measures effort, not knowledge;
  months-covered measures the thing the user actually cares about.
