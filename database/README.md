# Database

Planned primary database: PostgreSQL.

This directory will contain schema definitions, migrations, seeds, and local development fixtures.

Only synthetic data belongs in this repository.

## Migrations

Alembic migration scripts live in `database/migrations`.

The API app owns the migration runner configuration for now:

```bash
cd apps/api
make migrate
```

M1 includes an empty baseline migration only. M2 adds the first product tables: `households`, `users`, `household_memberships`, `auth_sessions`, `accounts`, `account_balances`, `transactions`, `transaction_categories`, `bills`, `income_sources`, `goals`, `scenarios`, and `financial_calculations`.

Later migrations add:

- M3: `recommendations`
- M4: `recommendations.model_version`, `recommendations.prompt_version`, and `ai_runtime_configs`
- M6 backend support: `pairing_sessions`, `paired_devices`, and nullable `auth_sessions.device_id` for device-backed session revocation
- M7: `imports`, `import_files`, `documents`, `document_extractions`, and nullable `transactions.import_id`/`transactions.possible_duplicate`
- M8: `reports` and `backup_jobs`
- M9: `audit_events`
- M10: `conversations` and `conversation_messages`
- M14: nullable `accounts.annual_interest_rate`/`accounts.minimum_payment_minor`, and `debt_payoff`/`retirement_projection` added to the `financial_calculations` type check
- M15: `annual` added to the `reports` type check
- later: net-worth snapshots, `budgets`, safe-to-spend/emergency-fund settings,
  401(k)-loan & maturity-date & institution columns, overview snapshots, the M97
  `transactions.duplicate_state` review flag, the M98 off-box backup destination
  + SMB credentials + size cap, `transactions.note`/attachment columns, the
  `audit_events.undo_token`/`reverted_at` undo columns (`0056`/`0057`), and the
  goal `monthly_contribution` (`0058`), and subsequent product work through
  uppercase currency normalization (`0092`)
- M125/issue #116: `0093_box_global_backup_settings` adds the box-global
  `backup_settings` singleton, `backup_retention_events`, and
  `backup_jobs.prune_reason`; `0094_backup_delete_intents` adds the durable
  `delete_pending` journal action

The migration head is currently **`0094_backup_delete_intents`** — run
`ls database/migrations/versions/` for the authoritative, complete list.

## Money Storage

Every money column is a signed integer `*_minor` column (e.g. `amount_minor`, `balance_minor`, `target_minor`) paired with a 3-character `currency` column. No financial amount is ever stored as a floating-point or numeric/decimal column — see `docs/specs/03-domain-model.md` for the full money rules and `services/financial-engine` for the `Money` value type application code uses to manipulate these amounts.

## Import and Document Staging

Uploaded import/document files are not stored in the database — `import_files.storage_path` and `documents.storage_path` are relative paths within a local directory controlled by `FAMILY_CFO_IMPORT_STAGING_DIR` (default `./data/import-staging`), matching the "Import staging" volume planned in `docs/specs/10-docker-spec.md`. Paths are always relative so they stay portable across environments; never commit real staged files (synthetic fixtures only, per `AGENTS.md`).

## Backups

`backup_jobs` tracks encrypted box-global backup archives stored on disk (never
in the database) under `FAMILY_CFO_BACKUP_DIR` (default `./data/backups`). Every
archive contains the whole database and shared staging tree; jobs and operational
retention events therefore carry no household owner. `backup_settings` is an
application-owned singleton keyed by `global` and is authoritative for cadence,
SMB destination, independent local/off-box policies, caps, reserves, review
state, and destination generations. Automated pruning clears the archive path
and retains the job row with a reason; the journal records global lifecycle
causality without credentials or household data.

### Archive Format

Each archive is a tar containing a database dump (`pg_dump --format=custom` in production, or a raw file copy of a file-based SQLite database in tests — see `services/backup/README.md`) and a tar of the import/document staging tree, both bundled before encryption so a single key covers everything a backup needs to restore.

