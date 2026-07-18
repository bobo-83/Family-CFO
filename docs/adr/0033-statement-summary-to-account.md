# ADR 0033: Read a loan/card statement's summary onto the account

## Status

Accepted.

## Context

A user asked why their student loan showed **"Due date unknown"** on Bills even
though they had uploaded its statement — the due date is printed right on it.
Two gaps:

1. **Statement import only extracted transactions.** `import_processing` walked
   the statement text for line items ("date, payee, amount") and created pending
   transactions. It never read the account **summary** a loan/card statement
   prints — payment due date, minimum payment, new balance.
2. **A liability account had nowhere to store a due date.** The `accounts` table
   held interest rate, minimum payment, and `maturity_date` (final payoff) — no
   *next payment* due date. So the timeline could only **infer** a loan's due day
   from a payment-labeled inflow on the account, which a manually-added loan has
   none of ([ADR 0024](./0024-bills-payment-timeline.md)).

## Decision

**When a statement import is tied to a liability account, read its summary onto
that account, and let the timeline use a stored due date directly.**

- New nullable `accounts.next_payment_due_date` column (migration `0059`).
- `parse_statement_fields(text)` extracts, conservatively and independently, the
  statement closing date, payment due date, minimum payment, and new balance with
  label-anchored patterns. A statement showing none leaves the account untouched —
  a wrong value is worse than none.
- `apply_statement_fields_to_account` (called from `_process_pdf`): the due date
  and minimum payment update the account row; the new balance is recorded as a
  **negative** balance (a liability owes) dated by the statement's **closing
  date**, so an out-of-order upload can't clobber a newer balance
  (`list_account_balances` keeps the latest by `as_of`). Assets and account-less
  imports are ignored.
- `payment_timeline` / `_liability_item`: a **stored due date is authoritative**
  (the day is known, not guessed). Order becomes stored → inferred from payments →
  undated. A recent payment still marks the row paid. This is what makes the loan
  show a real due date. The date can also be set by hand (a future account-edit
  surface), same column.

The change is **server-side**: the clients already render the timeline due date,
account balance, and minimum payment, so an imported statement reaches both iOS
and web with no client change. This stacks with [ADR 0032](./0032-debt-and-bill-are-one-obligation.md):
if a debt also has a matching bill, the bill still wins; the stored due date
rescues loans that don't have one.

## Invariant

> A liability account can carry a next-payment due date sourced from its statement
> (or set by hand). When present, the Bills timeline shows that exact day rather
> than inferring one or reporting "Due date unknown". A statement never overwrites
> a newer balance and never invents a field its text doesn't contain.

## Rejected

- **Infer the due date from the checking-side payment instead.** The payment
  that clears a loan lands on checking, not the loan account, and its date is the
  *pay* date, not the *due* date; the statement states the due date exactly.
- **Structured extraction in the OCR worker.** The statement-line heuristics
  already live in `import_processing`; keeping the summary parser beside them is
  simpler than widening the OCR adapter contract. Can move later if reused.
- **Reuse `maturity_date`.** That is the loan's final-payoff date — a different
  fact from the next monthly due date; conflating them would break payoff math.
