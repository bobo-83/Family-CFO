from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from family_cfo_backup import (
    BackupAdapter,
    BackupCommandError,
    BackupEncryptionError,
    PgDumpBackupAdapter,
    SqliteFileBackupAdapter,
    build_archive,
    decrypt,
    encrypt,
    extract_archive,
)
from sqlalchemy import text
from sqlalchemy.engine import Engine

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import audit, banksync, repository, smb_backup
from family_cfo_api.config import Settings

logger = logging.getLogger(__name__)


class BackupCompatibilityError(ValueError):
    """Raised when an archive's manifest says this app is too old to restore it
    (a newer app version or a database revision this build doesn't know)."""


# M98/M101: how many minutes between backups for each schedule option.
BACKUP_CADENCE_MINUTES = {
    "every_15min": 15,
    "hourly": 60,
    "every_6h": 360,
    "daily": 1440,
    "weekly": 10080,
}


def run_due_backups(engine: Engine, settings: Settings, *, now: datetime | None = None) -> int:
    """Back up every household whose backup cadence has elapsed since its last
    completed backup (M98/M101); the worker polls this every few minutes. Returns
    how many households were backed up this pass.

    Deliberately a module-level, importable function — NOT a closure inside the
    worker's scheduler — so the wiring is unit-testable. The old closure form let a
    bare `NameError` ship to production (M108, ADR 0019); scheduled work must be
    covered like anything else."""
    now = now or datetime.now(UTC)

    def backed_up_within(minutes: int) -> bool:
        cutoff = now - timedelta(minutes=minutes)
        for job in repository.list_backup_jobs(engine):
            if job.status != "completed" or not job.completed_at:
                continue
            # SQLite hands back naive datetimes, Postgres tz-aware ones; treat a
            # naive value as UTC so the cadence gate works on both.
            completed_at = job.completed_at
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=UTC)
            if completed_at >= cutoff:
                return True
        return False

    def smb_target_for(household) -> smb_backup.SmbTarget | None:
        if not (
            household.backup_smb_host
            and household.backup_smb_share
            and household.backup_smb_username
            and household.backup_smb_password_encrypted
        ):
            return None
        return smb_backup.SmbTarget(
            host=household.backup_smb_host,
            share=household.backup_smb_share,
            folder=household.backup_smb_folder,
            username=household.backup_smb_username,
            password=banksync.decrypt_credential(
                settings, household.backup_smb_password_encrypted),
            domain=household.backup_smb_domain,
        )

    backed_up = 0
    for household_id in repository.list_households(engine):
        household = repository.get_household(engine, household_id)
        if household is None:
            continue
        frequency = household.backup_frequency or "daily"
        if frequency == "off":
            continue
        # A small grace so a slightly-early poll still fires.
        minutes = BACKUP_CADENCE_MINUTES.get(frequency, 1440)
        if backed_up_within(max(1, minutes - 2)):
            continue
        backup_job_id = run_backup_once(
            engine,
            database_url=settings.database_url,
            staging_dir=settings.import_staging_dir,
            backup_dir=settings.backup_dir,
            encryption_key=settings.backup_encryption_key,
            retention_count=settings.backup_retention_count,
            smb_target=smb_target_for(household),
            max_bytes=household.backup_max_bytes,
            offbox_retention_days=settings.offbox_backup_retention_days,
        )
        # #62: a manual backup writes `backup.created`; a scheduled one wrote
        # nothing, so the box's own snapshots left no trace — and a snapshot's
        # existence is the fact a later `backup.restored` points at. Actor is
        # None: nobody pressed anything, the cadence did.
        job = repository.get_backup_job(engine, backup_job_id)
        audit.write_audit(
            engine,
            household_id,
            None,
            "backup_job",
            "backup_job",
            backup_job_id,
            f"Scheduled backup ran on the {frequency} cadence for household "
            f"{household_id} ({job.status if job else 'unknown'})",
        )
        backed_up += 1
    return backed_up


def restore_from_bytes(
    ciphertext: bytes,
    *,
    database_url: str,
    staging_dir: str,
    encryption_key: str | None,
) -> None:
    """Restore from an encrypted archive already in memory — the SMB-download path."""
    _restore_ciphertext(
        ciphertext,
        database_url=database_url,
        staging_dir=staging_dir,
        encryption_key=encryption_key,
    )


class BackupConfigurationError(ValueError):
    """Raised for a missing encryption key or an unsupported database_url scheme."""


