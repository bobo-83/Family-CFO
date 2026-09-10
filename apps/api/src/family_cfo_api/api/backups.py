from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from functools import partial

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, backup_processing, banksync, repository, rights, smb_backup
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_engine, require_right
from family_cfo_api.schemas import (
    BackupConfig,
    BackupConfigUpdateRequest,
    BackupDestinationCheckRequest,
    BackupDestinationCheckResponse,
    BackupEncryptionKey,
    BackupJob,
    BackupJobListResponse,
    ErrorResponse,
    RemoteBackup,
    RemoteBackupListResponse,
    RemoteRestoreRequest,
)

router = APIRouter(tags=["Backups"])
logger = logging.getLogger(__name__)


def _restore_boundary(
    engine: Engine, household_id: str, snapshot_at: datetime | None, lead: str
) -> tuple[int | None, str]:
    """#62: measure the audit gap a restore is about to create, and phrase it.

    A restore replaces the WHOLE database, `audit_events` included, so every event
    recorded after the snapshot was taken is destroyed by it. We keep that wholesale
    replace (option 2 in #62) and state the gap instead of trying to carry rows
    across it: the count is taken here, BEFORE the replace, and written afterwards.

    Returns (discarded_count, summary). The count is None when the snapshot's own
    timestamp is unknown — the gap is then real but unmeasurable, and the summary
    says so rather than implying zero.
    """
    if snapshot_at is None:
        return None, (
            f"{lead} — the snapshot's date is unknown, so the audit events recorded "
            "since it was taken were discarded uncounted"
        )
    discarded = repository.count_audit_events_since(engine, household_id, snapshot_at)
    # SQLite hands back naive datetimes, Postgres tz-aware ones — both are UTC.
    aware = snapshot_at if snapshot_at.tzinfo else snapshot_at.replace(tzinfo=UTC)
    taken = aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return discarded, (
        f"{lead} — {discarded} audit event(s) recorded between the {taken} snapshot "
        "and this restore were discarded with it"
    )


def _actor_after_restore(engine: Engine, user_id: str, summary: str) -> tuple[str | None, str]:
    """#68: who the restore audit row can credit, now that the restore has happened.

    The row is written AFTER the database is replaced so it survives the restore it
    describes (#62) — but `audit_events.actor_user_id` is a foreign key to
    `users.id`, and a member who joined after the snapshot was taken has no row in
    it. Naming them fails the constraint and returns 500 for a restore that
    succeeded. So: drop the attribution, keep the record, and say in the summary
    why the actor is empty. The record is the part that matters; the attribution is
    the part the restore made unknowable.

    Checked, not caught: an `IntegrityError` rescue around the write would also
    swallow the genuine failures the #62 row exists to make loud.

    Returns (actor_user_id, summary) — the summary unchanged when the user is there.
    """
    if repository.user_exists(engine, user_id):
        return user_id, summary
    return None, (
        f"{summary}; restored by a member whose account is not present in this "
        "snapshot, so this row has no actor"
    )


def _to_schema(record: repository.BackupJobRecord) -> BackupJob:
    return BackupJob(
        id=record.id,
        status=record.status,
        size_bytes=record.size_bytes,
        error_message=record.error_message,
        started_at=record.started_at,
        completed_at=record.completed_at,
        pruned_at=record.pruned_at,
        created_at=record.created_at,
        remote_status=record.remote_status,
        remote_error=record.remote_error,
        app_version=record.app_version,
    )


