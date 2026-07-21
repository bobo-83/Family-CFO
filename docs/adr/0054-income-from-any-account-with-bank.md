# ADR 0054: Income-categorized deposits count from any account, and show their bank

## Status

Accepted. Extends ADR 0053 (Income category counts as income).

## Context

ADR 0053 made an Income-categorized deposit count — but only got half the money.
The detection query, `list_income_detection_transactions`, is scoped to
**checking accounts** (`accounts.type == "checking"`). RSU and ESPP income lands
in a **brokerage** (here: Broadcom vests sold into a Charles Schwab account), so
those deposits never entered the pipeline at all, even filed under Income. On
real data the household's annual income read **$106,766** when the true detected
figure — with the brokerage RSU deposits — was **$222,917**.

Separately, the income-source evidence showed the account name but not the
**bank**, so a deposit couldn't be recognized as "the Schwab RSU sale."

## Decision

- **Income-categorized inflows are pulled from ANY account type.** A new
  `repository.list_income_categorized_transactions` selects Income-filed inflows
  regardless of `accounts.type`, and `recurring_income_candidates` merges them
  into the transaction set (deduped) in addition to the checking-account rows —
  so brokerage RSU/ESPP deposits are detected, counted, and shown.
- **Each deposit carries its bank.** `IncomeAnalysisTransaction` gains
  `institution`, populated from the account's linked bank, and both clients show
  it in the per-deposit evidence.
- **iOS shows each income source expandably**: tap a source to see its deposits,
  tap a deposit to see the evidence — date, payer, **bank**, account, and the
  bank memo (which is where an "RSU"/"ESPP" note appears). Previously the iOS
  income view was a flat, non-expandable summary row.

## Invariant

> A positive deposit filed under Income counts toward income regardless of which
> account type it landed in (checking, brokerage, …). Each counted deposit is
> shown with its bank and account so it is recognizable, and the source's
> individual deposits are inspectable on both clients.

## Rejected

- **Widen the base detection query to all account types**: no — auto-detecting
  income across a brokerage's buys/sells/dividends would be noisy. Only deposits
  the user *explicitly* filed under Income are pulled from non-checking accounts.
- **Infer RSU vs ESPP automatically**: not reliable from the data; instead the
  merchant, bank, account, and raw bank memo are surfaced so the user sees the
  source for themselves.
- **Add institution to account creation**: unrelated; the bank comes from the
  synced account (SimpleFIN), and a manually-added account simply has none.
