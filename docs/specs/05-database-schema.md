# Database Schema

Primary database: PostgreSQL.

The schema must protect precision, auditability, and privacy.

## Initial Tables

- households
- users
- household_memberships
- auth_sessions
- pairing_sessions
- accounts
- account_balances
- transactions
- transaction_categories
- bills
- income_sources
- goals
- scenarios
- financial_calculations
- recommendations
- conversations
- conversation_messages
- imports
- import_files
- documents
- document_extractions
- reports
- ai_runtime_configs
- audit_events
- backup_jobs

## Money Storage

Use:

- `amount_minor` as signed integer
- `currency` as ISO 4217 code

Do not persist financial amounts as floating point.

## Encryption Requirements

The schema design must support encryption for sensitive fields and encrypted backups. Final encryption implementation is defined by the security model.

## Audit Requirements

Persist enough information to explain:

- Which inputs were used
- Which calculation version ran
- Which assumptions were applied
- Which model and prompt version produced explanation text

## Qualified Aggregate Persistence (M124, ADR 0076)

M124 requires **no SQL migration**. Unreadable source identities and candidate
metadata are immutable, request-local application values and are never stored.
Repair changes no schema: the next read recomputes the aggregate and naturally
returns count 0 when every selected cell is readable.

Where a service already persists a `financial_calculations` record, complete and
incomplete work pass through one application-owned attempt boundary. An
incomplete attempt writes exactly one existing audit row with an application
schema version (for example `qualified-attempt/1`), `engine_invoked=false`, the
union-derived `incomplete_amount_count`, qualified component leaves, null
unsafe decisions, and a generic warning. It must not claim the deterministic
engine ran or change the engine version unless engine behavior changes.
Non-calculation aggregate endpoints gain no audit rows.

`inputs_json`, `outputs_json`, assumptions, and warnings never store unreadable
source row IDs, table/column names, ciphertext, sealed names, neighboring sealed
text, or guessed values. Cancellation before the persistence commit may leave
no row; after commit the single valid row remains. A retry writes a new
self-contained attempt and never accumulates counts across requests. Existing
schemaless calculation JSON is sufficient, and cached yearly reviews remain
stored but are suppressed while current dependencies are incomplete.

## Box-Global Backup Schema (issue #116, ADR 0077)

Issue #116 adds migration `0093_box_global_backup_settings.py`, parented to
`0092_uppercase_currency_codes` at the accepted baseline. Revalidate the head
before implementation.

### `backup_settings`

One application-owned row uses `key = 'global'` as its primary key and has no
household foreign key. It stores:

- `frequency` and nullable SMB host/share/folder/username/encrypted-password/
  domain fields;
- local and off-box `retention_mode`, `keep_all_days`, `daily_until_days`, and
  `weekly_until_days` groups;
- nullable independent `local_max_bytes`/`offbox_max_bytes`;
- non-negative independent `local_min_free_bytes`/`offbox_min_free_bytes`;
- `legacy_conflict_detected`, `retention_review_required`, nullable
  `retention_activated_at`, opaque local/off-box destination-generation UUIDs,
  and an internal nullable local-path fingerprint;
- timezone-aware `created_at` and optimistic `updated_at`.

Database checks enforce the constant key, allowed cadence/modes, mode/horizon
null consistency, cumulative tier order/bounds, positive nullable caps, and
non-negative reserves where portable. Repository validation repeats every rule
for non-HTTP callers. Fresh defaults are daily, independent tiered `3/14/90`,
unlimited caps, 1 GiB reserves, no conflict/review, and an active policy epoch.
SMB password remains encrypted and is never returned.

### `backup_jobs` reconciliation

Add nullable `prune_reason`, including at least `policy_expired`,
`bucket_superseded`, `max_bytes`, `missing_file`, and `explicit_delete`.
Automated local deletion never hard-deletes the row: file removal is followed by
marking it pruned with a reason. If metadata mutation fails after file deletion,
the next locked maintenance pass observes the missing file and idempotently
finishes reconciliation. Explicit administrator deletion may hard-delete only
after its irreversible journal/audit facts are recorded.

### `backup_retention_events`

The box-global operational journal has no household foreign key. It stores UUID
`id`; `destination`; nullable archive key/job id; `operation_id`; non-null unique
deterministic `event_key`; opaque destination generation; `action`; stable
`reason`; nullable archive time/timestamp source/size; nullable policy revision
and JSON snapshot; redacted nullable detail; and UTC occurrence time. Index
`(destination_generation, occurred_at)`.

Actions cover `pruned`, `explicit_deleted`, `reconciled`, `prune_failed`,
`inventory_failed`, `inventory_succeeded`, `capacity_blocked`, `lock_skipped`,
`anomaly_detected`, and `restore_reset`. The unique event key includes operation,
generation, archive-or-destination sentinel, action, and reason so both archive
and destination-wide retries are idempotent. Journal rows never store
credentials, raw SMB errors, UNC paths, usernames, archive content, or financial
data.

Status reads only events for the active destination generation and current
target. Deletion facts retain archive time after the file is gone, and the
latest inventory success clears a transient prior failure. Generation rotation
prevents one path/NAS or pre-restore history from describing another.

### Bootstrap, authority, and downgrade

Alembic creates schema but does not make durable values depend on the migration
process environment. The first transaction-safe repository read materializes
the singleton from database evidence plus one-release legacy inputs.

Legacy candidate selection prefers complete SMB configuration, then greatest
household `updated_at`, then ascending household ID; distinct non-empty
configurations set the conflict marker. Cadence/destination/encrypted credential
are copied, and the old shared cap seeds both destination caps. Every existing
installation starts review-required with null activation, so all visible managed
archives survive until explicit confirmation.

Legacy local count parses `FAMILY_CFO_BACKUP_RETENTION_COUNT`, falling back to
7 for missing, invalid, or non-positive input. For active cadence, compute
`cadence_horizon = ceil(max(0, count - 1) * cadence_minutes / 1440)`; compute
`observed_horizon` as the ceiling in days from bootstrap `as_of` to the oldest
completed/unpruned job, or zero. If
`max(90, cadence_horizon, observed_horizon + 1) <= 3650`, initialize
`3/14/derived_outer`; otherwise use keep-all plus review, never clamp or fail.
Cadence `off` omits the cadence term and preserves observed history. Legacy
remote age zero becomes keep-all; representable positive `N` becomes `N/N/N`;
larger values become keep-all plus review. Once materialized, database values
win even if legacy environment or household values change.

Downgrade copies representable cadence/SMB fields back to each household and a
shared cap only when destination caps are equal; otherwise it writes null and
requires operator review. Tier policy is not representable: rollback requires
explicit legacy environment values and cannot recreate deleted archives or
historical rows. Household columns/legacy helpers remain for one compatibility
release and are removed only by separately planned cleanup.

Restore re-upserts the captured current operational singleton after database
rollback, rotates both generations, sets review-required with null activation,
and writes a restore-reset event. Restored historical settings/journal state must
not resume pruning automatically.

## Migration Rules

- All schema changes use migrations.
- Migrations must be reversible where practical.
- Never include production data.
- Fixtures must be synthetic.
