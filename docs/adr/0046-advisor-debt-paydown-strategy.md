# ADR 0046: The advisor prioritizes debt paydown by rate, and respects 401(k) loans

## Status

Accepted.

## Context

A member's debt-plan answer was financially backwards (report 2026-07-20):

- It told them to pay off a **~2% student loan first** — a rate below inflation,
  which should be the *lowest* priority. The advisor used a naive "smallest
  balance first" (snowball) heuristic and ignored the interest rate entirely.
- It told them to pay off their **401(k) loans (9.5%)** — but a 401(k) loan's
  interest is paid to the borrower's *own* retirement account and is repaid by
  payroll deduction, so its rate is not a true cost; the member reasonably wants
  to keep them.
- It **ignored their credit cards, car leases, bills, and subscriptions** — the
  places a real avalanche (and the actual overspending) would start.

The tool already returns each debt's rate and type; the advisor just wasn't
reasoning with them.

## Decision

**The advisor prioritizes debt paydown by interest rate, deprioritizes low-rate
debt, treats 401(k) loans as special, and looks at the whole picture — enforced
by both grounding rules and per-debt data.**

- **Grounding rules** now instruct: recommend paydown by INTEREST RATE, not
  balance (highest-rate first, usually a card); a debt at/below ~4-5% APR (at or
  below inflation) is LOW priority — don't rush to pay it off ahead of higher-rate
  debt or investing; honor each debt's `strategy_note`; never rush a 401(k) loan
  for its rate (interest paid to yourself, payroll-deducted); and read the WHOLE
  picture (all debts incl. cards/leases, `get_bills`, `get_spending_insights`)
  before a plan.
- **`get_debt_outlook` returns a `strategy_note` per debt**: 401(k) loans get the
  pay-to-yourself nuance; any debt at/below the low-rate threshold (5%) gets a
  low-priority steer. So the guidance is grounded in the data, not only the
  prompt.

## Invariant

> A debt-paydown recommendation is ordered by interest rate, not balance. Low-rate
> debt (≤ ~5% APR) is never urged ahead of higher-rate debt or investing. A
> 401(k) loan is never recommended for early payoff on rate grounds alone. Any
> debt needing special handling carries a `strategy_note` the advisor must honor.

## Rejected

- **Leaving it to the model's general knowledge**: it demonstrably defaulted to
  snowball-by-balance and mishandled the 401(k) loan. The nuance must be grounded.
- **A hard avalanche algorithm that outputs the plan**: the advisor should still
  explain and adapt to the household (e.g. a small motivational win); the fix is
  correct *inputs and guardrails*, not removing its judgment.
- **Encoding the low-rate threshold as user-configurable**: 5% is a sensible
  default tied to inflation / long-run returns; revisit if it proves off, but
  don't add a setting nobody asked for.
