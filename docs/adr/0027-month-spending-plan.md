# ADR 0027: The month spending plan — "left to spend this month" (M113)

## Status

Accepted. The third layer of the Overview redesign, foreseen in ADR 0026's
rejected-for-now list. Shipped on iOS and the Angular dashboard together
(ADR 0025).

## Context

The Overview now had the cash-timing view (ADR 0026's outlook) and the worst
case (the stress test), but not the question a family actually budgets with:
*"how much can we still spend this month?"* — the Simplifi/Monarch "spending
plan" number, which is **income-aware and month-scoped**.

## Decision

`left to spend = expected income − spent so far − bills still due − loan & lease payments`

One read-only endpoint (`GET /overview/spending-plan`, `getSpendingPlan`)
computed in `finance_service.spending_plan`. The four terms are constructed so
**no dollar is ever counted twice**, which depends on how Family-CFO already
classifies money:

1. **Expected income** = income deposits *received* this month (the same set the
   income analysis counts: transfer-excluded, override-respecting) **+ paydays
   projected** through month end by stepping each detected income source from
   its last sighting (the ADR 0026 machinery).
2. **Spent so far** = `sum_spending` month-to-date. Card **charges** count when
   they happen; card **payments** and mortgage/loan/lease payment legs are
   categorized *Transfers* and excluded — so paying a card never double-counts
   the charges that built its balance. A bill paid this month is a categorized
   charge and lives here.
3. **Bills still due** = the payment timeline's *unpaid* bill-kind items due
   through month end (an overdue bill still claims this month's income). A paid
   bill has moved to term 2 — never in both.
4. **Loan & lease payments** = the recorded monthly payments on liability
   accounts, counted here exactly once *because* their transfer legs are
   invisible to `sum_spending`. Cards excluded entirely (see term 2);
   payroll-deducted 401(k) loans excluded (income is measured as net deposits,
   which already reflect them).

Presentation: a card on both clients between the Cash outlook and the Stress
test. Positive → "about $X/day for the remaining N days" (a pace, not a rule).
Negative → "this month's spending has outrun this month's income — the gap is
drawing on cash you already had," in warning color, with the full equation
printed under the number either way.

## Accrual vs cash — why both cards exist

The **outlook** answers *"will my cash dip below zero, and when?"* (timing: a
card payment hits when it hits). The **plan** answers *"is this month living
within this month's income?"* (accrual: spending counts when charged, card
payments don't exist). The same month can be cash-covered yet plan-negative —
that is information, not contradiction, and each card names its frame.

## Invariant

> Every dollar appears in exactly one of the plan's terms. Card payments and
> liability-payment transfer legs never appear in any term; card charges appear
> only in "spent"; a bill appears in "still due" until its charge lands, then
> only in "spent". The plan never invents figures: income projection requires a
> detected recurring source, and `per_day` is 0 rather than negative.

## Rejected

- **Counting card payments as spending** — double-counts every charge that
  built the balance; the Transfers categorization already encodes the truth.
- **Per-category envelopes in this card** — Budgets already do that; this is
  the whole-month headline, not an envelope system.
- ~~**A planned-savings term**~~ — **added in M118**: goals now carry an
  optional `monthly_contribution` (migration 0058); the plan subtracts the sum
  of declared contributions as its fifth term. Savings transfers are Transfers
  (excluded from spending) and stay within liquid, so the term never
  double-counts — reserved exactly once, only when declared. Goal create/update
  are audited and undoable (ADR 0023).
- **Calendar-month income smoothing** (expected annual ÷ 12) — invents money in
  months where paychecks genuinely don't land; projection from real cadence is
  honest about five-Friday months and gaps.
