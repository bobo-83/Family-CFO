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

## Grounded Read Tools Cover Every Visible Data Domain

A data domain the family can see in the app must have a matching read-only
grounded tool (ADR 0009; the rule is restated in `AGENTS.md`). Without one the
advisor has to say it cannot see something the app is displaying one screen
away, and sends the household elsewhere for a figure the box already holds.

Two properties make such a tool safe to add:

- **The tool is the grounding.** Every figure in an answer must come from a tool
  result, so itemising a record through a tool is what makes it quotable. A
  detail the model is not given is a detail it must refuse to state.
- **The payload carries its own guardrail.** Detail that could be misread has to
  travel with the rule that governs it, not just with the data. An account
  inventory therefore carries each account's spendability category (M33) and the
  rule that spendable money comes from `get_safe_to_spend` alone — it never
  invites the model to add balances up or subtract one from another.

Applied to accounts (`get_accounts`, M122):

- The tool returns EACH account's name, type, spendability category, signed
  balance, institution, emergency-fund reservation and vested-RSU flag.
- Liabilities are included, categorised `debts`, with balances signed as stored;
  `get_debt_outlook` stays the authority on the amount owed, rate, minimum
  payment, payoff and strategy.
- An account outside the household base currency is listed like any other and
  flagged `included_in_base_currency_totals: false`. An inventory that silently
  omits an account the family can see recreates the problem it exists to solve;
  only base-currency arithmetic excludes it.
- The tool and the `GET /accounts` endpoint project one shared assembler, so the
  Accounts tab and the advisor can never name different accounts.

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
