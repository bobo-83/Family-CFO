# ADR 0055: Recategorize deposits from the income page; liability postings aren't income

## Status

Accepted. Refines ADR 0053/0054 (Income category counts as income).

## Context

Counting Income-categorized deposits from any account (ADR 0054) surfaced two
real-data problems:

1. **Double-counting RSU.** An RSU vest sells in a brokerage (Broadcom → Charles
   Schwab, $58,839) and the proceeds are then **transferred to checking** (Amex,
   $59,797). Both legs were categorized Income, so both counted. The amounts
   differ slightly (price movement / other cash), so the internal-transfer
   matcher — which excludes equal-and-opposite legs — didn't pair them, and the
   Income category overrode transfer exclusion anyway.
2. **Lease payments counted as income.** A $649.73 "PAYMENT" posted on a Subaru
   `auto_loan` account was categorized Income and counted — but a positive posting
   on a loan/lease is a debt PAYMENT credit, never income.

The user needed a way to reclassify a wrongly-counted deposit from the income
page itself, and loan/lease credits should never count regardless of category.

## Decision

- **Liability postings are never income.** `list_income_categorized_transactions`
  excludes `LIABILITY_ACCOUNT_TYPES` (credit_card, mortgage, auto_loan,
  student_loan, other_liability, 401k_loan). A positive posting there is a debt
  payment credit, so it's dropped from the income pipeline even if categorized
  Income. (Mirrors the suspected-income liability guard.)
- **Recategorize from the income page.** Each counted deposit can be reclassified
  in place — iOS via the shared category picker on the expanded deposit, web via
  a per-deposit category select — calling the existing `updateTransaction`. Moving
  a deposit off the Income category drops it from the rollup, so the user fixes a
  double-counted transfer leg (reclassify to Transfers) in one tap, and the app's
  transfer detection then handles the internal move.

## Invariant

> A positive posting on a liability account never counts as income, whatever its
> category. Any counted income deposit can be recategorized directly from the
> income page; moving it off the Income category removes it from the rollup.

## Rejected

- **Account-level "count income from this account"**: doesn't resolve the
  double-count — a checking account legitimately receives both paychecks (income)
  and RSU transfers (not income), so a per-account switch can't separate them.
- **Fuzzy-match the RSU sale to its later transfer and auto-suppress**: the
  amounts and dates differ; guessing risks dropping real income. The user
  reclassifies the transfer leg explicitly instead.
- **Respect the Income category even on a loan account**: no — a positive loan/
  lease posting is structurally a payment; counting it is always wrong.