@router.get(
    "/backups",
    operation_id="listBackups",
    response_model=BackupJobListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="List backup jobs",
)
async def list_backups(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> BackupJobListResponse:
    records = repository.list_backup_jobs(engine)
    return BackupJobListResponse(backups=[_to_schema(record) for record in records])


# #181: minimum gap between on-demand backups per household — whole-box work
# (pg_dump + SMB push) that a stuck client or loop must not hammer.
_BACKUP_COOLDOWN_SECONDS = 60
_last_manual_backup: float | None = None
_manual_backup_cooldown_lock = threading.Lock()


def reset_backup_cooldown_for_tests() -> None:
    global _last_manual_backup
    with _manual_backup_cooldown_lock:
        _last_manual_backup = None


def _reserve_backup_cooldown() -> tuple[int | None, float | None]:
    import time

    global _last_manual_backup
    now = time.monotonic()
    with _manual_backup_cooldown_lock:
        if _last_manual_backup is not None and now - _last_manual_backup < _BACKUP_COOLDOWN_SECONDS:
            retry = int(_BACKUP_COOLDOWN_SECONDS - (now - _last_manual_backup)) + 1
            return retry, None
        _last_manual_backup = now
        return None, now


def _release_backup_cooldown(reservation: float | None) -> None:
    global _last_manual_backup
    if reservation is None:
        return
    with _manual_backup_cooldown_lock:
        if _last_manual_backup == reservation:
            _last_manual_backup = None


async def _run_sync(function, /, *args, **kwargs):
    """The worker thread—not the request waiter—owns any mutation lease."""
    return await anyio.to_thread.run_sync(
        partial(function, *args, **kwargs), abandon_on_cancel=False
    )


@router.post(
    "/backups",
    operation_id="createBackup",
    response_model=BackupJob,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        429: {"description": "A backup ran moments ago (cooldown)", "model": ErrorResponse},
    },
    summary="Create an on-demand encrypted backup",
)
async def create_backup(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupJob:
    # The anti-hammer rule is box-global; correctness exclusion is the
    # cross-process BackupOperationLock inside the synchronous lifecycle.
    retry_after, reservation = _reserve_backup_cooldown()
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=(
                f"A backup just ran — the next one can start in {retry_after}s. "
                "Your data is already saved."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    def run() -> str:
        config = backup_processing.build_backup_execution_config(engine, settings)
        return backup_processing.run_backup_once(engine, config)

    try:
        backup_job_id = await _run_sync(run)
    except backup_processing.BackupOperationBusyError as exc:
        _release_backup_cooldown(reservation)
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.created",
        "backup_job",
        backup_job_id,
        "Backup requested",
    )
    record = repository.get_backup_job(engine, backup_job_id)
    assert record is not None
    logger.info("backup requested backup_id=%s status=%s", record.id, record.status)
    return _to_schema(record)


# #75: this MUST stay declared above `POST /backups/{backup_id}/restore`. Starlette
# matches in declaration order, so with the parameterised route first this one is
# unreachable — every call lands there as backup_id="remote" and gets 404 "Backup not
# found". It stayed dead because it is the box-rebuild path: the restore you only
# reach for once the box is gone, which is far too late to find out.
@router.post(
    "/backups/remote/restore",
    operation_id="restoreRemoteBackup",
    response_model=BackupDestinationCheckResponse,
    responses={
        400: {"description": "Backup could not be restored", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup file not found on the share", "model": ErrorResponse},
        409: {
            "description": "Backup is from a newer app version than this box",
            "model": ErrorResponse,
        },
    },
    summary="Restore from a backup file on the off-box share (destructive)",
)
async def restore_remote_backup(
    payload: RemoteRestoreRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupDestinationCheckResponse:
    config = backup_processing.build_backup_execution_config(engine, settings)
    target = config.smb_target
    if target is None:
        raise HTTPException(status_code=400, detail="No Synology backup destination is configured")
    # Guard against path traversal — only a bare filename from the share.
    filename = os.path.basename(payload.filename)
    if filename != payload.filename or not filename.endswith(".enc"):
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    # Hold the global mutation lease across share selection/read and restore so
    # maintenance or explicit deletion cannot invalidate the selected source.
    try:
        discarded, restore_summary = await _run_sync(
            backup_processing.restore_remote_backup,
            engine,
            filename,
            config,
            before_restore=lambda snapshot_at: _restore_boundary(
                engine,
                session.household_id,
                snapshot_at,
                f"Restored from {filename}",
            ),
        )
    except smb_backup.SmbStorageError as exc:
        raise HTTPException(status_code=404, detail="Backup file not found on the share") from exc
    except backup_processing.BackupCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except (ValueError, backup_processing.BackupConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # #68: same as the on-box path — the acting member may not exist in the
    # database this archive just became.
    actor_id, restore_summary = _actor_after_restore(engine, session.user_id, restore_summary)
    audit.write_audit(
        engine,
        session.household_id,
        actor_id,
        "backup.restored_remote",
        "backup_file",
        os.path.splitext(filename)[0][:36],
        restore_summary,
    )
    logger.info(
        "backup restored from share filename=%s discarded_audit_events=%s", filename, discarded
    )
    return BackupDestinationCheckResponse(writable=True, reason=None)


@router.post(
    "/backups/{backup_id}/restore",
    operation_id="restoreBackup",
    response_model=BackupJob,
    responses={
        400: {"description": "Backup is not restorable", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup not found", "model": ErrorResponse},
        409: {
            "description": "Backup is from a newer app version than this box",
            "model": ErrorResponse,
        },
    },
    summary="Restore the database and documents from a completed backup (destructive)",
)
async def restore_backup(
    backup_id: str,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupJob:
    record = repository.get_backup_job(engine, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Backup not found")

    # #62: count the audit events this restore is about to destroy BEFORE it runs.
    # `started_at` is the honest bound — the database dump is taken at the top of
    # the job, so anything logged from that moment on is absent from the snapshot.
    discarded, restore_summary = _restore_boundary(
        engine,
        session.household_id,
        record.started_at or record.created_at,
        "Backup restore executed",
    )

    try:
        config = backup_processing.build_backup_execution_config(engine, settings)
        await _run_sync(backup_processing.restore_backup, engine, backup_id, config)
    except backup_processing.BackupCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("backup restored backup_id=%s discarded_audit_events=%s", backup_id, discarded)
    updated = repository.get_backup_job(engine, backup_id)
    assert updated is not None
    # Written AFTER the replace, deliberately: a row written before it would be
    # wiped by the very restore it describes (#62). Which is also why the actor
    # has to be re-checked against the restored database (#68).
    actor_id, restore_summary = _actor_after_restore(engine, session.user_id, restore_summary)
    audit.write_audit(
        engine,
        session.household_id,
        actor_id,
        "backup.restored",
        "backup_job",
        backup_id,
        restore_summary,
    )
    return _to_schema(updated)


@router.get(
    "/backups/config",
    operation_id="getBackupConfig",
    response_model=BackupConfig,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Backup destination + schedule, with the latest backup's status",
)
async def get_backup_config(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> BackupConfig:
    stored = repository.get_backup_settings(engine)
    jobs = repository.list_backup_jobs(engine)
    latest = next((j for j in jobs if j.status in ("completed", "failed")), None)
    shared_max = (
        stored.local_max_bytes if stored.local_max_bytes == stored.offbox_max_bytes else None
    )
    return BackupConfig(
        frequency=stored.frequency,
        smb_host=stored.smb_host,
        smb_share=stored.smb_share,
        smb_folder=stored.smb_folder,
        smb_username=stored.smb_username,
        smb_domain=stored.smb_domain,
        has_password=bool(stored.smb_password_encrypted),
        max_bytes=shared_max,
        latest=_to_schema(latest) if latest else None,
    )


@router.put(
    "/backups/config",
    operation_id="updateBackupConfig",
    response_model=BackupConfig,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Set the backup destination (a mounted share) and schedule",
)
async def update_backup_config(
    payload: BackupConfigUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupConfig:
    # The password is a secret: encrypt it at rest, and only rewrite it when the
    # client actually sent one (blank field = keep the stored password).
    update_password = payload.smb_password is not None
    encrypted = (
        banksync.encrypt_credential(settings, payload.smb_password)
        if payload.smb_password
        else None
    )
    # This legacy response shape updates the global singleton without exposing
    # WI-5 policy/status fields. Its shared max alias deliberately updates both.
    repository.get_backup_settings(engine, settings=settings)
    patch = {
        "frequency": payload.frequency,
        "smb_host": payload.smb_host,
        "smb_share": payload.smb_share,
        "smb_folder": payload.smb_folder,
        "smb_username": payload.smb_username,
        "smb_domain": payload.smb_domain,
        "local_max_bytes": payload.max_bytes,
        "offbox_max_bytes": payload.max_bytes,
    }
    if update_password:
        patch["smb_password_encrypted"] = encrypted or ""
    repository.update_backup_settings(engine, patch, expected_updated_at=None)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.config_updated",
        "household",
        session.household_id,
        "Backup destination/schedule changed",
    )
    return await get_backup_config(session=session, engine=engine)


@router.post(
    "/backups/destination-check",
    operation_id="checkBackupDestination",
    response_model=BackupDestinationCheckResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Test the Synology SMB connection can be written to",
)
async def check_backup_destination(
    payload: BackupDestinationCheckRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupDestinationCheckResponse:
    # Use the just-entered password, or fall back to the stored one when the field
    # was left blank (re-testing a saved target).
    password = payload.smb_password
    if password is None:
        stored = repository.get_backup_settings(engine, settings=settings)
        if stored.smb_password_encrypted:
            password = banksync.decrypt_credential(settings, stored.smb_password_encrypted)
    if not password:
        return BackupDestinationCheckResponse(
            writable=False, reason="Enter the Synology password to test the connection."
        )
    target = smb_backup.SmbTarget(
        host=payload.smb_host,
        share=payload.smb_share,
        folder=payload.smb_folder,
        username=payload.smb_username,
        password=password,
        domain=payload.smb_domain,
        io_timeout_seconds=settings.backup_io_timeout_seconds,
    )
    ok, reason = await _run_sync(smb_backup.verify, target)
    return BackupDestinationCheckResponse(writable=ok, reason=reason)


@router.get(
    "/backups/remote",
    operation_id="listRemoteBackups",
    response_model=RemoteBackupListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="List the .enc backups on the configured off-box share",
)
async def list_remote_backups(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> RemoteBackupListResponse:
    config = backup_processing.build_backup_execution_config(engine, settings)
    target = config.smb_target
    try:
        items = await _run_sync(smb_backup.list_backups, target) if target is not None else []
    except smb_backup.SmbInventoryError as exc:
        raise HTTPException(status_code=503, detail=exc.reason) from exc
    return RemoteBackupListResponse(
        backups=[
            RemoteBackup(
                filename=item["filename"],
                size_bytes=item["size_bytes"],
                modified_at=item["modified_at"],
                app_version=backup_processing._app_version_from_filename(item["filename"]),
            )
            for item in items
        ]
    )


@router.get(
    "/backups/encryption-key",
    operation_id="getBackupEncryptionKey",
    response_model=BackupEncryptionKey,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Reveal the backup encryption key (owner only) so it can be stored safely",
)
async def get_backup_encryption_key(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupEncryptionKey:
    key = settings.backup_encryption_key
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.key_revealed",
        "household",
        session.household_id,
        "Backup encryption key revealed",
    )
    return BackupEncryptionKey(configured=bool(key), key=key)


@router.delete(
    "/backups/{backup_id}",
    operation_id="deleteBackup",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup not found", "model": ErrorResponse},
    },
    summary="Delete an on-box backup",
)
async def delete_backup(
    backup_id: str,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    record = repository.get_backup_job(engine, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    config = backup_processing.build_backup_execution_config(engine, settings)
    try:
        await _run_sync(backup_processing.delete_local_backup, engine, backup_id, config)
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except (ValueError, backup_processing.UnsafeBackupPathError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.deleted",
        "backup_job",
        backup_id,
        "Deleted an on-box backup",
    )
    return Response(status_code=204)


@router.post(
    "/backups/remote/delete",
    operation_id="deleteRemoteBackup",
    response_model=BackupDestinationCheckResponse,
    responses={
        400: {"description": "No destination / invalid filename", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Delete a backup file from the Synology share",
)
async def delete_remote_backup(
    payload: RemoteRestoreRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupDestinationCheckResponse:
    config = backup_processing.build_backup_execution_config(engine, settings)
    target = config.smb_target
    if target is None:
        raise HTTPException(status_code=400, detail="No Synology backup destination is configured")
    filename = os.path.basename(payload.filename)
    if filename != payload.filename or not filename.endswith(".enc"):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    try:
        await _run_sync(backup_processing.delete_remote_backup, engine, filename, config)
    except backup_processing.BackupOperationBusyError as exc:
        raise HTTPException(
            status_code=409, detail="A backup operation is already in progress"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        return BackupDestinationCheckResponse(writable=False, reason=smb_backup._friendly(exc))
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.deleted_remote",
        "backup_file",
        os.path.splitext(filename)[0][:36],
        f"Deleted {filename} from Synology",
    )
    return BackupDestinationCheckResponse(writable=True, reason=None)
