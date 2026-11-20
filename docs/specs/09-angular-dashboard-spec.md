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
- Account-only onboarding (issue #162, ADR 0079): Devices and Users must not
  issue/render/copy QR authentication secrets. Devices retains member-attributed
  list/revoke and installation/account sign-in guidance; Users retains account
  invites/roles. Existing sessions remain listed/revocable. Tests cover both
  surfaces and verify no QR creation request is made.

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

## Box-Global Backup Retention (issue #116, ADRs 0077–0078)

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
retention** action with opaque `expected_revision` and confirmation. `updated_at`
is chronology only; the revision-aware client never sends the deprecated timestamp
or falls back to tokenless writes. Show migrated/restore review and pending-prune
count/bytes before confirmation.

Cadence/destination autosaves and activation share one owner/intent-stamped
serialized lane with immutable in-flight snapshots. Exactly one activation may be
visibly queued behind an operational save. A 409 or transport ambiguity preserves
the draft and same-owner activation, reloads current configuration separately, and
requires deliberate reconciliation rather than replay. Reconciliation has
refreshing, refresh-failed, and ready phases; duplicate retry/confirmation is
coalesced or disabled. A 422 requires correction and new confirmation, 401/403
requires authentication/permission recovery, and 428 is a client/contract fault.
Session, presentation, restore, or full-reload replacement discards old consent
and password snapshots.

Schedule, Synology destination, and retention feedback renders immediately beside
the initiating control using localized `role=status` success/information and
`role=alert` failures. New edits cannot clear unresolved reconciliation. The
schedule picker noun is **Backup** while the immediate action remains **Back up
now**.

A **Recovery window** card renders one destination row/card each with configured
target, “Oldest readable backup currently visible,” timestamp-basis caveat,
visible count, nullable exact-readable count, number probed and probe
completeness, coverage, capacity, anomalies, and stable reason text. Required
states distinguish not configured, empty, building, met, incomplete, shortened,
unknown, healthy, constrained, degraded, and unavailable. Unknown capacity says
backups will still be attempted; unavailable inventory never looks empty; every
state says archive integrity and key correctness are checked during restore. A
degraded destination whose reasons are only `retention_review_required` and
optional `coverage_unknown` presents **Review required** while diagnostic and
accessible copy retains the underlying API status. Any additional reason restores
the actual fault badge. Overall may use that presentation only when the API is
actually degraded, at least one configured destination is review-only, and every
configured non-healthy destination is review-only.

Config/status requests bind authenticated session, presentation/queue epoch,
intent identity, monotonic request generation, and the revision observed at start.
Ownership is stamped at enqueue time. Only a still-current completion may apply. A slow pre-save success/failure cannot replace post-save state, and an
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

## Reauthentication (#164)

A current expired or rejected session tears down the active protected outlet
and replaces the protected URL with sign-in. The sign-in reason is separate
from form errors. Check child activation and guard async work by session owner.
See [ADR 0080](../adr/0080-session-expiry-reauthentication.md).

## AI runtime "Find a model" search results (issue #168)

A submitted query renders a **labeled, query-restricted results view** — not
another contribution to the browse universe. Browse behavior is unchanged
except where stated. See the plan at `docs/plans/ai-runtime-model-search-2026-11-19.md`
and its critique at `docs/reviews/ai-runtime-model-search-plan-critique-2026-11-19.md`.

### State and ownership

- `searchQuery` is the editable **draft**; `submittedQuery` is the trimmed
  submitted text (`null` = browse mode). Editing a nonempty draft never
  relabels or replaces displayed results. The state, not the draft, selects
  search mode.
- One shared request generation (`liveGeneration`) covers search and browse
  fetches. Submit, quick filter (including `all`), clear, and destruction
  invalidate outstanding work even when they start no replacement request. A
  completion commits only when its generation is current and the component is
  alive; there is **no unconditional `finally`**. Logical cancellation only —
  superseded requests may finish in transport and are discarded.
- Results are owned per destination: `searchResults` (current query's accepted
  response) and `browseLiveResults` (last accepted quick-filter response).
  Search responses never write browse state and vice versa.
- **`browseLiveResults` lifecycle:** retained across quick-filter switches and
  while a replacement fetch loads (today's behavior); cleared on that fetch's
  total failure so the curated fallback is honest. Search exit restores the
  previous quick filter without refetching.
- Merged-universe computeds (`knownModels`, `browseModels`, search candidates)
  read `catalog`/`config` through a guarded accessor: an errored resource
  degrades to "no curated/configured entries", never a render-time throw.

### Interaction contract

| Trigger | Behavior |
|---|---|
| Nonempty draft edit | Draft only; results and heading unchanged. |
| Draft empty/whitespace (incl. native search-input clear) | Immediately clear search; no request. |
| Submit nonempty draft | Allowed while loading (newest wins). Identical trimmed in-flight query is a no-op (trim-only identity); any other query supersedes. |
| Clear search (button or empty submit) | Invalidate pending work, exit search, reset window, restore browsing **without fetching**, return focus to the input. |
| Quick filter (incl. `pickPhotoModelNext()`) | Exit search, clear draft/query results, invalidate pending work, then existing browse behavior (`all` fetches nothing). |
| Role / fit-only / sort change | Recompute locally; reset window; never invalidate a pending request. |
| Cluster toggle / peer reachability | Recompute eligibility and fit in place; keep the query and candidates; reset window. |
| Apply from a search row | Does not clear or reset the search state; status-driven stub changes recompute candidates in place. |
| Destruction | Invalidate generation; existing polling cleanup unchanged. |

### Matching, ranking, pagination

- Match = literal contiguous substring (case-insensitive) of the trimmed query
  in the full repo `id` **or** `label`. No regex, token expansion, fuzzy
  matching, or matching on `notes`. Stubs qualify only by **id**.
- Candidates: current HF response (server order), then curated entries, then
  matching configured/served stubs. Dedupe by case-insensitive id, first
  position wins, curated metadata overrides a live duplicate at that position;
  nothing is synthesized from query text.
- Eligibility: cluster inactive → exclude `min_nodes >= 2`; oversized
  `min_nodes == 1` models stay visible with their honest single-box verdict.
- Role/fit-only/sort facets apply to search via the shared helper (neutral
  `all` quick filter). Exact-first pin: the single case-insensitive full-ID
  equality match moves to index 0 **only if it survived eligibility and
  facets**; the pin overrides explicit sorting. A filter-excluded exact match
  is never reinserted and produces the standard no-match message (no special
  branch).
- One ordered paginated list mixing cluster and single-box rows (cluster rows
  chosen by fragment, not a separate section). Pagination is the mode-aware
  reuse of `visibleModels`/`hiddenCount`; the exact match is visible without
  "Show more".

### Presentation states

- Search heading: **Search results for "{submittedQuery}"** (`aria-labelledby`;
  query via interpolation, never HTML). Clear-search control usable while
  loading; the Search button label no longer reflects `searching()` — progress
  lives in the result status, and the button stays enabled for supersede
  (disabled only for a whitespace-only draft or the identical in-flight query).
- The browse quick-filter toolbar and the browse loading/error lines are
  scoped to browse mode; the shared `searchError` alert renders per mode.
- Loading: "Searching Hugging Face…" (`role="status"`, `aria-busy`); no old
  rows, no no-match message.
- Success with rows: result count + one paginated list; full repo ID visible
  in every collapsed row (both modes, shared fragments).
- Success, zero displayable rows: "No models match '{query}' with the current
  filters." — no error alert, no browse rows underneath.
- **Search failure:** `role="alert"` with dashboard-owned copy ("Could not
  search Hugging Face. Search again to retry.") — the endpoint's only non-auth
  failure is a 503 whose server detail is browse-phrased ("showing curated
  models only") and must not surface in search mode; unexpected statuses keep
  the structured pass-through. With matching local candidates (curated/active
  entries), also show "Showing matching catalog and currently configured models
  only." and render those filtered, ranked matches; without them, no rows and
  **no** no-match message (remote absence was not established).
- **Browse failure:** clear `browseLiveResults`, keep curated + stubs, show the
  browse-specific fallback line (stale live results are never mislabeled as
  curated).
- New copy is marked with the existing `i18n`/`$localize` conventions; new
  styles are minimal (heading, wrapping repo id, cluster annotation).

### Non-goals

- #167 apply flow (Apply must keep working from search rows, nothing more).
- No backend, OpenAPI, generated-client, database, persistence, URL-state,
  iOS, or auth changes; no exact-ID lookup endpoint; no auto-retry, timeout
  policy, or local cache; no new service or child component.
- Advisor tool access is explicitly out of scope: this changes an existing
  operator-facing model picker, not a household data domain.

### Regression coverage (acceptance criteria)

1. Exact `owner/model` ranks first (mixed-case/padded input, explicit sort,
   filter-excluded exact match not reinserted, full ID visible collapsed).
2. Unrelated curated cluster recommendations stay out; cluster on/off/peer-
   unreachable consistency; matching cluster rows keep combined fit + Apply
   (`cluster: true` payload).
3. Loading / no-match / HF failure are distinct; stale responses and cleared
   queries cannot repopulate; clearing restores browsing without a request.
4. Race matrix: supersession in both directions, identical in-flight dedupe,
   search↔browse crossovers, `all` invalidation, destruction, errored-resource
   degradation (failed `listAiModels`/`getAiRuntimeConfig` must not throw).