### Key Handling

`FAMILY_CFO_BACKUP_ENCRYPTION_KEY` must be a Fernet key (url-safe base64, 32 bytes):

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set it via an environment file or Docker secret (matching the pattern `docs/specs/10-docker-spec.md` already specifies for other secrets) — never commit it. There is no key recovery mechanism: losing the key makes every backup encrypted with it permanently unrecoverable. Rotating the key only affects backups taken after the rotation; restoring an older backup requires the key that was active when it was taken.

### Retention, capacity, and recovery status

Local and off-box tier policies are deterministic UTC decisions over qualified
inventory. Logical caps do not include protected anomalous evidence, but those
files still consume physical space. Capacity is a caller-available point-in-time
observation (`statvfs` locally, SMB `stat_volume` remotely), not a reservation;
an unsupported query is unknown and a later write can still fail. Recovery
status proves only visible read-probed candidates—not key availability,
authenticated decryption, archive completeness, migration viability, or restore.

### Upgrade and downgrade

Migration `0093` creates schema only. The first transaction-safe repository read
bootstraps the singleton from deterministic legacy household evidence and the
running process's legacy count/off-box-age compatibility inputs. It requires
administrator review and leaves automatic retention inactive; once the singleton
exists, database values are authoritative and the old environment values and
household columns are ignored by normal reads.

Downgrading `0094` converts pending delete intents to failed-prune evidence.
Downgrading `0093` copies cadence/SMB fields to every household and copies a
shared cap only when the two new caps are equal, then drops the new schema. Tier
policies, unequal caps, reserves, activation, and recovery status are not
representable, and no downgrade recreates deleted archives or journal history.
Set explicit legacy retention environment values before starting old code.

### Off-box destination

The global singleton can copy each encrypted archive to a Synology/SMB share.
The SMB password is encrypted at rest with the backup key and is never returned.
Backups can be listed and restored directly from the share. See
[`docs/guides/backup-and-restore.md`](../docs/guides/backup-and-restore.md) for
capacity limitations and the user-credentialed NAS validation checklist.

### Restore Procedure

`POST /api/v1/backups/{id}/restore` (system administrator with `backups.manage`)
decrypts the named archive and replaces the *entire* current database and staging
directory. Restore preserves the current operational global configuration and
encrypted SMB credential across the replacement, reapplies migrations, rotates
destination generations, and requires retention review again. Reconciliation
handles dump-time/interrupted job state and protects newer orphaned files.

### Tests and rollout

SQLite tests cover migrations, bootstrap/downgrade, and lifecycle behavior. CI
also provisions synthetic PostgreSQL 17 for migrations `0093`/`0094`, singleton
bootstrap, advisory-lock conflict, and connection loss. It sets
`FAMILY_CFO_REQUIRE_POSTGRESQL=1`, so these tests fail rather than skip if the
URL, driver, or server is unavailable.
Deploy/migrate API and worker before contract `0.160` web or Apple clients; verify
live config/status and local/SMB paths before each client rollout.

## Audit Events

`audit_events` (M9) records a non-sensitive row for every sensitive mutation made through the write APIs (account/transaction/bill/income CRUD, membership changes, household bootstrap): `actor_user_id`, `action` (e.g. `account.created`), `entity_type`, `entity_id`, and a short `summary`. Audit rows are `Internal` per the security model and must never contain `Restricted`/`Sensitive` values — no amounts, balances, passwords, or tokens (enforced by `family_cfo_api/audit.py` and asserted by tests). Coverage has since been extended to **every** mutation: `audit.write_audit` refuses an unclassified action, and each state-changing action records an `undo_token` (columns added in `0056`) so it can be reversed from the Activity log ([ADR 0023](../docs/adr/0023-every-mutation-is-undoable.md)); `reverted_at` marks one that has been undone.
