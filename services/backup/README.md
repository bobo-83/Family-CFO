# Backup

Encrypted database and document backups for Family CFO, implemented as the `family_cfo_backup`
package. It has no database or HTTP dependency -- callers (see
`apps/api/src/family_cfo_api/backup_processing.py`) select an adapter, build/encrypt an archive,
and persist `backup_jobs` rows.

## M8 Scope

- `BackupAdapter`: a `Protocol` with `dump_database(destination: Path)` and
  `restore_database(source: Path)` -- deliberately narrow. Document-tree handling is identical
  regardless of database backend, so it lives once in the caller, not duplicated per adapter.
- `PgDumpBackupAdapter` (real): shells out to `pg_dump`/`pg_restore` against a PostgreSQL
  `database_url`, converting a SQLAlchemy `postgresql+psycopg://` URL to the plain
  `postgresql://` form libpq CLI tools expect. This sandboxed development environment has no
  `pg_dump`/`pg_restore` binary and no live PostgreSQL server, so it is only unit/contract-tested
  here (command construction, error handling) with a stubbed subprocess call -- the same
  "test the seam, not the vendor binary" approach M4 used for the vLLM HTTP layer and M7 used for
  OCR (ADR 0007).
- `SqliteFileBackupAdapter` (test-only): file-copies a file-based SQLite database. Exercises the
  identical dump/restore seam against a real file on disk, so encryption/retention/restore
  behavior is covered by tests without a PostgreSQL server. Never used against a `:memory:` URL.
- `build_archive`/`extract_archive`: bundle a database dump and a document-tree tar into one tar
  archive so a single encryption key covers both.
- `encrypt`/`decrypt`/`generate_key`: Fernet symmetric encryption (`cryptography` package). A
  missing or invalid key raises `BackupEncryptionError` rather than falling back to an unencrypted
  archive -- there is no unencrypted-backup code path.

## Assumptions and Limitations

- No backup-key recovery or rotation mechanism. Losing the key makes existing backups permanently
  unrecoverable; this is an open threat-model question the security model already flags, not
  resolved here.
- No encrypted Qdrant backup — the `qdrant` vector store holds only regenerable embeddings, not source-of-truth data, so it is intentionally out of scope for backups (re-embed from PostgreSQL after a restore).

## Tests

```bash
cd services/backup
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
```
