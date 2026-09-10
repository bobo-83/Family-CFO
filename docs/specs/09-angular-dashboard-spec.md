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
  A seed from the Overview carries the session key captured BEFORE its request
  started and the context's `household_id`, and is refused when either no longer
  matches — a delayed response must never become another household's currency.
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
  same way, and both the target and the planned-contribution labels name that
  currency. An existing goal is edited in the currency it was declared in: the
  contribution editor labels and sends the goal's own currency.
- Every string is a `$localize`/`i18n` message with `vi` and `lt` entries;
  currency codes are never translated.

## Box-Global Backup Retention (issue #116, ADR 0077)

The Backups page is a system-administrator surface over one box-global stream.
Rename client-local owner concepts to `canManageBackups` and say “Only a system
administrator can manage whole-box backups”; server `BACKUPS_MANAGE`
authorization remains authoritative.

Initial load fetches global configuration, recovery status, local history, and
key status; remote detail may reuse status or the existing remote list. Add the
generated-client-backed `getBackupRecoveryStatus()` service seam and refresh it
after create, configuration save, destination check, local/remote deletion, and
remote-list refresh.

The page adds a **Retention and capacity** card with **On this box** and
**Synology** subsections. Each destination has:

- Tiered / Keep every backup mode;
- numeric “Keep every backup for,” “Keep one per day through,” and “Keep one per
  week through” day fields with `1 <= all <= daily <= weekly <= 3650` validation;
- independent maximum total size and minimum free-space reserve;
- a policy summary such as “Every backup for 3 days · one daily through 14 days
  · one weekly through 90 days.”

Retention/cap/reserve edits are drafts and use one explicit **Save and activate
retention** action with optimistic `updated_at` and confirmation. They are never
auto-saved on blur. Show migrated/restore review and pending-prune count/bytes
before confirmation. Existing cadence/destination autosaves may remain only if
serialized/coalesced and unable to clear pending review. A 409 preserves the
unsaved draft, reloads current configuration separately, and requires deliberate
reconciliation rather than replay.

A **Recovery window** card renders one destination row/card each with configured
target, “Oldest readable backup currently visible,” timestamp-basis caveat,
visible count, nullable exact-readable count, number probed and probe
completeness, coverage, capacity, anomalies, and stable reason text. Required
states distinguish not configured, empty, building, met, incomplete, shortened,
unknown, healthy, constrained, degraded, and unavailable. Unknown capacity says
backups will still be attempted; unavailable inventory never looks empty; every
state says archive integrity and key correctness are checked during restore.

Config/status requests bind authenticated session, monotonic request generation,
and the config token observed at start. Only a still-current completion may
apply. A slow pre-save success/failure cannot replace post-save state, and an
older failure cannot clear newer success. Only a current status failure clears
prior recovery dates and shows “Recovery status unavailable”; existing
create/restore/delete actions remain usable.

Use accessible headings; `role=status` for non-fatal observations and
`role=alert` for constrained/unavailable warnings; every icon/color has text.
Every new `$localize`/template primary string receives Lithuanian and Vietnamese
catalog entries. Tests cover policy mapping/validation, explicit activation,
pending preview and 409 draft preservation, independent destination values,
every state and timestamp caveat, refresh and reverse completion/session
ownership, current-failure stale clearing, accessibility, narrow viewport,
translations, and removal of the obsolete shared cap/“last 7” copy.

## Qualified and Unavailable Aggregates (M124, ADR 0076)

The dashboard regenerates its client from the coordinated OpenAPI contract and
uses shared qualified-money presentation helpers rather than page-local
arithmetic. Overview current/month/year, spending/category totals, savings,
safe-to-spend, outlook, spending plan, Budgets and mutation refreshes, Bills
payment timeline, and Income/Tax all follow these rules:

- Render `QualifiedMoney.value`, including partial zero, and place localized,
  accessible singular/plural omission copy next to every positive count. A
  partial disclosure is a note/status; transport errors remain alerts.
- Render null/unavailable decisions as an em dash or “Unavailable”; remove
  success/danger styling, percentages, progress, checkmarks, and links that
  imply a decision exists.
- Never sum qualified values/counts or derive totals, net, remaining, coverage,
  percentages, running outlook balances, forecasts, or rankings in the browser.
  Category spending consumes `SpendingByCategory.total`; Budgets consumes the
  required server `BudgetListResponse.summary`.
- An incomplete cash outlook may show starting cash, readable events, and
  qualified component leaves, but no running-balance/runway view. A partial
  budget may show spent, but no remaining/percent/status.
- Continue rendering unaffected sibling sections. Aggregate incompleteness in a
  200 response is not a resource error, while a strict operation's documented
  409 is surfaced as unreadable stored data and remains distinct from 423.
- Preserve household/session/request ownership so a late prior-context response
  cannot replace current qualified or unavailable state.

Tests cover count 0, partial nonzero, partial zero, pluralization, unavailable
copy, sibling survival, absence of misleading classes/labels and local
arithmetic, each affected page fixture, generated-client drift, localization,
and accessibility.
