# Domain Model

## Core Concepts

### Household

A household owns financial context, users, accounts, goals, reports, and settings.

A household has ONE base currency (ADR 0075, #152). Every total the app computes
for it — net worth, the emergency fund, safe-to-spend, a purchase's impact, a
retirement projection's grounded savings — is in that currency and only that
currency. Multi-currency households remain deferred; this rule holds instead of
crashing. Currency codes are canonical upper-case ISO 4217 wherever they are
stored or compared; "usd" and "USD" are the same currency, never two.

### User

A person who can authenticate and access the household according to a role.

Roles:

- Owner
- Adult
- Viewer
- Child profile

### Account

An account represents a financial container.

An account carries its own currency, which may differ from the household's base
currency (`POST /accounts` accepts any ISO code, and bank sync creates accounts
in whatever currency the provider reports). Such an account is real: it is
listed on the Accounts tab and by the advisor's `get_accounts`, with its balance
in its own currency. It is never summed into a base-currency total, never
converted, and never silently dropped — each total that would otherwise have
counted it discloses it (`excluded_accounts` in a tool payload, the generic
warning on a calculation, `accounts_outside_base_currency` on the Overview), and
an emergency-fund designation on it is ignored and said to be ignored.

Types:

- Checking
- Savings
- Credit card
- Brokerage
- Retirement
- HSA
- 529
- Mortgage
- Auto loan
- Student loan
- Real estate
- Other asset
- Other liability

### Transaction

A dated money movement with amount, currency, account, merchant, category, import source, and review state.

### Bill

A recurring obligation such as mortgage, utilities, insurance, subscriptions, phone, internet, or childcare.

### Income Source

Income includes salary, bonus, RSUs, stock options, side income, and other recurring or expected inflows.

### Goal

A target with a purpose, amount, date, priority, and funding source.

A goal is declared in a currency and shown in that currency. One declared outside
the household's base currency is never relabelled: it is not the emergency
fund's target, purchase impact skips it and says so, and only a base-currency
emergency-fund goal tracks the live designated fund (ADR 0075).

Examples:

- Emergency fund
- Vacation
- Retirement
- College
- Vehicle
- Renovation

### Scenario

A what-if calculation using current context plus user-provided changes.

Examples:

- Buy a laptop
- Take a vacation
- Refinance a mortgage
- Retire at 55
- Accelerate debt payoff

### Recommendation

An explainable answer grounded in financial engine outputs and optionally expanded by the reasoning model.

## Money Rules

- Store amounts as integer minor units plus currency.
- Do not use floating point for persisted money.
- Preserve original imported values.
- Track assumptions used for projections.

## Auditability

Financial engine outputs must include:

- Input references
- Calculation version
- Assumptions
- Warnings
- Output values

## Qualified Amounts and Availability (ADR 0076)

A read-side monetary aggregate distinguishes a descriptive leaf from a decision:

- `QualifiedMoney` is a wire value containing `value: Money` and
  `incomplete_count`. Its value is the sum of readable contributors only; the
  count is the number of distinct relevant unreadable stored amount cells.
- An internal unreadable source is the immutable, request-local identity
  `(household_id, table, row_id, column)`. Composite fields union source sets and
  derive a count; they never add child counts.
- An internal amount candidate retains safe non-amount metadata with an optional
  amount until eligibility, attribution, matching, grouping, and detection are
  resolved. It is not a zero-valued transaction or statement and never crosses
  the wire.
- `ComputationAvailability` distinguishes a complete non-additive computation
  from one whose membership, cadence, median, pairing, order, or projection can
  change because of an unreadable candidate.

Non-amount predicates are applied before decode when possible. An unreadable
cell counts only if it can affect the field; unknown sign is conservatively
relevant. The same source counts once within a composed field, while independent
fields each disclose their own relevant omission. A decision is evaluated only
when the union of every dependency it uses is empty. Otherwise its money value
is null or its non-money state is `unavailable`/`unknown`, while independent
qualified leaves remain visible.

`CategorySpendingTotals` is one authoritative scan containing qualified
per-category buckets, categorized total, uncategorized total, and overall total;
uncategorized is never derived by subtraction. Card aggregates choose the newest
eligible statement using non-amount metadata before decoding only the balance
they use. A corrupt unused minimum does not qualify a balance-only aggregate,
and an unreadable newest balance never falls through to an older statement or
running balance.

These identities and candidates are transient application concepts, not stored
entities. Existing strict raw records remain strict and are never populated with
placeholder amounts.

## Initial Aggregate Boundaries

- Household
- Financial Account
- Transaction Import
- Goal
- Scenario
- Report
- Conversation
- AI Runtime Configuration

## Box-Global Backup Retention Domain (issue #116, ADRs 0077–0078)

Backup state is operational box state, not household financial state. Archives,
configuration, jobs, retention events, and the mutation lock have no household
owner even when an authenticated system administrator supplies household-scoped
audit context.

### BackupSettings

`BackupSettings` is the single persisted configuration identified by `global`.
It owns cadence and SMB destination fields; independent local/off-box
`RetentionPolicy` values, logical maximum bytes, and minimum caller-available
reserves; an opaque UUID-backed configuration `revision`; chronological
`updated_at`; review/activation state; and opaque local and off-box destination
generations. The revision changes atomically with every non-empty settings
mutation, destination-generation rotation, and restore/compensation re-upsert;
`updated_at` is modification chronology rather than entity identity. The
generations change when destination identity changes and after restore, so
observations and journal facts never flow between different physical
destinations. SMB password ciphertext and the local path fingerprint are internal
and never returned.

Fresh settings are daily with two tiered `3 / 14 / 90` policies, unlimited
logical caps, 1 GiB reserves, and activated retention. Every upgraded or restored
setting requires explicit system-administrator review before automatic pruning.

### RetentionPolicy

A `RetentionPolicy` has mode `tiered | keep_all`. `tiered` requires cumulative
`keep_all_days`, `daily_until_days`, and `weekly_until_days` satisfying
`1 <= keep_all <= daily <= weekly <= 3650`; `keep_all` requires null horizons.
Its UTC bands are recent (all archives), daily (newest per UTC date), weekly
(newest per ISO Monday week), and expired. Equal adjacent horizons disable an
intermediate band. A policy is destination-independent and contains no cadence.

### BackupInventoryItem

A `BackupInventoryItem` is one observed archive candidate with destination,
stable archive key, timezone-aware `taken_at`, timestamp source
`job_started_at | remote_modified_at`, size, optional job ID, and
managed/present/readable/compatible state plus non-sensitive anomaly codes.
Local job time and joined remote job time use `backup_jobs.started_at`; remote
mtime is a disclosed fallback.

Only completed, present, nonzero, readable, compatible managed items occupy
buckets or contribute to logical-cap bytes. A recognized remote item can remain
eligible without a local row/file. Missing, unreadable, size-mismatched,
future-dated, future-version, orphaned, unrecognized, partial, and other
anomalous artifacts are excluded from recovery claims and protected from
automatic deletion.

### RetentionDecision

A `RetentionDecision` is immutable: archive key, `keep | delete`, reason, and
optional UTC bucket key. Reasons are `newest`, `recent`, `daily_bucket`,
`weekly_bucket`, `expired`, `bucket_superseded`, `capacity_limit`, and
`protected_anomaly`. Stable selection order is `taken_at DESC, archive_key ASC`.
The newest eligible archive is always kept. A `RetentionPlan` adds the explicit
UTC `as_of` and exact policy/cap snapshot; identical inputs produce identical
output.

### CapacityObservation

`CapacityObservation` describes caller-usable destination capacity at one UTC
instant. It contains `status: ok | warning | insufficient | unknown |
unavailable`, nullable total and available bytes, required reserve bytes,
nullable estimated-next-backup bytes and acceptability, a stable reason code,
and optional redacted reason. It never calls `total - available` “used space”
and never guarantees a later write. Protected physical bytes can make capacity
constrained even though they are excluded from the managed logical cap.

### RecoveryWindow

`RecoveryWindow` separates configured target from observed candidates. Per
destination it records configuration, destination state
`not_configured | empty | healthy | constrained | degraded | unavailable`,
coverage state `not_applicable | empty | building | met | incomplete |
shortened | unknown`, policy/review state, pending prune totals, visible and
nullable readable counts, endpoint-probe completeness/count, qualified oldest
and newest readable times, timestamp source, anomaly counts, capacity, redacted
reasons, and `verification_scope=inventory_read_probe`.

A recovery candidate is visible, nonzero, endpoint-read-probed, and not known
incompatible. It is not a verified restore point: key availability,
authenticated decryption, content completeness, migration, and destructive
restore remain untested. Overall state uses all configured destinations and
never lets healthy local storage hide unavailable off-box storage.

### BackupRetentionEvent

`BackupRetentionEvent` is a durable, box-global operational journal fact. It
carries destination/generation, operation and deterministic unique event key,
optional archive/job identity and time/source/size, action, stable reason,
policy revision/snapshot, redacted detail, and occurrence time. Actions include
prune/delete/reconcile outcomes, inventory and capacity health, lock skips,
anomalies, and restore resets. It has no household foreign key and stores no
credential, raw SMB exception/path/user, archive content, or financial data.

Events are idempotent across retries and scoped to the active destination
generation. They distinguish coverage still building from coverage shortened by
a known policy, capacity, or explicit-delete action; they do not certify an
archive.

## Authentication lifecycle (#164)

A device identity outlives an auth session and retains its household and public
key. An existing-device challenge proves possession of that key in combination
with the account password. A client session generation identifies work and
cached data from one installation of a bearer.

## AI Runtime Apply Domain (issue #170, ADR 0081)

An AI runtime replacement is operational deployment state, not financial state.
Its job records, plans, snapshots, decisions, and receipts carry model and
deployment identities only — never financial data, credentials, or household
content. The advisor has no access to it (explicit non-goal, #170 gate).

### ApplyJob

One model-replacement transaction, identified by a UUID `job_id` and an
idempotency `request_id`. Durable states are `accepted`, `staging`,
`launching`, `verifying`, `commit_pending`, `restoring`, `succeeded`,
`failed`, `restored`, `recovery_failed`, and `commit_repair_required`; `idle`
is a read representation, never a stored state. Each record carries a
monotonic `sequence`, origin (`api | cli`), optional opaque initiator
household/user ids, a normalized-request digest, the durable `decision`
(`undecided | commit | restore` — monotonic: a committed decision never
becomes restore), phase deadlines, and a failure code with a redacted message.
HTTP disconnects, client navigation, and manager restarts never cancel a job.

### LaunchPlan

The frozen, immutable resolution of one accepted request: main/photo
repository IDs with immutable revisions, photo mode and placement, selected
topology, installed runtime version and exact image IDs, structured launch
arguments, context/concurrency limits, and per-node memory/disk requirements
with evidence classifications (`verified | estimated | unknown`). Unknown
required evidence is never treated as zero, as "fits", or as a single-node
assumption. The plan never contains `.env` contents, tokens, SSH details,
raw checker output, or arbitrary container logs.

### DeploymentSnapshot

The immutable previous-working deployment captured before acceptance and
verified serving at capture time: model IDs + revisions + served-name policy,
exact image IDs (including peer identity), structured
serve/parser/context/KV arguments, the presence and value of every
runtime-owned environment key, service placement/scale/restart/routing,
runtime configuration hashes, and references to retained cache artifacts.
Restoration reinstates it exactly and re-verifies it; a first deployment has
no snapshot and reports `failed`/`no_previous_runtime` after returning to the
explicit empty baseline.

### CommitDecision and receipt

`commit` becomes durable only after exact verification passes
(`commit_pending`); an API reconciler then performs the one settings
transaction — initiating-household config upsert, matching-household repoint,
receipt insert, outcome audit — under a PostgreSQL advisory lock, and
acknowledges the manager. `restore` is the durable direction for any failure
after active mutation but before commit. The receipt
(`ai_runtime_apply_receipts`) is the only SQL record of the job: identity,
runtime key, plan hash, attribution, source, committed model, and commit
time. It makes the commit exactly-once across API replicas and retries; it
stores no execution state.

### Non-financial scope

No financial calculation reads or writes any of this domain; synthetic
verification probes never execute household tools; the apply audit actions
are operational records registered explicitly irreversible and name no
financial figures beyond the committed model identity.
