# Backup and Restore Guide

Family CFO takes encrypted backups of the database and the uploaded
import/document tree, and can restore from them. This is separate from
volume-level snapshots of your host (do both).

## The key

Backups are encrypted with a Fernet key from `FAMILY_CFO_BACKUP_ENCRYPTION_KEY`.

- Generate one:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Store it in your own secret manager, **not** only in `.env`.
- **There is no recovery** (ADR 0008). Lose the key and every backup encrypted
  with it is unrecoverable. Rotating the key only affects backups taken after.

Without the key set, backup jobs fail (by design — they never write plaintext).

## What's in a backup

A single encrypted archive bundling:

- a PostgreSQL dump (`pg_dump --format=custom`), and
- a tar of the import/document staging tree.

Backups are stored in the `backups` volume; `backup_jobs` rows track status.
Retention keeps the newest `FAMILY_CFO_BACKUP_RETENTION_COUNT` (default 7)
completed backups and prunes older ones — a failed backup never counts toward or
deletes anything.

## Taking a backup

- **On demand** (owner only), from the dashboard **Backups** page, or:
  ```bash
  curl -sk -X POST https://localhost:8443/api/v1/backups \
    -H "authorization: Bearer <owner-token>"
  ```
- **Automatically** — the worker runs a backup once a day.

## Off-box backup to a Synology (SMB)

A backup that only lives in the `backups` volume dies with the box. Family CFO
can also push each encrypted archive straight to a **Synology (or any SMB share)**
— no host mounting required; the box uploads over SMB itself. Owner only, on the
dashboard **Backups** page (and the iOS app — same capability on both, M98/M99).

On the Synology: enable SMB, create a shared folder, and give a user read/write.
Then fill in the **Off-box backup — Synology (SMB)** card (settings save as you
edit):

- **Synology address** (e.g. `192.168.1.50`) and **Shared folder** (e.g.
  `family-cfo-backups`); optional **Subfolder**.
- **Username** / **Password** — the password is encrypted on the box and never
  shown again (leave blank later to keep the saved one); optional **Domain**
  (default `WORKGROUP`).
- **Schedule** — Daily, Weekly, or Off (this governs the off-box copy).
- **Max total size (GB)** — prune the share to this budget; `0` = no limit.
- **Test connection** verifies the address, share, and credentials before you
  rely on it (`POST /api/v1/backups/destination-check`).

Each backup then shows its off-box result: **“copied to Synology”** on success,
or the SMB error on failure — and a failed off-box copy raises a notification so
it doesn't fail silently. The credentials and schedule live on
`GET/PUT /api/v1/backups/config`.

## Restoring

Restore is **destructive**: it replaces the entire current database and staging
tree with the backup's contents. Owner only.

- From the dashboard **Backups** page (with a confirmation dialog), or:
  ```bash
  curl -sk -X POST https://localhost:8443/api/v1/backups/<backup-id>/restore \
    -H "authorization: Bearer <owner-token>"
  ```
- **From the Synology**, when the local volume is gone (a fresh box, disk loss):
  the **Restore from Synology** list shows the archives found on the share,
  newest first — pick one to restore. Backed by `GET /api/v1/backups/remote` and
  `POST /api/v1/backups/remote/restore` (and `POST /api/v1/backups/remote/delete`
  to remove one from the share). You still need the same **encryption key** — the
  archive on the Synology is encrypted with it.

A restore also rolls back the `backup_jobs` bookkeeping to its state at the
moment the backup was taken — so the restored-from row shows `running`, not
`completed`. That's inherent to backing up the whole database, not a bug.

## Version note

`pg_dump`/`pg_restore` in the API image and the PostgreSQL server must share a
major version. The shipped compose pins them together (postgres:17 + client 17);
if you change the DB image, keep the client in step or restores will fail on
version-specific settings.

## Verifying restore from a clean environment

The restore round trip is covered by the test suite (`test_backup_processing.py`
against a real database file) and was verified against real PostgreSQL in Docker
during M12. To check your own deployment:

```bash
# 1. take a backup and note its id
# 2. change some data (e.g. delete an account)
# 3. restore that backup id
# 4. confirm the data returned
```
