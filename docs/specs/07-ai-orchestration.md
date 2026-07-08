# AI Orchestration

## Principle

The LLM explains. The financial engine calculates.

## Initial Runtime

Use vLLM first because it provides high-performance local inference and an OpenAI-compatible API.

The backend must not depend directly on vLLM-specific behavior. Use a runtime adapter interface.

Future adapters:

- Ollama
- llama.cpp
- Other OpenAI-compatible runtimes

## Specialized Systems

### Vision

Purpose:

- OCR
- Receipts
- Bills
- Product capture
- Bank statement extraction

Output: structured JSON with confidence.

Vision never provides financial advice.

### Financial Engine

Purpose:

- Budget calculations
- Cash flow
- Net worth
- Retirement
- Debt
- Savings goals
- Forecasts

Output: deterministic results with assumptions and warnings.

### Reasoning LLM

Purpose:

- Recommendations
- Coaching
- Explanations
- Tradeoffs
- Confidence summaries

Input:

- Financial engine output
- User goals
- Conversation history
- Structured vision output
- Relevant retrieved context

## Recommendation Contract

Every AI recommendation must expose:

- Direct answer
- Assumptions
- Deterministic calculation references
- Short-term impacts
- Long-term impacts
- Tradeoffs
- Alternatives
- Confidence
- Missing information

## Guardrails

- The LLM must not invent account balances, debt terms, or investment performance.
- The LLM must cite calculation outputs when making numeric claims.
- Financial advice must be framed as educational guidance unless a future legal review changes this policy.
- The system must not autonomously move money or make trades.

## Agentic Tool-Calling (planned; see ADR 0009)

The initial implementation is **compute-then-narrate**: the app computes with the
financial engine and the LLM only rephrases the results. To answer open-ended
questions ("if I buy this, how many years of retirement does it cost me?") without
an endpoint per question, the direction — recorded in
[ADR 0009](../adr/0009-agentic-tool-calling.md) — is **agentic tool-calling**:

- The financial-engine calculations become callable **tools** (described by JSON
  schemas). The model decomposes a question and orchestrates tool calls; it never
  computes numbers or supplies facts itself.
- Every figure in an answer traces to a tool output (which queries Postgres / runs
  the engine). The guardrail principle is unchanged; the trust boundary moves to
  validating the model's **tool arguments**.
- Facts the model cannot compute (cost of living, market rates, balances) come
  from the user or a data-source tool — never a model guess; a missing required
  fact is asked back, not fabricated.
- The structured endpoints (`/advisor/*`, `/reports/generate`) remain as fast
  deterministic paths and the fallback when no model is configured.

This extends "the LLM explains, the financial engine calculates" to a multi-step
flow; it does not weaken it.
