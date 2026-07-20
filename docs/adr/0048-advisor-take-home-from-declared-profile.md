# ADR 0048: The advisor quotes take-home from the declared profile, not detected deposits

## Status

Accepted.

## Context

Asked for a budgeting plan, the advisor told a household their **take-home was
~$[redacted]/month** — absurd against their **declared $[redacted]/yr gross**. Tracing it:

- The advisor quoted `get_income_and_tax`'s `monthly_average_income`, which the
  income analysis computes as **detected recurring deposits ÷ 12**
  (`annual_income = $[redacted]`).
- That undercounts badly here: income *detection* only recognizes regular-cadence
  deposits, so RSU/bonus/irregular pay is missed; the synced history is short (so
  the ÷12 divides a few months of data by a full year); and **a large share of inflows
  were miscategorized as "Transfers"** and excluded. Their *actual* categorized
  income was several times the detected figure.
- Yet the household had **declared a compensation profile** ($[redacted] gross), and
  the tax estimate already computed **$[redacted] tax** on it — a real take-home of
  **~$[redacted]/month** — which the advisor ignored in favor of the noisy deposit
  average.

M73 already made the declared profile authoritative for the *tax* estimate; the
income *figures* the advisor quoted hadn't caught up.

## Decision

**When a compensation profile is declared, it is the authority on income; the
advisor quotes its gross and a derived take-home, never the detected-deposit
average.** In `get_income_and_tax`:

- Renamed the deposit-based figures to `annual_income_detected` /
  `monthly_average_detected` so their nature is explicit, and added an
  `income_basis_note` explaining they undercount (irregular/RSU pay, short
  history, transfer-miscategorized paychecks).
- Added a grounded **`take_home`** (annual + monthly) = gross − estimated total
  tax, from the (profile-based when declared) tax estimate.
- Grounding rules: for what the household earns or takes home, use the profile
  gross and `take_home`; never quote the detected-deposit average as their pay.

## Invariant

> The advisor's statement of what a household earns or takes home comes from the
> declared compensation profile (gross) and the tax-derived take-home, not from
> detected recurring deposits — which are labeled `*_detected` and flagged as an
> undercount. A plan's income basis is never the deposit average when a profile
> exists.

## Rejected

- **Fix income detection to catch RSU/irregular pay**: valuable but hard and
  probabilistic; the declared profile is exact and already the tax authority —
  prefer it.
- **Count "Transfers" inflows as income automatically**: some transfers really
  are internal moves; auto-reclassifying risks double-counting. The miscategorized
  paychecks are a data fix the household makes (recategorize to Income); the
  profile-based take-home makes the advisor correct meanwhile.
- **Drop the deposit figures entirely**: they're still useful signal (and the
  only income basis when no profile is declared) — kept, but clearly labeled.
