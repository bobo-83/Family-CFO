# ADR 0042: Debt interest rate is stored as a decimal fraction

## Status

Accepted.

## Context

A member asked the advisor to plan paying down their debt. The advisor replied
that "all 4 modeled debts are in interest-only mode — your payments aren't
reducing the principal" and asked the user for their smallest balance and its
minimum payment — **data the app already stores** (4 debts, each with balance,
rate, and minimum payment). That is exactly the duplicate-input the app is
supposed to avoid.

Two defects compounded:

1. **The debt tool hid the data.** `get_debt_outlook` returned only aggregate
   counts and totals, never the per-debt balance/rate/minimum, so the advisor
   had no stored numbers to reason with and fell back to asking.
2. **The interest rate was stored in the wrong unit.** The engine
   (`calculate_debt_payoff`) and every advisor rate field treat a rate as a
   decimal **fraction** (`monthly_rate = annual_interest_rate / 12`, so 0.06 =
   6%). But both clients treated `accounts.annual_interest_rate` as a
   **percentage**: they display it with a "%" suffix and stored the raw
   typed/scanned value (9.5 for 9.5%). The engine then read 9.5 as 950% APR, so
   every payment was smaller than the monthly interest → no debt could ever
   amortize → all "interest-only" → payoff unmodelable. The contract left the
   field undescribed, which let the two representations drift apart. The demo
   fixtures happened to be hand-written as fractions, hiding the bug in tests.

## Decision

**`annual_interest_rate` is a decimal fraction everywhere — 0.06 means 6% APR —
matching the engine, the advisor tools, and `annual_return_rate`.**

- **Contract**: the field now documents "decimal FRACTION, not a percentage;
  clients divide the entered percent by 100 before sending."
- **Clients** convert at the boundary: web and iOS keep working in percent in
  the form/display (what users understand), but divide by 100 on send and
  multiply by 100 on load. The scanned `apr_percent` stays a percent through the
  form and is converted on save like any typed value.
- **Migration 0062** repairs existing data: any stored rate `> 1.0` is a
  mis-stored percentage and is divided by 100 (no real APR is ≥ 100% as a
  fraction); rates `≤ 1.0` are already fractions and left alone.
- **`get_debt_outlook`** now returns a `debts` array with each debt's name,
  type, balance, rate, minimum payment, and an `interest_only` flag; its
  description and `debt_payoff`'s instruct the advisor to read these and feed
  them straight into a payoff calc — never to ask the user for a figure the tool
  returns.

## Invariant

> Any rate crossing the API (`annual_interest_rate`, `annual_return_rate`, …) is
> a decimal fraction. Clients convert to/from percent only for display. The
> advisor reads stored debt terms via `get_debt_outlook` and never asks the user
> for a balance, rate, or minimum the app already holds.

## Rejected

- **Make the engine treat the rate as a percentage** (divide by 100 in the
  engine): would split the meaning of "rate" across the engine — debt rates in
  percent, investment-return rates in fractions — and force migrating the
  correct fixtures instead of the incorrect real data.
- **Only expose the debts in the tool, leave the unit bug**: the advisor would
  read the numbers but the payoff engine would still compute 950% APR and refuse
  to model — it would stop asking, then give wrong or "impossible" answers.
- **A heuristic that divides every rate by 100**: would corrupt the fixtures and
  any already-correct fraction; the `> 1.0` guard converts only the mis-stored
  rows. A genuine sub-1% APR entered as a percent ("0.5") is indistinguishable
  and left as-is — rare, and re-entering it once corrects it.
