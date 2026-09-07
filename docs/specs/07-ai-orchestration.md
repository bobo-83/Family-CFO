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
  balance, institution, last sync time, emergency-fund reservation and
  vested-RSU flag.
- Liabilities are included, categorised `debts`, with balances signed as stored;
  `get_debt_outlook` stays the authority on the amount owed, rate, minimum
  payment, payoff and strategy. The sign is a reading of the balance, not a
  property of the account type: negative is owed, zero is clear, and a positive
  liability balance is a credit (an overpayment or refund) that must never be
  reported as a debt.
- An account outside the household base currency is listed like any other and
  flagged `matches_base_currency: false`. An inventory that silently omits an
  account the family can see recreates the problem it exists to solve; only
  base-currency arithmetic excludes it. The flag claims currency equality and
  nothing more: matching is necessary for a total to include an account, never
  sufficient (net worth skips 401(k) loans; safe-to-spend counts only liquid
  types), so the tool that owns a total remains the authority on what is in it.
- The inventory is CURRENT (`as_of: "current"`). `get_net_worth(month=…)`
  answers for a past month, and today's accounts are not that month's — so both
  tools' descriptions and payloads say so rather than leaving the model to
  itemise a historical total with a present-day list.
- The tool and the `GET /accounts` endpoint project one shared assembler, so the
  Accounts tab and the advisor can never name different accounts.

Applied to totals (M123, ADR 0075, #152): the tool that OWNS a total discloses
what it left out, and the model must not add it back.

- `get_net_worth`, `get_emergency_fund`, `get_safe_to_spend`,
  `project_purchase_impact`, `project_retirement` and `when_can_i_retire` are
  base-currency figures. Each returns `excluded_accounts` beside `warnings`: the
  accounts held in another currency that WOULD have been components of that
  figure (eligibility-specific — net worth skips 401(k) loans regardless, the
  emergency fund counts liquid or designated accounts, safe-to-spend touches
  cash, reservations and debts), as `{name, balance}` with `balance` typed in the
  account's OWN currency. `name` is household text and never grounds a number;
  the balance's display string does, so the advisor can SAY what it could not add
  up and quote it in its own currency.
- The warning that travels with the figure — and is persisted in the
  calculation row — is generic ("1 account held in EUR is not counted in this
  USD figure; a balance in another currency is never converted"). It never
  names the account: a warning is app-authored text whose digits ground, and
  account names are sealed content while `warnings_json` is plaintext.
- `GROUNDING_RULES` forbid adding an excluded balance back into a base-currency
  figure or presenting it as base-currency money. An approximate conversion, if
  the family asks for one, must come from `get_exchange_rate` and be labelled
  approximate; without that tool the advisor says it cannot convert here.
- A past-month `get_net_worth(month=…)` reports `excluded_accounts: null`: a
  snapshot has no record of what it left out, and an empty list would claim
  "nothing".

## Guardrails

- The LLM must not invent account balances, debt terms, or investment performance.
- The LLM must cite calculation outputs when making numeric claims.
- Grounding is unit-aware: money travels as minor units plus a display string,
  and only the display form grounds an answer. Accepting the minor-unit twin
  would let a hundredfold overstatement of a real balance pass the guardrail.
  Every tool therefore emits money through the shared serializer; a raw
  `<field>_minor` output field grounds nothing at all.
- Grounding comes from figures, not from names. Household- and bank-supplied
  text (account, institution, merchant, category, description, label — and the
  model's own search `query`) is excluded from the grounded set: an account
  called "Fidelity Brokerage 9876" must not make USD 9,876.00 quotable. Free
  text the app itself produced — notes, warnings, and `web_search` snippets —
  still grounds, because quoting a public price out of a snippet is the point
  of that tool.
- The mirror of that rule on the answer side: digits that name a thing rather
  than an amount (`401k`, `529 plan`, `1099-DIV`) are not money claims. Without
  this the advisor would fail closed for saying an account type out loud, since
  those digits used to ground only as a side effect of the account's name.
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
