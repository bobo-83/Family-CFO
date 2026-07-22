# 0063 — The model speaks dollars, never minor units

Date: 2026-07-22
Status: Accepted

## Context

Advisor tools took money arguments in minor units (cents): the model iterated
`project_retirement` with `monthly_contribution_minor: 1100000` — correctly,
$11,000/month reaches the 4% target at 60 — and then told the user to "save
about **$1.1 million per month**" (user report 2026-07-22). The math was
right; the verbalization read its own cents input as dollars. The grounding
guardrail passed it because 1,100,000 genuinely appears in the tool trace —
grounding checks provenance, not units.

The user's instinct was "I need a smarter model", but no model should be
handed a unit convention that makes a correct number wrong by 100× when
spoken. Minor units are a wire/database concern; they were never meant to be
part of the conversation.

## Decision

- Every model-facing tool INPUT is in dollars (`_MONEY_FIELD` is now a
  `number` in major units): `price`, `present_value`, `current_savings`,
  `monthly_contribution`, `annual_expenses`, `balance`, `minimum_payment`,
  `extra_monthly_payment`, `amount`. `_money_arg` converts to minor units
  internally (Decimal, half-up) and still accepts the legacy `*_minor`
  integer form so an older cached schema cannot break a turn.
- Tool RESULTS keep `amount_minor` (clients and the guardrail use it) but
  always carry `display`; GROUNDING_RULES now says explicitly: quote the
  display string, never read `amount_minor` as dollars — it is 100× smaller
  than it looks.
- A schema test guards the invariant: no tool input may ever be named
  `*_minor` again.

## Rejected options

- **A smarter model** — any model can misread an alien unit convention; this
  failure was designed in, not reasoned in.
- **Guardrail scale-checking** (reject quotes that match a trace value ÷100)
  — heuristic, false-positive-prone (real $11k and $1.1M can both occur),
  and fixes the symptom while leaving the trap.

## Invariant

The conversation layer (tool inputs, displays, answers) is entirely in major
units; minor units exist only inside the engine, the database, and result
payloads' `amount_minor` fields, which are never to be spoken.
