# ADR 0078: Use an opaque revision for backup-settings concurrency

## Status

Accepted (2026-09-14, issue #116). This ADR supersedes ADR 0077 only for the
backup-settings concurrency-token representation. ADR 0077 remains authoritative
for box-global ownership, retention, recovery status, mutation locking, restore,
audit, and no-pruning-during-activation behavior. Implementation is governed by
`docs/plans/backup-settings-revision-and-recovery-ux-2026-09-12.md`.

## Context

`BackupSettings.updated_at` currently serves both as display chronology and as an
exact optimistic-concurrency token. That coupling is not portable across generated
clients. The shipped Swift client decodes a fractional JSON date into
`Foundation.Date` and can encode it without the original fractional text. The API
then correctly rejects the changed timestamp with 409 even when no competing writer
exists. Adding timestamp tolerance would instead allow distinct writes within the
tolerance interval to share a validator.

The PUT endpoint has a second concurrency defect: after committing a settings row,
it discards that record and reloads the singleton while constructing the response.
A later writer can commit during that interval, causing the first response to carry
the later writer's token. Queued work from the first client could then overwrite the
intervening edit without observing a conflict.

Contract `0.160` is already released and immutable. Its lossless clients must remain
able to send exact `expected_updated_at` values while the next clients adopt a token
that survives language and serializer boundaries.

## Decision

### 1. One opaque JSON validator

`BackupConfig` adds a required `revision: string`. The persisted value is an
application-generated UUID string in `backup_settings.revision VARCHAR(36) NOT
NULL`, but the API treats it as opaque: clients must not parse, sort, increment,
display, or derive meaning from it. OpenAPI bounds it to 1 through 128 characters
without declaring a UUID format.

`updated_at` remains required but is only modification/display chronology and
operator diagnostic context. It is not entity identity or the preferred
concurrency token.

`BackupConfigUpdateRequest` adds optional `expected_revision`. New `0.161` clients
send that field only. No ETag or If-Match authority is added.

### 2. Contract 0.161 is the compatibility window

The `0.161` server accepts these request forms:

- `expected_revision` only: compare the revision exactly;
- exact `expected_updated_at` only: accepted for `0.160` compatibility;
- both: both must match the same current row;
- neither: allowed only for the existing tokenless legacy-field allowlist and its
  equal-cap alias rule.

A supplied compatibility timestamp is never ignored when a revision is also
present. A stale, contradictory, rounded, or truncated supplied token returns 409
with no mutation or audit. No timestamp tolerance is permitted. The earliest
removal of `expected_updated_at` and tokenless compatibility is contract `0.162`
in a separate coordinated change after `0.160` clients are no longer supported.
The immutable `compatibility/0.160.yaml` fixture is never changed.

Retention/capacity changes or `confirm_retention_policy=true` require at least one
precondition token. After structural and policy validation, absence of both returns
428. Invalid bodies, invalid policy, policy fields without confirmation, and
malformed revision strings remain 422. Backup/restore operation contention and
stale tokens remain 409.

### 3. Every mutation advances the validator atomically

Every non-empty successful mutation of `backup_settings` generates a fresh UUID
revision and a strictly increasing `updated_at` in the same SQL statement. This
includes ordinary and tokenless compatibility updates, confirmed activation,
direct internal activation, destination-generation rotation, local-path identity
change, restore re-upsert, and compensating restore re-upsert. Destination identity
and configuration revision advance in the same transaction.

Repository writers load the row under their existing transaction discipline,
validate every supplied token, and repeat every supplied predicate in the SQL
`UPDATE`. An empty normalized patch validates all supplied tokens and returns the
current record without advancing either field. A non-empty equal-value patch retains
the existing reviewed-write behavior: it advances both fields and emits the
existing no-value-change audit summary.

Restore never reuses a captured or restored revision. After migration it restores
the captured operational configuration and encrypted password, creates a fresh
revision, advances chronology, rotates both destination generations, requires
retention review, and clears the activation time. The same rule applies to verified
compensation, so every token observed before a restore fails afterward.

### 4. PUT responses belong to their own commit

GET may load the current singleton and adapt it to `BackupConfig`. PUT must adapt
the exact `BackupSettingsRecord` committed by that request. Pending-prune and latest
job metadata may be evaluated at response time, but configuration values and
`revision` remain pinned to the committed record. A later writer can therefore make
the first response stale, but can never donate its revision to that response.

Successful mutation produces exactly one existing `backup.config_updated` audit
whose summary names changed groups without values. Rejected 409, 422, and 428
requests produce no mutation and no audit. Audit or response-construction failure
after persistence remains transport-ambiguous: the settings may be committed, and
a client must reload and reconcile before retrying.

### 5. Release boundary

The authoritative OpenAPI is edited before backend/client adaptation. Publication
of required response fields is coordinated as contract `0.161`: immutable fixture,
regenerated web and Swift clients, `VERSION`, and all component build resets land as
one later release item. API/worker deploy before dependent clients. Before any
revision-dependent client is distributed, rollback may quiesce writers, downgrade
migration 0095 to 0094, and restore prior images. After such a client is installed,
a server-only rollback is unsupported; retain a compatible server or forward-fix.

### 6. Security and advisor scope

Revisions are non-secret validators and contain no configuration values, credential
material, path identity, household data, or financial data. They may appear in API
payloads and bounded diagnostics, but never justify logging a request body or SMB
credential. Authorization remains `BACKUPS_MANAGE`.

No Advisor tool change is required. This change adds no household-readable data
domain and affects only a system-administrator mutation protocol; the ordinary
advisor remains household-scoped.

## Invariant

A settings mutation accepted against a current validator yields one fresh opaque
revision tied to that exact committed record. Any earlier revision or altered
compatibility timestamp fails closed, and restore or destination identity change
invalidates all previously observed revisions without weakening review or pruning
safety.

## Consequences

- Generated clients carry concurrency state losslessly as a string.
- `updated_at` can retain its human and audit meaning without serializer precision
  affecting correctness.
- The one-release dual-token path adds temporary repository and test complexity.
- PUT responses can be immediately stale if another writer commits, but they can no
  longer authorize overwriting that writer; the next request receives 409.
- Revision-aware clients require a coordinated `0.161` server and cannot safely run
  against `0.160`.

## Rejected alternatives

- **Timestamp tolerance or truncation:** distinct valid writes could share a token.
- **Swift encoder-only repair:** decoding has already lost the original timestamp
  representation.
- **Global date-transcoder change:** broad risk and continued coupling to database
  precision.
- **ETag/If-Match plus JSON compatibility:** two simultaneous authorities and poorer
  ergonomics in the current generators.
- **Numeric revision:** introduces JavaScript safe-integer and accidental-arithmetic
  concerns.
- **Opaque updated-at text:** still couples correctness to timestamp storage and
  formatting.
- **Automatic replay after conflict:** can overwrite another administrator's
  accepted edit.
