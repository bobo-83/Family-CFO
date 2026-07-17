# ADR 0024: Bills tab redesign — the payment timeline (M111)

## Status

Accepted. Extends ADR 0020 (recurring obligations) and the Bills model. iOS +
API shipped; the Angular dashboard's bills view is a pending follow-up.

## Context

The Bills tab was organized by what things *are* — bills grouped by spending
category, then account-obligation sections — not by what needs *paying*. Credit
cards, the household's largest recurring payment event (paid in full monthly),
appeared nowhere on the page; they were only a subtraction inside safe-to-spend.
The user: "When we pay bills we pay everything that's due" — the page didn't
answer "what do I owe, when, and am I on top of it?"

## Decisions

1. **The unit is the payment, not the merchant; the axis is time, not category.**
   The tab's primary view is one list — bills, credit-card payments, loan and
   lease payments — grouped **Overdue → Due soon → No due date → Paid this cycle
   → Upcoming**, under a headline of *what's due in the window vs cash on hand*.
   Category grouping moved one level down ("Manage bills"), where add / edit /
   categorize / delete and the balance-sheet notes live.
2. **Paid status is derived from real transactions, never hand-marked.** A bill
   matches its actual charge by normalized-merchant (fuzzy: equal, substring, or
   token-subset) + amount within ±30% + a window tied to the specific occurrence.
   Every "Paid" row carries the matched transaction (`paid_with`) — the receipt
   behind the checkmark, always verifiable.
3. **A payment only counts for the occurrence it sits next to.** Matching across
   a whole cycle would let last month's charge mark tomorrow's as paid. If a due
   date passed within its grace period (per-frequency, e.g. 10 days for monthly),
   the payment must sit near *that* date or the bill is **overdue**; otherwise
   only an early payment near the *next* occurrence counts. After grace expires
   unmatched, the claim is dropped (rolled forward), not left accusing.
4. **Variable-amount bills (utilities): fixed due day, estimated amount.** The
   bill's stored amount is the *typical* charge (detection's median); matching
   tolerates ±30% and the paid row reports the **actual** amount. The existing
   drift suggestions (M59) keep the estimate current. No new "variable" flag.
5. **Credit cards are first-class payables; their due day is inferred, and
   inferred dates are never flagged overdue.** The amount is the pay-in-full
   current balance (conservative — statement balances aren't available from
   SimpleFIN). The due day comes from payment-labeled inflows ("payment",
   "autopay"…) on the card's own account, rolled monthly; refunds don't count.
   No history → "No due date yet", never a guess. Loans/leases infer the same
   way; payroll-deducted 401(k) loans stay off the timeline entirely (they never
   claim checking cash, ADR 0020).
6. **One new read-only endpoint** — `GET /bills/timeline` (`getPaymentTimeline`)
   built from existing data; no schema migrations, no new mutations (nothing new
   to undo under ADR 0023).

## Invariant

> A "Paid" checkmark exists only with a matched real transaction attached, tied
> to the specific occurrence it settled. An inferred due date is never reported
> overdue. Every timeline amount is either a stored figure (bill estimate,
> minimum payment) or the current balance — never a guess.

## Rejected

- **Hand-marking bills paid** — busywork the data can do; drifts from reality.
- **Statement-balance card rows** — SimpleFIN doesn't provide them; showing the
  current balance is conservative and honest (matches the safe-to-spend card
  treatment, M96).
- **Asking the user for each card's due day** — inference from payment history
  covers it (minimize duplicate input); a one-time ask remains an option if a
  card's history is too thin.
- **Flagging inferred dates overdue** — inference isn't strong enough evidence
  to accuse a missed payment; only stored bill due dates can go overdue.
- **Keeping category grouping primary** — it answers "what do I spend on," not
  "am I on top of my payments"; it stays, one level down.
