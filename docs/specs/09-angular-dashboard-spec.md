# Angular Dashboard Spec

## Platform

Angular desktop web dashboard running inside Docker.

## Responsibilities

- Reports
- Transaction management
- Statement review
- Imports
- Administration
- Settings
- AI model configuration
- Backup management
- User management

## Information Architecture

Initial sections:

- Overview
- Cash Flow
- Transactions
- Accounts
- Goals
- Reports
- Imports
- AI Models
- Backups
- Settings
- Users

## UX Principles

- Work-focused dashboard.
- Dense but readable information.
- Clear review queues for imports and OCR results.
- Explanations remain attached to calculations.
- No hidden cloud dependencies.

## Generated API Client

Angular client code must be generated from `shared/openapi/family-cfo.v1.yaml`.

## Acceptance Criteria

- Dashboard can guide initial onboarding.
- Dashboard can manage local AI runtime configuration.
- Dashboard can review imports before they affect financial state.
- Dashboard can revoke paired devices.

## Debts & Loans page (M116)

The dashboard counterpart of the iOS loan editor (ADR 0025): lists loan-type
accounts (mortgage, auto, student, 401(k), other) with total-owed and
monthly-payment summaries (401(k) loans excluded — payroll-deducted, owed to
yourself); add/edit/delete; statement scan via `scanLoanStatement` from a file
picker or a pasted image/PDF (ADR 0028, `window:paste` while the form is open);
and the loan's end entered as a date OR "N payments remaining" (M115 — both
store `maturity_date`; the conversion helpers are exact inverses, test-guarded).
A liability's balance is recorded NEGATIVE (the amount owed), matching iOS.

## Goals page additions (M118/M119)

Goals carry an optional planned monthly contribution (create field + per-goal
inline edit) feeding the Overview's "Left to spend this month" savings term
(ADR 0027), and a Delete action (undoable, ADR 0023). Full goal management
exists on both clients (ADR 0025).

## Foreign-currency accounts (M123 follow-up, #156, ADR 0075)

The household has one base currency and every total the API returns is in it.
An account held in another currency is real, listed, counted in no total and
never converted. The dashboard renders that rule; it never re-derives a figure.

- **Overview.** Under the net-worth value, when `HouseholdContext.
  accounts_outside_base_currency` is a non-empty list: "Not counted in {base}
  totals: {name} ({balance}) · …", each balance formatted in the account's own
  currency. `[]` (known, none) and `null` (a past month, unknown) both render
  nothing — neither shows "no foreign accounts" copy. The safe-to-spend card's
  `warnings` already carry the API's generic disclosure sentence.
- **Base currency, loaded and never guessed.** `HouseholdCurrencyService` (root)
  holds the base currency for the current session: seeded by the Overview from
  the context it already loads, fetched once by a page entered directly. Keyed by
  household id and access token, so a logout or a login as another household
  drops it and a late completion for the old session is discarded; single-flight;
  successes only cached, a failure reported and cleared so the next call retries.
  No literal `'USD'` anywhere.
- **Accounts.** The form's currency control is empty and disabled until the base
  currency is known, then set once; a value the user typed is never overwritten
  by a late response, and reset lands on the loaded base. The submit method
  refuses while the currency is unknown (the button is also disabled, but Enter
  submits the form). A row held in another currency carries the chip "Held in
  {currency} · not counted in {base} totals". The reservation total sums
  base-currency reservations only — the Overview's number — and lists a foreign
  reservation under it, unadded.
- **Goals.** A new goal is created in the base currency; the form waits for it the
  same way. An existing goal is edited in the currency it was declared in: the
  contribution editor labels and sends the goal's own currency.
- Every string is a `$localize`/`i18n` message with `vi` and `lt` entries;
  currency codes are never translated.
