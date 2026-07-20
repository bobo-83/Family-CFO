# ADR 0043: The advisor can read debt over time, not just now

## Status

Accepted.

## Context

A member asked the advisor for their **average debt over their lifetime**. It
couldn't answer: `get_debt_outlook` is a point-in-time snapshot (current
balances only), `net_worth_snapshots` stores a single net-worth number with no
debt breakdown, and the study insights are single-month. So any multi-month or
"average/trend" debt question either got declined or, worse, got the *current*
total mislabeled as an average.

A second, honest wrinkle surfaced while diagnosing it: the household had ~3
months of **transactions** (imported history) but only ~10 days of recorded
**balances** (daily bank-sync balances began when the accounts were linked).
"10 days" and "90 days" are two different histories, and the earlier answer
conflated them.

## Decision

**Add a `get_debt_history` tool that returns total debt at each month-end across
the transaction window, plus the average and the number of months covered.**

- Debt per month is **reconstructed** (`finance_service.reconstruct_debt_total`)
  the same way net worth already is: today's liability balances minus the
  liability-account transactions posted since that month-end. Approximate before
  daily balances began, exact after — and stated as such in the tool's `note`.
- The series spans only the months of data that exist; the tool returns
  `months_covered` and its description tells the advisor to say "over the N
  months I have" rather than imply a longer record. This keeps the advisor
  honest about a short history instead of inventing a "lifetime" average.
- No new snapshot mechanism: the per-account `account_balances` rows are already
  written daily by bank sync, so debt is *already* snapshotted going forward —
  the gap was purely the tool to aggregate it over time, not the capture.
- `get_debt_outlook` stays the current-snapshot tool; `get_debt_history` is the
  multi-month one. Their descriptions route the model to the right one.

## Invariant

> Any "average / over time / trend" question about debt is answered from
> `get_debt_history`, whose window is bounded by real data and reported via
> `months_covered`. The advisor never presents a current snapshot as a
> historical average, and never implies more history than exists.

## Rejected

- **A new `liabilities_minor` column on `net_worth_snapshots`, snapshotted
  daily**: the family asked for daily debt capture, but `account_balances`
  already records every account's balance daily — a debt column would duplicate
  it. Reconstruction covers the pre-capture months a column never could.
- **Reporting a "lifetime" average without bounding it**: with two weeks to a
  few months of data, an unqualified "lifetime average" misleads; `months_covered`
  forces the honest framing.
- **Per-account balance-history reads for each month**: reconstruction from
  current balance + transactions is one uniform method across the whole window,
  including months before any balance was recorded.
