# OpenAPI

The backend API is the source of truth. SwiftUI and Angular clients generate clients from the same OpenAPI contract.

Initial contract: `shared/openapi/family-cfo.v1.yaml`.

## Design Rules

- Version the API under `/api/v1`.
- Return structured errors.
- Use stable IDs.
- Use integer minor units for money.
- Use explicit currency.
- Keep LLM responses structured enough for UI rendering.
- Avoid duplicated DTO definitions in app clients.

## Initial Endpoint Groups

- Health
- Pairing
- Authentication session
- Household context
- Accounts
- Transactions
- Bills
- Income
- Goals
- Purchase advisor
- Chat
- Reports
- Imports
- Documents
- AI runtime configuration

## Error Shape

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "details": {}
  }
}
```

## Recommendation Shape

Recommendations must include:

- Answer
- Assumptions
- Impacts
- Tradeoffs
- Alternatives
- Confidence
- Calculation references

## Qualified Aggregate Contract (M124, ADR 0076)

The coordinated contract release adds:

```text
QualifiedMoney
  value: Money                 required
  incomplete_count: integer    required, minimum 0

ComputationAvailability
  status: complete | unavailable
  incomplete_count: integer    required, minimum 0
```

`complete` requires count 0 and `unavailable` requires a positive count.
`QualifiedMoney.value` is never null and never contains a synthetic substitute.
A positive count means the value sums readable contributors only. A nullable
decision uses `QualifiedMoney | null`; every non-null decision is complete.
Ordinary absence versus corruption-driven unavailability is distinguished by a
colocated availability field where both would otherwise serialize as null.

The field matrix is normative in ADR 0076 and the M124 task section. It includes
qualified descriptive totals across household context, spending/categories,
budgets, cash flow, savings, timeline totals, outlook/plan components, income,
and yearly overview; nullable derived net/remaining/coverage/rate/projection/
safe-to-spend/tax values; `unknown` or `unavailable` decision enums; nullable
rankings; `SavingsContributionSet`; server-authored
`SpendingByCategory.total`; and required `BudgetListResponse.summary`.
Existing unaffected stored values remain `Money`.

Transaction list/detail, the card-statement list and card-statement
mutation/undo pre-reads, raw exports/indexing, equality/dedupe/sign/range
filters, write comparisons, review
generation, and unstable candidate/ranking-only operations document HTTP 409
`sealed_amount_unreadable`. Aggregate-local qualification does not catch
`household_locked`; HTTP 423 remains distinct.

The contract specifies exact null/zero/empty semantics: partial zero is
`value.amount_minor == 0` with a positive count; no contributors is zero/count
0; unavailable decisions are null rather than zero; unavailable rankings are
null rather than an exact empty list; counts exclude ADR 0075 foreign-currency
exclusions.

## Client Generation

Generated clients are derived artifacts. The OpenAPI contract is edited first, then clients are regenerated.

M124 is an intentional breaking shape change. It moves contract `0.158` to
expected `0.159`, or the next unused minor if `VERSION` advances first. FastAPI
schemas, authoritative OpenAPI, recursive parity validation (`$ref`, requiredness,
nullability, array items, and enums), immutable compatibility fixture, generated
web and Swift clients, API behavior, `VERSION`, and all component `BUILD` values
must land atomically under ADR 0074. Mixed old/new artifacts are unsupported;
rollout and rollback are coordinated. Item 1 changes documentation only; later
items own all contract, version, and generated-client files.

## Backup Retention and Recovery Contract (issue #116, ADR 0077)

The authoritative OpenAPI change is additive and occurs only in the later
server/contract work item. WI-1 defines its required shape; it does not edit the
YAML or generated clients.

### Global configuration

`GET/PUT /backups/config` remains the configuration seam but represents one
box-global stream. `BackupConfig` and its update request add:

- `local_retention` and `offbox_retention`, each with
  `mode: tiered | keep_all`, nullable cumulative horizons, and nullable
  `target_oldest_at` on responses;
- `local_max_bytes`, `offbox_max_bytes`, `local_min_free_bytes`, and
  `offbox_min_free_bytes`;
- `legacy_conflict_detected`, `retention_review_required`, nullable
  `retention_activated_at`, and the optimistic `updated_at` token;
- update-only `expected_updated_at` and
  `confirm_retention_policy: boolean = false`.

Tiered horizons validate as
`1 <= keep_all_days <= daily_until_days <= weekly_until_days <= 3650`;
keep-all requires null horizons. Caps are null or positive (input zero
normalizes to unlimited for compatibility), reserves are non-negative, and the
existing SMB completeness/password-preservation rules remain.

Deprecated `max_bytes` remains for one contract window. If neither new cap is
supplied, a supplied alias updates both; either new cap takes precedence. The
response alias is non-null only when both caps are equal. Explicit-field tracking
distinguishes omission from JSON null. A tokenless legacy request may update
legacy fields last-write-wins but cannot activate pending retention. A stale
`expected_updated_at` returns 409 with no mutation, and only a valid confirmed
save activates policy. Confirmation returns pending prune count/bytes and never
prunes synchronously.

### Capacity and remote inventory

`BackupCapacityObservation` contains status
`ok | warning | insufficient | unknown | unavailable`, nullable total/available
bytes, required reserve, nullable estimate and acceptability, `as_of`, stable
reason code, and optional redacted reason. It is added to
`BackupDestinationCheckResponse`; writable with unknown capacity remains
writable, while unavailable auth/network/share is not.

`RemoteBackupListResponse` adds `status`, `as_of`, and optional redacted reason.
An unavailable listing carries `status=unavailable` and an empty `backups` array;
clients must inspect status and must not render it as an empty destination.

### Recovery status

Add `GET /backups/status`, protected by `backups.manage`, returning one
`BackupRecoveryStatus` with a shared UTC `as_of`, overall status, nullable
qualified overall oldest/newest times, exactly one local and one off-box
`BackupDestinationRecoveryStatus`, and fixed
`verification_scope: inventory_read_probe`.

Each destination includes:

- configured and `not_configured | empty | healthy | constrained | degraded |
  unavailable` status;
- `not_applicable | empty | building | met | incomplete | shortened | unknown`
  coverage status;
- policy, review/activation state, and pending prune count/bytes;
- recognized visible count, nullable exact readable count, probe status
  `complete | partial | unavailable`, and probed count;
- nullable oldest/newest readable times and oldest timestamp source
  `job_started_at | remote_modified_at` only when endpoint qualification
  establishes them;
- metadata mismatch, protected anomaly, compatibility-unknown, and
  known-incompatible counts;
- capacity, stable reason codes, optional redacted reason, destination `as_of`,
  and the same fixed verification scope.

Coverage evaluates the outer target UTC-day/ISO-week bucket. `met` needs a
qualified candidate in that bucket plus a newer candidate and sufficient probe
completeness; `building` means the policy is too young with no contrary
opportunity/deletion evidence; known policy/capacity/explicit deletion yields
`shortened`; mature sparse or failing history yields `incomplete`; review,
inventory/probe uncertainty, anomalies, or missing causality yields `unknown`.
Overall status is unavailable only when no configured destination can be
inventoried, empty only when every configured destination is fully and safely
empty, healthy only when every configured destination is healthy, constrained
when any is constrained, and otherwise degraded.

Every description uses “oldest readable backup currently visible” or “recovery
candidate.” The contract explicitly excludes a guarantee of key availability,
authenticated decryption, archive integrity/completeness, migration, or restore.

### Compatibility and release

`HostedHouseholdList.offbox_backup_retention_days` remains deprecated for one
window, derived as zero for keep-all or the off-box outer horizon for tiered; the
full global policy is not duplicated onto household responses. Standard 409
responses document optimistic conflict and `backup_in_progress`.

At the pinned baseline the first dependent client moves contract `0.159` to
`0.160`, resets component builds, and adds immutable
`shared/openapi/compatibility/0.160.yaml`; if the repository advances first, use
the next unused contract. OpenAPI changes precede generation. API/worker deploy
before web/iOS, and mixed dependent clients with an older API are unsupported.
