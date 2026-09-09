# ADR 0076: Qualify readable monetary leaves and make incomplete decisions unavailable

## Status

Accepted (2026-09-08, issue #119). Refines ADR 0072's strict unreadable-sealed-
amount behavior for read-side aggregates and follows ADR 0075's precedent of
returning a useful, explicitly limited result without hiding an exclusion.
Implementation is owned by the ordered work in
`docs/plans/incomplete-monetary-aggregates-2026-09-08.md` and must not begin
before this ADR and the Spec Kit gate are accepted.

## Context

A sealed transaction or card-statement amount that decrypts to a non-integer is
corrupt. The strict decoder raises `SealedAmountUnreadableError`, and affected
operations return HTTP 409 `sealed_amount_unreadable`. This prevents the prior,
dangerous behavior of replacing an unreadable amount with zero.

That strict behavior is correct for raw records and operations whose result
cannot honestly survive a missing amount. It is unnecessarily destructive for
an additive read-side aggregate: one unreadable stored cell currently prevents
an entire household context, spending view, cash-flow view, or other response
from preserving readable sibling totals. Conversely, simply summing the
readable rows and continuing every calculation would turn an incomplete input
into plausible but unsafe decisions about affordability, coverage, tax,
budgets, savings, or runway.

The API therefore needs one reusable contract that distinguishes a descriptive
partial leaf from a decision that cannot safely be made, while retaining strict
refusal wherever no honest partial representation exists.

## Decision

### 1. Qualify additive leaf totals

Every affected additive monetary leaf is represented on the wire as:

```text
QualifiedMoney
  value: Money
  incomplete_count: integer >= 0
```

`value` is the deterministic sum of readable contributors only. A positive
`incomplete_count` means selected stored amount cells were omitted; it is not a
lower bound because an unreadable refund or inflow could move the exact result
in either direction. Exact zero/count 0 means no selected contributors; zero
with a positive count is a partial zero and must be presented as such.

Qualification is field-local. Unaffected response sections remain available,
and ADR 0075 foreign-currency exclusions remain a separate disclosure rather
than being merged into `incomplete_count`.

### 2. Make dependent decisions unavailable

A safety-, recommendation-, or decision-shaped output is not calculated when
an unreadable dependency could change its answer. Monetary decisions use
`QualifiedMoney | null`; a non-null decision is complete and therefore has
count 0. Non-money decisions use nullable fields or explicit `unavailable` /
`unknown` enum states.

In OpenAPI 3.1, primitive and array nullability uses a `type: [T, "null"]`
union. A nullable reference points to an exact `Nullable*` copy whose top-level
type includes `"null"`; the pinned Swift OpenAPI Generator 1.10.3 maps that
copy back to the existing generated Swift type. This avoids the unsupported
standalone `{ type: null }` branch without using `$ref` siblings, which would be
an intersection and would not actually admit JSON null. The containing schema's
`required` array remains authoritative: required nullable fields cannot be
omitted, while optional nullable fields may be omitted or explicitly null.

Non-additive detection, matching, ranking, cadence, median, pairing, and
projection operations use a colocated
`ComputationAvailability { status: complete | unavailable, incomplete_count }`.
When unavailable, independently declared facts may remain, but inferred rows,
rankings, forecasts, statuses, percentages, and recommendations are suppressed.
No API, client, advisor, persisted calculation, widget, or worker may reconstruct
a null decision from partial components.

### 3. Count storage-cell identities, never child counts

Internally, an unreadable source is the immutable request-local identity
`(household_id, table, row_id, column)`. Aggregate composition unions source
sets and derives the count from set cardinality; counts are never added because
the same stored cell may reach multiple branches.

Apply household, date, currency, account, category, and other non-amount
predicates before decoding whenever possible. Count a cell only when it passes
those predicates and could affect that field. If eligibility depends on its
unreadable sign, treat it conservatively as potentially relevant. Metadata-
preserving candidates must survive until matching, grouping, transfer exclusion,
or attribution is complete; an unreadable candidate is never materialized as a
zero-valued domain record.

For card aggregates, choose the newest eligible statement using non-amount
metadata before decoding its authoritative balance. A corrupt balance never
falls through to an older statement or running balance. Corruption in an unused
minimum-due cell does not affect balance-only aggregates.

### 4. Keep strict boundaries strict

The existing strict amount decoder remains unchanged. Aggregate tolerance is a
separate, explicit cell boundary that catches only
`SealedAmountUnreadableError`. `HouseholdLockedError` remains HTTP 423, and
database, crypto, cancellation, and unexpected failures propagate normally.

HTTP 409 `sealed_amount_unreadable` remains the contract for transaction
list/detail, the card-statement list and card-statement mutation/undo pre-reads,
raw exports and
indexing inputs, equality/dedupe/sign/range filters, write-side comparisons,
and unstable candidate or ranking products with no independently useful
section. Strict background consumers isolate the known failure to one
household/job and retry on a later scheduled run; they never invent zero.
Previously durable output is preserved except for transaction vectors confirmed
stale by corruption: those points are removed because their payload can expose an
amount that storage can no longer read. Locked-household output remains preserved.

### 5. The server owns composition; clients preserve parity

The API owns all financial composition. It adds server-authored totals wherever
clients currently recombine fields, including `SpendingByCategory.total` and
`BudgetListResponse.summary`. Web, iPhone/iPad, Watch, and widgets consume the
same OpenAPI result, show partial leaves with visible and accessible count copy,
show unavailable decisions without positive/negative styling or progress, and
do not sum wrappers or omission counts locally.

A successful 200 with an unavailable decision clears stale cached decisions.
Apple request ownership includes household/session identity, requested month,
and a monotonic generation; stale or cancelled work must not commit UI, cache,
notification, or widget side effects. Swift changes and validation occur only
on macOS with Xcode.

### 6. Advisor and background behavior

Existing M16 read-only tools reuse the same qualification-aware services as the
HTTP endpoints; this creates no new advisor data domain and no parallel advisor
arithmetic. Tool payloads serialize `value` and `incomplete_count`, label
positive counts as partial, and exclude the count from money/number grounding.
The advisor may quote a partial descriptive value only with its limitation. It
must not reconstruct a null decision or advise on spending, affordability,
coverage, tax liability, budget health, savings cuts, or runway from incomplete
inputs. `incomplete_data` means stored data needs repair, not that the household
must sign in again; sign-in guidance remains reserved for HTTP 423.

Study/review/narrative generation does not invoke an LLM or deterministic
fallback, update hashes, write memories, or replace a cached review from
incomplete facts. Scheduling continues past an incomplete household/month so
one damaged unit cannot starve later work. Vector indexing distinguishes locked
from corrupt data: a locked household preserves every existing vector, while a
confirmed corrupt transaction read removes only that household's transaction-kind
points. Memory-kind points and every other household remain untouched.

### 7. Privacy, audit, and persistence

Unreadable source identities are request-local. Responses, calculation JSON,
warnings, advisor payloads, reviews, and ordinary logs expose only counts and
generic repair language—never ciphertext, plaintext, sealed names, guessed
amounts, or source table/row/column identities. Controlled server diagnostics
may log the household, table, row, and column needed for repair, but never the
token or neighboring sealed text.

Where a service already persists a calculation, a single application-owned,
versioned incomplete attempt records qualified component leaves, null decisions,
`engine_invoked=false`, and the union-derived count. The deterministic financial
engine is not claimed to have run, and its version is unchanged unless engine
behavior changes. A request writes at most one attempt. Non-calculation
aggregate endpoints gain no audit rows.

### 8. Contract and release boundary

This is an intentional coordinated contract release: expected `0.159` from
current `0.158`, or the next unused minor if the repository advances first.
FastAPI schemas, authoritative OpenAPI, recursive parity checks, immutable
compatibility fixture, generated web and Swift clients, API implementation,
`VERSION`, and component `BUILD` values land together under ADR 0074. Mixed old
and new API/client artifacts are unsupported; rollout and rollback are
coordinated.

There is no SQL migration, data repair, new encryption format, cloud dependency,
Docker image/service/topology change, or response-wide database snapshot.
Existing `financial_calculations` JSON and optional primitive client-cache fields
carry the new application shape without schema migration. Repair is natural:
the next read recomputes count 0 and resumes decisions.

## Invariant

An unreadable stored monetary cell is never replaced with zero, hidden, or used
to produce a decision it could change. Every affected read either returns an
honest qualified additive leaf, marks the dependent decision unavailable, or
retains the documented strict 409 boundary; unrelated readable sections remain
usable, and no public or persisted artifact reveals source identity.

## Consequences

- Aggregate responses can remain HTTP 200 and useful while disclosing exactly
  how many relevant stored cells were omitted per field.
- The same damaged source can count once in a composed field and once in each
  independent leaf that it affects; field-local counts describe different
  aggregates.
- Contract types change broadly, so API and both client families must ship as
  one compatibility release.
- Services must preserve provenance through composition, which deliberately
  makes integer-only assumptions fail during implementation.
- Some operations continue to return 409 even after an aggregate page loaded;
  a useful summary does not make a corrupt raw record readable.

## Rejected

- **Make the global decoder tolerant.** This weakens raw-record integrity and
  risks silently treating corruption as zero outside aggregates.
- **Use a response-level metadata map.** Completeness can detach from the field
  it qualifies, and clients can accidentally ignore or misassociate it.
- **Return partial safety decisions.** A plausible affordability, coverage,
  status, tax, ranking, or runway answer is misleading when an omitted amount
  could reverse it.
- **Add child counts during composition.** One stored cell can reach several
  branches; addition double-counts it. Source-set union is required.
- **Run detection again on readable rows and call it partial.** Missing input can
  change membership, cadence, median, pairing, or order; readable-only output is
  not a stability proof.
- **Support old and new wire shapes simultaneously.** Parallel shapes create
  ambiguous client behavior and undermine the monorepo compatibility gate.
- **Repair, delete, re-encrypt, or guess the value.** This feature reports
  incomplete reads; it does not alter financial ground truth.
