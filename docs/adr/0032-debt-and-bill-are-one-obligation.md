# ADR 0032: A debt modeled as both an account and a bill is one obligation

## Status

Accepted.

## Context

A user reported the U.S. Department of Education student loan appearing **twice**
on the Bills tab: once as an explicit **bill** ("Department of Education",
monthly, with a real due date and a matched payment) and once as a **liability
account** obligation ("U.S. Department of Education", surfaced under
[ADR 0024](./0024-bills-payment-timeline.md)'s "loans are first-class" rule). The
account copy also showed **"Due date unknown"** — `_account_payments` only infers
a due day from payment-labeled inflows *on the loan account itself*, and a
manually-added loan has none.

The two are the same real obligation. Beyond the confusing double-listing, the
same payment was reserved **twice** in the money math: `compute_safe_to_spend`
counted it as a bill (`bills_due`) *and* as a minimum debt payment, and the month
spending plan counted it as both a timeline bill and an account obligation.

## Decision

**When a liability account's recurring payment is also modeled as an explicit
bill, they are one obligation. The bill is authoritative and the derived account
obligation is suppressed on every bill-facing surface; the debt is counted once.**

- A single helper `bill_covered_account_ids(bills, liability_accounts)` matches an
  account to a bill by **fuzzy merchant key** (the same `_keys_match` the timeline
  uses — `"department of education"` matches `"u s department of education"`) **and
  amount within ±30%** (`_MATCH_AMOUNT_TOLERANCE`). Requiring an amount match keeps
  two distinct debts to the same creditor from collapsing into one.
- `recurring_liability_obligations` drops covered accounts — which removes the
  duplicate from the **payment timeline**, **Manage bills**, and the **month
  spending plan** (all three read obligations through it).
- `compute_safe_to_spend` marks covered accounts as *modeled* (so they aren't
  warned as "no minimum recorded") but does **not** subtract their minimum again —
  they are already reserved via `bills_due`. `SAFE_TO_SPEND_HORIZON_DAYS` (30)
  spans a full monthly cycle, so a monthly debt's bill is always in-window.

The **bill wins** because it already carries the stored due date and matches the
real charge, so the surviving row has a due date — fixing both the duplicate and
the "Due date unknown". The liability **account is untouched**: it still lives in
Accounts / Debts for payoff and net worth. Deleting the bill re-surfaces the
account obligation, which is the correct self-healing behavior.

## Invariant

> A liability that is also set up as an explicit bill is represented exactly once
> — as the bill — on every surface that lists or reserves payments (timeline,
> Manage bills, month spending plan, safe-to-spend). It is never shown twice and
> never reserved twice.

## Rejected

- **Keep the obligation, suppress the bill.** Would hide user-authored data (a
  bill you can edit/delete) in favor of a derived row, and would still need new
  due-date inference for the loan to not read "Due date unknown". Suppressing the
  *derived* duplicate is less surprising.
- **Merge into one synthetic row** combining the account's identity with the
  bill's due date. Two source entities with different IDs make tap-through, edit,
  and undo ambiguous on the clients for no user benefit.
- **Fix only the display, leave safe-to-spend double-counting.** Same root cause;
  leaving the money wrong while fixing the list would be a known, silent defect.
- **Do nothing / tell users not to do both.** The app auto-detects bills from
  recurring charges, so it creates this collision itself.
