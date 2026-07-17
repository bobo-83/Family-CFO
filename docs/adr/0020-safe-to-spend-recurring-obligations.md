# ADR 0020: Safe-to-spend recurring obligations & subscription forecast (M106/M109)

## Status

Accepted. The lease/loan reservation and the Bills-tab obligations view are shipped
(M106); the subscription forecast is in progress (M109). Extends ADR 0003
(deterministic finance) and relates to the Bills model.

## Context

Safe-to-spend = liquid cash − emergency fund − bills due − minimum debt payments −
credit-card balances. It is a **snapshot**: cash you have *now*, minus what is
*already committed*. It does **not** forecast future discretionary spending or
income. Two gaps surfaced:

1. **Leases weren't reserved.** Car leases are `auto_loan` accounts with a recorded
   monthly payment but a **$0 balance** (a lease has no payoff balance). The old
   minimum-debt logic only counted liabilities with an *outstanding balance*, so it
   silently skipped ~$1,040/mo of real lease obligations — safe-to-spend was
   overstated.
2. **Subscriptions aren't reserved at all.** Recurring charges (Disney+, Apple,
   Arlo) are categorized as *spending*, not set up as *bills*, so they are counted
   as past spend and never forecast as an upcoming commitment.

## Decisions

1. **Reserve every recurring liability payment exactly once.** `minimum_debt_payments`
   counts each liability's recorded monthly payment whether or not it has a payoff
   balance — a **loan** (pays down principal) *and* a **lease** (pays down nothing).
   Credit cards are excluded (their whole balance is the "cards" line); 401(k) loans
   are excluded from the subtraction (payroll-deducted — already in take-home pay).
   `finance_service.recurring_liability_obligations` is the single source, used by
   both the reservation and the Bills-tab display.
2. **The Bills tab shows all recurring obligations** (loans, leases, payroll-deducted
   loans) with a one-line **balance-sheet note** each, alongside actual bills — but
   each obligation is reserved in safe-to-spend **once**: bills → `bills_due`, account
   payments → `minimum_debt_payments`, cards → cards line. The Bills tab only
   *displays* the account-based ones; it never adds a second reservation.
3. **Subscriptions are reserved "the bill way", never as a monthly total.** For each
   recurring subscription, reserve **only its next upcoming charge that falls within
   the safe-to-spend horizon and has not been paid this cycle** — exactly how
   `bills_due` treats bills. It is shown as a heads-up drill-down; it does **not**
   create Bill records (keeps the Bills tab uncluttered).

4. **A payment tracked as a loan/lease account is not suggested as a bill (M110).**
   Bill detection scans every checking/credit-card outflow, so a recurring
   loan/mortgage/lease payment would be offered as a *bill* even though its monthly
   payment is already reserved via minimum-debt-payments — nagging the user to
   double-model the same obligation. A monthly suggestion whose amount matches a
   liability account's recorded monthly payment (within a small rounding tolerance)
   is therefore excluded from suggestions. This is the same "reserved once"
   invariant applied to the *suggestion* surface. (Filing it as a Bill instead is
   also valid and silences the suggestion by name-match — the two models are
   mutually exclusive; never both, or safe-to-spend reserves it twice.)
5. **Same-named bill category and obligation section render as one (M110).** The
   Bills tab groups hand-entered bills by category name and shows account
   obligations in their own titled sections; when a user category (e.g. "Loans")
   shares a title with an obligation section, they merge into a single section
   rather than two identically-headed ones.

## Why not a naive monthly subscription total (the double-count trap)

A past subscription charge has **already left liquid** — it's why cash is lower, and
it shows in Spending; safe-to-spend does **not** re-subtract it. Subtracting "the
monthly subscription total" would re-reserve charges that already hit this cycle —
a real double-count. Reserving only the **next-in-window** charge counts a *future*
outflow that hasn't happened, so it never double-counts the current cycle.

## Annual / sparse subscriptions → use a Bill, not the auto-forecast

The auto-forecast is inference from history: a **monthly** subscription needs 3
consistent sightings; an **annual** one (e.g. an Arlo camera plan, ~$229/yr) can't
be inferred until it has been seen **twice ~a year apart**, and even then its next
charge is usually **outside the 30-day safe-to-spend horizon**, so it is correctly
**not** reserved *now* (a charge due in 9 months is not committed this month).

For a known recurring expense the app cannot yet infer — an annual subscription
seen once, or any irregular one — the right mechanism is a **recurring Bill** with
the correct frequency + next-due date: it appears on the Bills tab as a tracked
obligation, and safe-to-spend reserves it (via `bills_due`) once its charge falls
within the horizon. The auto-forecast handles the *frequent, inferable* case; Bills
handle the *known-but-not-inferable* case. They never both reserve the same charge
(a Bill's merchant is excluded from the forecast's category scan by amount/cadence,
and each is reserved once).

## Invariant (prevents recurrence)

> **Every recurring commitment is reserved in safe-to-spend exactly once. Never
> subtract a charge that has already left liquid — reserve only the next occurrence
> that falls within the horizon and is not yet paid.**

## Rejected

- **Subtract the monthly subscription total** — double-counts already-paid charges in
  the current cycle.
- **Forecast all future spending/income** (groceries, paychecks) — turns the snapshot
  into a cash-flow projection; out of scope for safe-to-spend (that's a separate view).
- **Turn subscriptions into Bills** — also correct, but clutters the Bills tab; the
  forecast is kept as an informational-but-reserved drill-down instead.
