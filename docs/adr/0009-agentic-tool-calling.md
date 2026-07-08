# ADR 0009: Agentic Tool-Calling over Deterministic Financial Primitives

Status: Accepted

## Context

Family CFO's AI layer today follows a **compute-then-narrate** model (ADR 0003):
the deterministic financial engine computes every number, and the local LLM only
rephrases those already-computed facts into prose. Numbers the model invents are
discarded by a guardrail that checks the model's output against the facts we put
in the prompt (M4).

Two limitations surfaced:

1. **The AI's current role is thin.** It only narrates the purchase advisor and
   report outputs, is off by default, and adds phrasing rather than capability.
   The product's identity — "a local AI financial advisor" — implies far more.

2. **Open-ended questions don't fit a per-endpoint model.** A real question like
   *"if I buy this $1,000 phone, how many years of retirement does it cost me if
   I retire at 50 in Vietnam?"* is a composition of several calculations
   (opportunity-cost growth × retirement expense ratio × a cost-of-living input).
   The combinatorial space of such questions is unbounded; you cannot build an
   API endpoint per question, and you cannot let the model answer freely because
   it would fabricate the numbers and facts.

The combinatorial explosion is in the *questions*, not the *math*: a small set of
composable deterministic primitives can answer a near-infinite space of questions
if something decomposes the question and orchestrates the primitives.

## Decision

Adopt an **agentic tool-calling** architecture for open-ended questions: the local
model orchestrates calls to deterministic financial tools; it never computes
numbers or supplies facts itself.

Principles that constrain the design:

- **The model orchestrates; tools compute.** The model decomposes a question and
  decides which tools to call with which arguments. Every number in its answer
  comes from a tool result (which queries Postgres / runs the financial engine),
  never from the model's own arithmetic. This is ADR 0003 restated for a
  multi-step flow, not a departure from it.
- **Tools wrap the existing engine.** The financial-engine calculations
  (`calculate_net_worth`, `cash_flow`, `budget_summary`, `emergency_fund`,
  `goal_progress`, `purchase_impact`, `debt_payoff`, `retirement_projection`, plus
  new primitives such as future-value / opportunity-cost) become the callable
  tools, described by JSON schemas. The calculation logic is unchanged.
- **The trust boundary moves to tool arguments.** Instead of validating the
  model's output text against prompt facts, the system validates the *arguments*
  the model passes to tools (types, ranges, currency, referenced entity ids) and
  keeps tool outputs authoritative. The final narration is still checked so that
  every figure it states traces to a tool output — the guardrail principle from
  M4 carries over, its mechanism shifts.
- **Facts the model cannot compute must come from real sources, never the model.**
  Values like a country's cost of living, current market rates, or account
  balances are supplied by the user or by a data-source tool — the model is
  never allowed to guess them. A missing required fact is surfaced as a question
  back to the user, not fabricated.
- **Structured endpoints remain.** `POST /advisor/purchase`, `/advisor/retirement`,
  and `/reports/generate` stay as fast, deterministic paths and as the fallback
  when no model is configured or the tool loop fails. Tool-calling is an
  additive, general `chat`-style path, not a replacement — so little existing
  work is discarded.
- **Local-only, opt-in, unchanged privacy posture.** Tool-calling runs against
  the same opt-in local vLLM (ADR 0004); household data still never leaves the
  box, and external providers still require a future superseding ADR (ADR 0008).

Data placement follows the same split (unchanged by this ADR, recorded for
clarity): the **ledger** (balances, transactions, the numbers) stays in
PostgreSQL because it must be exact, aggregatable, concurrent, access-controlled,
and auditable; **unstructured content** (documents, receipts, conversation) lives
in files plus, eventually, a vector store for semantic retrieval (still backlog).
The model reads *tool outputs*, not raw storage, either way.

## Reuse / Rework / Drop

Recorded so the blast radius is on the record:

- **Reused as-is:** the entire financial engine (it becomes the tools),
  `ai_runtime_configs` + the AI-runtime API + the vLLM Docker service, the
  `DeterministicExplanationAdapter` fallback, and the guardrail number-traceability
  helpers.
- **Extended:** `VLLMAdapter` gains tool/function-calling support (same
  OpenAI-compatible endpoint, plus a `tools` parameter and a multi-turn loop);
  guardrails gain tool-argument validation.
- **Reworked or dropped:** the single-shot `LlmExplanationAdapter.explain_*` flow
  and the fact-in-prompt builders (`PurchaseFacts`/`ReportFacts`,
  `build_*_explanation_prompt`, `known_values_from_facts`) are superseded by a
  general tool-calling prompt/loop. This is a few hundred lines; the structured
  endpoints can keep the old flow if desired.

## Consequences

- New work is mostly *construction*, not demolition: a tool-descriptor layer over
  the engine, a tool-calling orchestration loop, and argument-validation
  guardrails. A milestone spec gate will scope the first slice (see the roadmap /
  implementation tasks).
- The system now depends on the configured local model's tool-calling reliability;
  a weak model may pick wrong tools or arguments. Argument validation, bounded
  tool sets, and the deterministic fallback contain this, but it is a real new
  dependency and belongs in test/verification planning.
- Answers to open-ended questions become possible without an endpoint per
  question, while every stated number remains grounded and auditable.
- This ADR extends ADR 0003 (LLM explains, engine calculates), ADR 0004 (local
  runtime abstraction), and ADR 0007 (replaceable interfaces — the `RuntimeAdapter`
  seam is the extension point); it is consistent with ADR 0008 (local-only AI). It
  supersedes none.
