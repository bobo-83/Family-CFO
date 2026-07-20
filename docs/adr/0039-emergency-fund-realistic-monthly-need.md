# ADR 0039: Emergency-fund coverage divides by realistic monthly need, not bills alone

## Status

Accepted.

## Context

Emergency-fund coverage ("your fund covers 7.1 months") is
`liquid_fund / monthly_essential_expenses`. The denominator was
`_monthly_bill_total` — recurring bills only (mortgage, internet, insurance).
That is absurdly optimistic: it ignores everyday living costs (groceries, gas,
childcare) and every loan/credit-card payment. A household that truly burns
~$14k/month could be told a $100k fund lasts many months because its *entered
bills* sum to a small number. A member flagged their own 7.1-month figure as
obviously wrong — "it only takes into account recurring bills, not my average
spending and my loan/debt minimum payments."

The subtlety is double-counting. In this app:

- **Bill payments are categorized transactions** (a mortgage lands in a Housing
  category), so they are ALREADY inside "average spending"
  (`sum_spending` excludes only transfers, income, and taxes).
- **Debt minimum payments are transfers**, so they are NOT in average spending.

So naively summing `bills + average spending + debt minimums` counts
housing/utilities twice.

## Decision

**The emergency-fund denominator is the realistic monthly cash a household must
cover if income stopped:**

```
monthly_essential_expenses
  = recurring_bills
  + debt_minimum_payments
  + max(0, average_monthly_spending − recurring_bills)
```

- `recurring_bills` — `_monthly_bill_total` (unchanged).
- `debt_minimum_payments` — `_monthly_debt_minimums`: minimum payments on loans,
  cards, and other liabilities (leases). **Deduped** against bills via
  `bill_covered_account_ids` (a debt also modeled as a bill is counted once) and
  **excluding 401(k) loans** (repaid by payroll deduction — the money never
  reaches the bank, so it makes no claim on liquid cash). Cards contribute their
  *minimum*, not the statement balance: in an emergency you pay the minimum to
  stay current. This mirrors the safe-to-spend obligation model.
- `average_monthly_spending` — trailing-3-complete-month actual spending / 3, the
  same window as the M44 savings rate. The **bill portion is stripped back out**
  (`max(0, avg − bills)`) so housing/utilities counted inside spending aren't
  added a second time on top of the explicit bills line.

Equivalently this is `max(recurring_bills, average_spending) + debt_minimums`:
the larger of committed bills vs. what the household actually spends, plus the
debt payments that live outside spending. Each dollar is counted once.

Implemented once in `finance_service.monthly_essential_expenses(...)` and fed to
the engine's `calculate_emergency_fund_months`, so every surface — the household
overview card, the advisor's `get_emergency_fund` tool, the goal gap — inherits
it. `EmergencyFundInputs` no longer carries the denominator; the cheap callers
(safe-to-spend, goal current) that only need the fund balance don't pay for the
trailing-spending query or debt sweep.

## Invariant

> Emergency-fund coverage divides the liquid fund by realistic monthly need:
> recurring bills + deduped debt minimum payments (excluding payroll-deducted
> 401(k) loans) + everyday spending above bills. No component is double-counted;
> the denominator is always ≥ recurring bills. Any new obligation that claims
> monthly cash must be added here or justified as already inside one of the three
> terms.

## Rejected

- **Bills only** (the bug). Ignores living costs and all debt service; wildly
  overstates coverage.
- **Literal `bills + average spending + debt minimums`.** Double-counts
  housing/utilities (in both bills and spending), understating coverage.
- **Average total spending alone.** Includes one-off spikes (a $20k home project)
  as if they recurred monthly, wildly *understating* coverage; and misses debt
  minimums, which are transfers excluded from spending.
- **A hand-entered "monthly expenses" field.** Violates the minimize-duplicate-
  input goal — we already have bills, debts, and transactions; derive it.
