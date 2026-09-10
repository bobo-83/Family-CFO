# Backup and Restore Guide

Family CFO takes encrypted backups of the whole database and the shared
import/document tree, and can restore from them. This is separate from
volume-level snapshots of your host (do both).

Backup configuration and history are **box-global**, not household-owned. Every
archive contains all hosted households, and only a system administrator with the
`backups.manage` right may configure, create, list, delete, or restore backups.

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

A single encrypted archive bundles:

- a PostgreSQL dump (`pg_dump --format=custom`), and
- a tar of the import/document staging tree.

Archives are stored in the local `backups` volume and can also be copied to an
SMB share. `backup_jobs` rows and the box-global retention journal track their
state.

## Retention and capacity

Local and off-box destinations have independent policies, logical byte caps, and
minimum free-space reserves. A tiered policy keeps every archive in the recent
window, one archive per UTC day through the daily horizon, and one per ISO week
(Monday bucket) through the weekly horizon. Fresh installations default to
`3 / 14 / 90` days. `keep_all` disables time-based deletion, but an enabled
logical cap can still remove eligible older archives.

The newest eligible archive and uncertain evidence (for example unreadable,
mismatched, orphaned, or newer-version files) are protected from automatic
pruning. Protected physical bytes do not count toward the logical cap but still
consume real storage, so a destination can remain constrained even after every
safe deletion.

Capacity is an observation, not a reservation or guarantee. Local status uses
caller-available `statvfs` bytes; SMB status uses caller-available
`smbclient.stat_volume()` bytes and therefore reflects the configured account's
quota and permissions. An unsupported query is `unknown`, not zero. Another
process or NAS client can consume space after a successful check, and the local
preflight cannot measure a separate system temporary volume used during dump or
restore. Actual `ENOSPC`, upload, and rename results remain authoritative.

`GET /api/v1/backups/status` reports configured targets separately from visible,
read-probed recovery candidates. Its oldest/newest dates do **not** prove that
the encryption key is available, the archive is complete, migrations succeed,
or a destructive restore works. Only a restore test verifies those properties.

## Taking a backup

- **On demand** (system administrator), from the dashboard **Backups** page, or:
  ```bash
  curl -sk -X POST https://localhost:8443/api/v1/backups \
    -H "authorization: Bearer <system-admin-token>"
  ```
- **Automatically** — the worker follows the persisted box-global cadence.
  Cadence `off` stops scheduled creation, but hourly maintenance and status
  reconciliation still run.

A failed off-box copy never changes a completed local backup to failed.

## Off-box backup to a Synology (SMB)

A backup that only lives in the local volume dies with the box. Family CFO can
push each encrypted archive to a Synology or compatible SMB share without a host
mount.

Configure the box-global destination through the Backups client or
`GET/PUT /api/v1/backups/config`:

- SMB address, share, optional subfolder, username, password, and optional domain;
- creation cadence;
- independent local/off-box tier policies;
- independent logical maximum bytes and caller-available free-space reserves.

The saved password is encrypted on the box and is never returned. Leaving the
password field absent retains it; an explicit empty value clears it. Destination
check verifies write access and reports a capacity observation, but is not a
restore test.

### Supported-NAS manual check

Before relying on a NAS model/firmware/account combination, perform this check
with synthetic data. Enter credentials yourself in the dashboard or a
user-controlled credential tool; never paste them into an issue, transcript,
command line, or log.

1. Set a test account quota and confirm destination-check reports
   caller-available `stat_volume` capacity consistent with that quota.
2. Create a backup and confirm the `.partial` upload is renamed to one final
   `.enc` archive with no leftover partial.
3. List remote backups and confirm the new archive is present and readable.
4. Delete that disposable test archive through Family CFO and confirm list no
   longer returns it.
5. Repeat once with capacity below reserve plus the next-backup estimate; verify
   upload is blocked or fails truthfully while the completed local archive stays
   available.

This records the required stat-volume/quota/upload/rename/list/delete behavior;
it does not certify every NAS firmware or concurrent-storage failure mode.

## Upgrade and activation

Migrations `0093_box_global_backup_settings` and
`0094_backup_delete_intents` create the singleton configuration, retention
journal, prune metadata, and durable delete-intent action.

On the first repository read after upgrade, legacy household destination/cadence
and legacy retention environment values bootstrap the singleton once. The
database is authoritative afterward; changing
`FAMILY_CFO_BACKUP_RETENTION_COUNT` or
`FAMILY_CFO_OFFBOX_BACKUP_RETENTION_DAYS` no longer edits active retention.
Those inputs and old household columns remain for one compatibility release only.

Every upgraded installation starts with retention review required and automatic
time/cap pruning paused. Status still previews pending deletions. A system
administrator must review the independent policies/caps/reserves and explicitly
confirm them before a later locked maintenance pass may prune. Confirmation does
not delete files inside the configuration request.

For rollout, migrate and deploy the API and worker first. Verify the live
configuration/status endpoints and real local/SMB paths, then deploy the web
client and TestFlight/OTA separately. Do not expose a `0.160` client before the
API/worker contract and maintenance behavior are live.

## Restoring

Restore is **destructive**: it replaces the entire current database and staging
tree with the backup's contents. It requires a system administrator.

- From the dashboard **Backups** page (with a confirmation dialog), or:
  ```bash
  curl -sk -X POST https://localhost:8443/api/v1/backups/<backup-id>/restore \
    -H "authorization: Bearer <system-admin-token>"
  ```
- After loss of the local volume, use the remote list and remote-restore flow.
  You still need the encryption key that encrypted the chosen archive.

Restore preserves the current operational backup configuration and encrypted SMB
credential across the database replacement, reapplies migrations, rotates both
destination generations, and pauses automatic retention for administrator review.
It can roll `backup_jobs` bookkeeping back to the dump-time state; reconciliation
repairs interrupted/current evidence without deleting protected newer orphans.

## Downgrade and rollback

Before starting an older release, downgrade the database with that release's
supported procedure and explicitly set the legacy retention environment values.
Migration `0093` copies representable cadence/SMB fields to every household and
copies a shared cap only when local and off-box caps are equal. Tier policies,
independent unequal caps/reserves, activation state, and recovery-status semantics
cannot be represented by old code. Downgrade does not recreate already-pruned
archives or discarded journal/history fields; take and verify a backup first.

## Version and test notes

`pg_dump`/`pg_restore` in the API image and the PostgreSQL server must share a
major version. The shipped Compose stack pins PostgreSQL and client 17 together;
keep them in step if you change the database image.

The automated matrix covers pure tier boundaries, migration/bootstrap and
downgrade behavior on SQLite, migration `0093`/`0094` plus singleton bootstrap
on PostgreSQL 17, storage failure seams, status/API contracts, OpenAPI
compatibility, and the production advisory-lock conflict/connection-loss path.
CI sets
`FAMILY_CFO_REQUIRE_POSTGRESQL=1`; a missing URL, driver, or server fails instead
of skipping. The NAS checklist above and a guarded restore of a newly created
archive remain operator checks because they require the deployed storage and
user-controlled credentials.