def _current_schema_revision(engine: Engine) -> str | None:
    """The alembic revision the live database is stamped at, or None (test
    databases built from metadata.create_all have no alembic_version table)."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 — no alembic_version table is a normal case
        return None


def _known_migrations() -> tuple[set[str], str | None]:
    """(every revision id this build ships, the head revision). alembic.ini sits
    in the working directory for both the container and the test runner; when it
    isn't reachable the guard degrades to (empty set, None) — permissive."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        revisions = {script.revision for script in scripts.walk_revisions()}
        return revisions, scripts.get_current_head()
    except Exception:  # noqa: BLE001
        return set(), None


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _check_restore_compatibility(manifest: dict | None) -> None:
    if manifest is None:
        # Archives from before backups carried a manifest. Restoring one from
        # the same install it came from is the common (and safe) case.
        logger.warning("restore: archive has no version manifest (pre-versioning backup)")
        return
    backup_version = manifest.get("app_version")
    if backup_version and _version_tuple(str(backup_version)) > _version_tuple(APP_VERSION):
        raise BackupCompatibilityError(
            f"This backup was made by Family CFO {backup_version}, which is newer than "
            f"this box ({APP_VERSION}). Update the app first, then restore."
        )
    revision = manifest.get("schema_revision")
    if revision:
        known, _head = _known_migrations()
        if known and str(revision) not in known:
            raise BackupCompatibilityError(
                f"This backup uses database revision {revision}, which this version of "
                "the app doesn't know. Update the app first, then restore."
            )


