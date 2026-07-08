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

## Money Storage

Every money column is a signed integer `*_minor` column (e.g. `amount_minor`, `balance_minor`, `target_minor`) paired with a 3-character `currency` column. No financial amount is ever stored as a floating-point or numeric/decimal column — see `docs/specs/03-domain-model.md` for the full money rules and `services/financial-engine` for the `Money` value type application code uses to manipulate these amounts.

## Import and Document Staging

Uploaded import/document files are not stored in the database — `import_files.storage_path` and `documents.storage_path` are relative paths within a local directory controlled by `FAMILY_CFO_IMPORT_STAGING_DIR` (default `./data/import-staging`), matching the "Import staging" volume planned in `docs/specs/10-docker-spec.md`. Paths are always relative so they stay portable across environments; never commit real staged files (synthetic fixtures only, per `AGENTS.md`).

## Backups

`backup_jobs` tracks encrypted backup archives, stored on disk (never in the database) under a directory controlled by `FAMILY_CFO_BACKUP_DIR` (default `./data/backups`), matching the "Encrypted backups" volume planned in `docs/specs/10-docker-spec.md`. `backup_jobs.storage_path` is a relative path within that directory, cleared (not deleted as a row) once `FAMILY_CFO_BACKUP_RETENTION_COUNT` (default `7`) prunes the on-disk file, so backup history remains visible via `GET /api/v1/backups` even after the archive itself is gone.

### Archive Format

Each archive is a tar containing a database dump (`pg_dump --format=custom` in production, or a raw file copy of a file-based SQLite database in tests — see `services/backup/README.md`) and a tar of the import/document staging tree, both bundled before encryption so a single key covers everything a backup needs to restore.

### Key Handling

`FAMILY_CFO_BACKUP_ENCRYPTION_KEY` must be a Fernet key (url-safe base64, 32 bytes):

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set it via an environment file or Docker secret (matching the pattern `docs/specs/10-docker-spec.md` already specifies for other secrets) — never commit it. There is no key recovery mechanism: losing the key makes every backup encrypted with it permanently unrecoverable. Rotating the key only affects backups taken after the rotation; restoring an older backup requires the key that was active when it was taken.

### Restore Procedure

`POST /api/v1/backups/{id}/restore` (`owner` only) decrypts the named archive and replaces the *entire* current database and staging directory with its contents — this is destructive by definition, and also reverts `backup_jobs`' own bookkeeping to its state at dump time (the row for the backup being restored from reads `running`, not `completed`, since the dump necessarily precedes that status update — an inherent property of backing up the whole database, not a bug). There is no API-level confirmation step; a dashboard confirmation dialog is future work.

## Audit Events

`audit_events` (M9) records a non-sensitive row for every sensitive mutation made through the write APIs (account/transaction/bill/income CRUD, membership changes, household bootstrap): `actor_user_id`, `action` (e.g. `account.created`), `entity_type`, `entity_id`, and a short `summary`. Audit rows are `Internal` per the security model and must never contain `Restricted`/`Sensitive` values — no amounts, balances, passwords, or tokens (enforced by `family_cfo_api/audit.py` and asserted by tests). M9 audits the mutations it introduces; extending coverage to the other existing mutation points (auth login, pairing, imports apply/discard, reports, backups) is a tracked backlog follow-up.
