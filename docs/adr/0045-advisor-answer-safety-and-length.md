# ADR 0045: The advisor never advises overpaying a debt, and answers aren't truncated

## Status

Accepted.

## Context

Two advisor defects from one debt-plan answer (user reports, 2026-07-20):

1. **It told the user to send $10,078.01 to pay off a loan whose balance was
   $3,816.36.** The engine (`calculate_debt_payoff`) is correct — it caps each
   payment at the remaining balance — but the model *ignored* it: it invented a
   "$10,000 extra payment," added it to the minimum, quoted the sum as the amount
   to send, and fabricated an incoherent "22 months / $77.78 interest" timeline.
   Nothing in the grounding rules stopped it from advising a payment larger than
   the debt.

2. **The answer was cut off mid-sentence** ("You're over-leveraged — and"). The
   tool-calling loop ran at its default `max_tokens=500`, far too small for a
   multi-phase plan.

## Decision

- **Grounding rule against overpayment.** The system prompt now states: never
  tell the user to send more than a debt's balance; the one-time amount to clear
  a debt is its `payoff_now` from `get_debt_outlook`; `debt_payoff`'s
  `extra_monthly_payment` is a RECURRING monthly amount, not a lump sum; quote
  `debt_payoff`'s own `months_to_payoff` and interest, never the model's; and
  don't invent an extra payment the user didn't ask for.

- **`payoff_now` on every debt.** `get_debt_outlook` now returns, per debt, the
  one-time amount that clears it today — balance plus about one month's interest
  — so the advisor has a grounded figure to quote instead of doing its own
  (wrong) arithmetic.

- **A real answer-token budget.** Chat runs the tool-calling loop at
  `max_tokens=1200` (`_ANSWER_MAX_TOKENS`) instead of the library's 500. The
  model still stops at its natural end; this only lifts the ceiling so a long
  plan isn't guillotined.

## Invariant

> The advisor never instructs the user to pay more than a debt's balance; a
> lump-sum payoff figure is `payoff_now` and nothing larger. A final answer is
> allocated enough tokens to complete — a cut-off answer is a bug, not a length
> policy.

## Rejected

- **Fixing only the engine**: the engine was already correct; the model's prose
  was wrong. The fix belongs in grounding + a grounded `payoff_now` figure.
- **Unbounded `max_tokens`**: 1200 comfortably fits the longest real plans while
  keeping a runaway generation bounded; raise it further only if a real answer is
  seen to need it.
- **Post-truncation "continue" stitching**: complexity for no gain versus simply
  giving the answer room the first time.
