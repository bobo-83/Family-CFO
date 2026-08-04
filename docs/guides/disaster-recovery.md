# Disaster recovery: restoring onto fresh hardware

What you need in your password manager, gathered from the Backups screen and
the box's `.env`:

1. **Backup key** (`FAMILY_CFO_BACKUP_ENCRYPTION_KEY`) — opens the backup
   archives on the Synology.
2. **Recovery key** (`FCFO-…`, shown once at creation) — per household;
   unlocks the content inside when no other key is available.
3. **Master key** (`FAMILY_CFO_MASTER_KEY`) — optional but convenient; with
   it, step 4 below is unnecessary.

## The restore

1. Stand up the stack on the new machine (`docker compose up -d`); put the
   **backup key** in `.env`. If you saved the old **master key**, put it in
   `.env` too — otherwise let the box generate a fresh one.
2. Restore the newest archive from the Synology (Backups → Restore). The
   version-manifest guard refuses archives newer than the app — update first
   if it says so.
3. Sign in. **With the old master key restored, you are done.**
4. **Without it**, the household is locked (HTTP 423 behind the scenes) and
   heals through any one of:
   - a **member password** sign-in (the normal case — recovery is automatic),
   - a paired **iPhone** opening the app (silent device unlock),
   - the **recovery key**: Backups → Restore keys → "Unlock with recovery
     key". 
   The first successful unlock re-mints the box's key copy under the new
   master key; after that everything is normal, including overnight jobs.

Sealed households behave the same, except no box copy is re-minted — that is
their guarantee. They unlock per session, as always.

## What is unrecoverable

A household that loses **every** member password, **every** paired device,
the recovery key, AND the master key cannot be read by anyone — that is the
design (ADR 0072), stated here so nobody discovers it during a disaster.