def _migrate_after_restore(database_url: str, manifest: dict | None) -> None:
    """Bring a just-restored OLDER database forward to this build's schema. Only
    runs when the manifest names a known, non-head revision — a same-version
    restore (and a legacy manifest-less one) is left exactly as restored."""
    if manifest is None:
        return
    revision = manifest.get("schema_revision")
    if not revision:
        return
    known, head = _known_migrations()
    if str(revision) not in known or str(revision) == head:
        return
    logger.info("restore: migrating restored database %s -> %s", revision, head)
    result = subprocess.run(
        [
            sys.executable, "-m", "alembic", "-c", "alembic.ini",
            "-x", f"database_url={database_url}",
            "upgrade", "head",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # The data itself is restored; only the schema catch-up failed. Don't
        # fail the restore — the API entrypoint reruns `upgrade head` on the
        # next restart, which is the recovery path.
        logger.error(
            "restore: post-restore migration failed (restart the app to retry): %s",
            result.stderr.strip()[-2000:],
        )


def select_backup_adapter(database_url: str) -> BackupAdapter:
    scheme = database_url.split("://", 1)[0].split("+")[0]
    if scheme == "postgresql":
        return PgDumpBackupAdapter(database_url)
    if scheme == "sqlite":
        path = database_url.split("///", 1)[-1] if "///" in database_url else ""
        if not path or path == ":memory:":
            raise BackupConfigurationError(
                "backups require a file-based sqlite database_url in this environment"
            )
        return SqliteFileBackupAdapter(Path(path))
    raise BackupConfigurationError(f"no backup adapter for database scheme {scheme!r}")


def _tar_directory(directory: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        if os.path.isdir(directory):
            tar.add(directory, arcname=".")
    return buffer.getvalue()


def _untar_directory(data: bytes, directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    buffer = io.BytesIO(data)
    with tarfile.open(fileobj=buffer, mode="r") as tar:
        tar.extractall(directory, filter="data")


def verify_destination(path: str) -> tuple[bool, str | None]:
    """Can we actually write backups to this path? Returns (ok, reason). The reason
    is user-facing, so it names the likely fix — a share that isn't mounted, a
    permission problem, a missing folder, or a full disk."""
    if not path.strip():
        return False, "No destination path set."
    if not os.path.isdir(path):
        return False, (
            f"“{path}” isn't a directory the server can see. Mount your Synology "
            "share there on the box first (see the setup instructions)."
        )
    probe = os.path.join(path, ".family-cfo-write-test")
    try:
        with open(probe, "wb") as handle:
            handle.write(b"ok")
        os.remove(probe)
    except PermissionError:
        return False, f"Permission denied writing to “{path}”. Check the share's write permissions."
    except OSError as exc:
        return False, f"Can't write to “{path}”: {exc.strerror or exc}."
    return True, None


def _copy_to_destination(source_path: str, destination_dir: str, filename: str) -> None:
    """Copy a finished .enc into the off-box destination. Raises OSError on failure."""
    import shutil

    os.makedirs(destination_dir, exist_ok=True)
    shutil.copy2(source_path, os.path.join(destination_dir, filename))


def run_backup_once(
    engine: Engine,
    *,
    database_url: str,
    staging_dir: str,
    backup_dir: str,
    encryption_key: str | None,
    retention_count: int,
    smb_target: smb_backup.SmbTarget | None = None,
    max_bytes: int | None = None,
    offbox_retention_days: int = 0,
) -> str:
    """Create one encrypted backup archive. Returns the backup_job id regardless of outcome.

    Called directly by tests (synchronous, deterministic) and polled by the
    worker's scheduled job, following the same pattern M7 established for
    import processing.
    """
    job = repository.create_backup_job(engine)
    repository.update_backup_job(engine, job.id, status="running")

    try:
        if not encryption_key:
            raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")

        adapter = select_backup_adapter(database_url)
        os.makedirs(backup_dir, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            dump_path = Path(tmp_dir) / "database.dump"
            adapter.dump_database(dump_path)
            database_dump = dump_path.read_bytes()

        documents_tar = _tar_directory(staging_dir)
        # Sealed inside the encrypted archive so it survives any round-trip
        # (Synology share, USB stick) — the restore side reads it to decide
        # whether this app version can safely restore the archive.
        manifest = {
            "app_version": APP_VERSION,
            "schema_revision": _current_schema_revision(engine),
        }
        archive = build_archive(database_dump, documents_tar, manifest=manifest)
        ciphertext = encrypt(encryption_key, archive)

        storage_path = f"{job.id}.enc"
        full_path = os.path.join(backup_dir, storage_path)
        with open(full_path, "wb") as backup_file:
            backup_file.write(ciphertext)

        # M98: also push the encrypted archive off-box to the Synology share over
        # SMB, so a dead box doesn't take its backups with it. An upload failure
        # never fails the backup itself (the on-box copy is safe) — it's recorded
        # so the app can warn, with the reason.
        remote_status = "skipped"
        remote_error: str | None = None
        if smb_target is not None:
            try:
                # The remote copy carries the app version in its name so the
                # rebuild-restore list can label archives without decrypting.
                smb_backup.upload(smb_target, full_path, f"{job.id}.v{APP_VERSION}.enc")
                remote_status = "synced"
            except Exception as exc:  # noqa: BLE001 — recorded, not fatal
                remote_status = "failed"
                remote_error = smb_backup._friendly(exc)
                logger.warning("backup smb upload failed backup_id=%s error=%s", job.id, exc)

        repository.update_backup_job(
            engine,
            job.id,
            status="completed",
            storage_path=storage_path,
            size_bytes=len(ciphertext),
            remote_status=remote_status,
            remote_error=remote_error,
            app_version=APP_VERSION,
            schema_revision=manifest["schema_revision"],
        )
        logger.info(
            "backup completed backup_id=%s size_bytes=%s remote=%s",
            job.id,
            len(ciphertext),
            remote_status,
        )
    except (BackupCommandError, BackupEncryptionError, BackupConfigurationError, OSError) as exc:
        repository.update_backup_job(
            engine, job.id, status="failed", error_message=f"{type(exc).__name__}: {exc}"
        )
        logger.warning("backup failed backup_id=%s error_type=%s", job.id, type(exc).__name__)
        return job.id

    _apply_retention(engine, backup_dir, retention_count)
    if max_bytes and max_bytes > 0:
        _enforce_size_cap_local(engine, backup_dir, max_bytes)
        if smb_target is not None:
            try:
                _enforce_size_cap_remote(smb_target, max_bytes)
            except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
                logger.warning("remote size-cap prune failed: %s", exc)
    # #192: bound the off-box copies by AGE too, so a deleted household's
    # ciphertext has a real erasure horizon. Independent of the size cap.
    if smb_target is not None and offbox_retention_days > 0:
        try:
            _enforce_age_cap_remote(smb_target, offbox_retention_days)
        except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
            logger.warning("remote age-cap prune failed: %s", exc)
    return job.id


def _enforce_size_cap_local(engine: Engine, backup_dir: str, max_bytes: int) -> None:
    """Delete the oldest on-box backups until the combined size is under the cap
    (always keeping the newest one)."""
    jobs = repository.list_completed_backup_jobs_for_retention(engine)  # oldest first
    total = sum(job.size_bytes or 0 for job in jobs)
    for job in jobs[:-1]:  # never delete the most recent
        if total <= max_bytes:
            break
        if job.storage_path:
            full_path = os.path.join(backup_dir, job.storage_path)
            if os.path.exists(full_path):
                os.remove(full_path)
        repository.delete_backup_job(engine, job.id)
        total -= job.size_bytes or 0
        logger.info("backup size-cap pruned backup_id=%s", job.id)


def _enforce_size_cap_remote(smb_target: smb_backup.SmbTarget, max_bytes: int) -> None:
    """Delete the oldest Synology backups until the combined size is under the cap."""
    items = smb_backup.list_backups(smb_target)  # newest first
    total = sum(item["size_bytes"] for item in items)
    for item in reversed(items[1:]):  # oldest first, keep the newest
        if total <= max_bytes:
            break
        smb_backup.delete(smb_target, item["filename"])
        total -= item["size_bytes"]
        logger.info("remote size-cap pruned filename=%s", item["filename"])


def _enforce_age_cap_remote(
    smb_target: smb_backup.SmbTarget, retention_days: int, *, now: float | None = None
) -> int:
    """#192: delete Synology backups older than retention_days — the bounded
    erasure horizon for a deleted household. The newest archive is ALWAYS kept
    (so a household that stops backing up still has one restorable copy).
    Returns how many were deleted."""
    import time

    now = now if now is not None else time.time()
    cutoff = now - retention_days * 86400
    items = smb_backup.list_backups(smb_target)  # newest first
    deleted = 0
    for item in items[1:]:  # keep the single newest, whatever its age
        if item["modified_at"] < cutoff:
            smb_backup.delete(smb_target, item["filename"])
            deleted += 1
            logger.info("remote age-cap pruned filename=%s", item["filename"])
    return deleted


def _apply_retention(engine: Engine, backup_dir: str, retention_count: int) -> None:
    completed = repository.list_completed_backup_jobs_for_retention(engine)
    excess = completed[: max(0, len(completed) - retention_count)]
    for job in excess:
        if job.storage_path:
            full_path = os.path.join(backup_dir, job.storage_path)
            if os.path.exists(full_path):
                os.remove(full_path)
        repository.mark_backup_job_pruned(engine, job.id)
        logger.info("backup pruned backup_id=%s", job.id)


def restore_backup(
    engine: Engine,
    backup_job_id: str,
    *,
    database_url: str,
    staging_dir: str,
    backup_dir: str,
    encryption_key: str | None,
) -> None:
    job = repository.get_backup_job(engine, backup_job_id)
    if job is None:
        raise ValueError(f"backup job {backup_job_id} not found")
    if job.status != "completed" or not job.storage_path:
        raise ValueError(
            f"backup job {backup_job_id} is not a completed backup with a stored archive"
        )
    if not encryption_key:
        raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")

    full_path = os.path.join(backup_dir, job.storage_path)
    with open(full_path, "rb") as backup_file:
        ciphertext = backup_file.read()

    _restore_ciphertext(
        ciphertext,
        database_url=database_url,
        staging_dir=staging_dir,
        encryption_key=encryption_key,
    )
    logger.info("backup restored backup_id=%s", backup_job_id)


def _restore_ciphertext(
    ciphertext: bytes,
    *,
    database_url: str,
    staging_dir: str,
    encryption_key: str | None,
) -> None:
    if not encryption_key:
        raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")
    archive = decrypt(encryption_key, ciphertext)
    database_dump, documents_tar, manifest = extract_archive(archive)
    _check_restore_compatibility(manifest)
    adapter = select_backup_adapter(database_url)
    with tempfile.TemporaryDirectory() as tmp_dir:
        dump_path = Path(tmp_dir) / "database.dump"
        dump_path.write_bytes(database_dump)
        adapter.restore_database(dump_path)
    _untar_directory(documents_tar, staging_dir)
    _migrate_after_restore(database_url, manifest)


def _app_version_from_filename(name: str) -> str | None:
    """`{job_id}.v{app_version}.enc` → the app version; None for the older
    unversioned `{job_id}.enc` names still sitting on the share."""
    base = name.removesuffix(".enc")
    if ".v" not in base:
        return None
    candidate = base.rsplit(".v", 1)[1]
    return candidate if candidate and candidate[0].isdigit() else None


def list_remote_backups(path: str) -> list[dict]:
    """The .enc archives sitting on the off-box share, newest first — the restore
    list the user picks from after a box rebuild (when the local job history is
    gone). Each item: filename, size_bytes, modified_at (epoch seconds)."""
    if not path or not os.path.isdir(path):
        return []
    items: list[dict] = []
    for name in os.listdir(path):
        if not name.endswith(".enc"):
            continue
        full = os.path.join(path, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        items.append(
            {
                "filename": name,
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
                "app_version": _app_version_from_filename(name),
            }
        )
    items.sort(key=lambda item: item["modified_at"], reverse=True)
    return items


def restore_from_path(
    engine: Engine,
    enc_path: str,
    *,
    database_url: str,
    staging_dir: str,
    encryption_key: str | None,
) -> None:
    """Restore directly from an .enc file on the off-box share — the disaster path
    when the box was rebuilt and there's no backup_jobs row to reference."""
    if not os.path.isfile(enc_path):
        raise ValueError(f"backup file not found: {enc_path}")
    with open(enc_path, "rb") as backup_file:
        ciphertext = backup_file.read()
    _restore_ciphertext(
        ciphertext,
        database_url=database_url,
        staging_dir=staging_dir,
        encryption_key=encryption_key,
    )
    logger.info("backup restored from share path=%s", enc_path)
