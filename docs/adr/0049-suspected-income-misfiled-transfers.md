# ADR 0049: Flag transfers that are probably misfiled income, and let the user confirm

## Status

Accepted.

## Context

ADR 0048 established that **$262k of this household's inflows were miscategorized
as "Transfers"** and therefore excluded from income — a big enough gap that the
advisor's detected-income figure was useless and we fell back to the declared
compensation profile. That ADR explicitly *rejected* auto-reclassifying transfers
as income, because some transfers really are internal money movement and blindly
counting them would double-count.

But the household shouldn't have to hunt through hundreds of "Transfers" by hand
to find the paychecks. We can single out the likely misfiles cheaply: an internal
move has an **equal-and-opposite leg in a sibling account** within a few days
(this is exactly what `_counterparties` already computes for the source→destination
UI label, and what income detection uses to suppress matched pairs). A sizeable
inflow filed as *Transfer* with **no such leg** is almost always external money
coming in — a paycheck, an RSU sale deposit, a client payment.

The user asked us to auto-detect these, but with a hard requirement: **let me
confirm the value, and until I do, tag it so it's visually identifiable.**

## Decision

Add a **derived** `suspected_income` flag to the `Transaction` schema (no new
column, no migration). A transaction is suspected income when all hold:

- it is an inflow (`amount_minor > 0`) of at least **$200** (`_SUSPECTED_INCOME_MIN`),
- its category is a Transfer (`TRANSFER_CATEGORY_NAMES`),
- it has **no matching internal counterparty leg** (`_counterparties`), and
- the user has **not** already ruled it "not income" (no income-override `exclude`).

The flag is computed wherever transactions are serialized (`GET /transactions`
and `GET /transactions/review`), and a new review kind
`GET /transactions/review?kind=suspected_income` returns exactly the flagged set
for a dedicated review surface.

Resolution reuses existing, already-audited/undoable actions — no new endpoint:

- **Confirm as income** → recategorize the transaction to the Income category
  (`PATCH /transactions/{id}` with the Income `category_id`). It stops being a
  Transfer, so the flag clears and it now counts as income everywhere.
- **Keep as transfer** → record an income-override `exclude`
  (`POST /income/analysis/overrides`). That is precisely the "don't re-flag"
  memory: the detection skips it thereafter.

Both iOS and web render the flag as a badge on transaction rows and host the
`suspected_income` review list with Confirm / Keep-as-transfer actions
(ADR 0025 cross-client parity).

## Invariant

> A sizeable inflow filed as a Transfer with no matching internal leg, and not yet
> ruled on by the user, carries `suspected_income = true` and is surfaced for
> review. Confirming recategorizes it to Income (the real fix); keeping it records
> an `exclude` override that suppresses future flagging. Nothing is reclassified
> automatically — the user always confirms the value first.

## Rejected

- **Auto-reclassify transfers with no leg to Income**: ADR 0048's rejection still
  holds — a genuine external-account transfer (a move to a brokerage we don't sync)
  has no leg either, and silently counting it as income would overstate earnings.
  The user confirms.
- **Persist a `suspected_income` column + a flag-writer run from `autofile_all`**
  (the `flag_possible_duplicates` pattern): rejected as unnecessary. The signal is
  fully derivable from data we already load per request, so a column adds migration
  cost and a stale-flag reconciliation problem for no benefit.
- **A dedicated confirm/dismiss endpoint pair**: rejected. Recategorize and
  income-override already exist, are audited and undoable (ADR 0023), and carry the
  exact semantics we need. A new endpoint would duplicate them and re-litigate undo.
- **Lower the $200 floor**: kept a floor so small stray transfers (Venmo splits,
  ATM) don't clutter the review list; real paychecks clear it comfortably.
