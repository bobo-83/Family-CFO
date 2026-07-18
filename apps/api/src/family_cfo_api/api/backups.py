from __future__ import annotations

import logging
import os

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


def _smb_target(
    household: repository.HouseholdRecord | None,
    settings: Settings,
    *,
    password_override: str | None = None,
) -> smb_backup.SmbTarget | None:
    """Build the SMB target from stored config, decrypting the password — or use a
    just-entered password (for a pre-save test). None when not fully configured."""
    if household is None:
        return None
    if not (household.backup_smb_host and household.backup_smb_share and household.backup_smb_username):
        return None
    password = password_override
    if password is None:
        if not household.backup_smb_password_encrypted:
            return None
        password = banksync.decrypt_credential(settings, household.backup_smb_password_encrypted)
    return smb_backup.SmbTarget(
        host=household.backup_smb_host,
        share=household.backup_smb_share,
        folder=household.backup_smb_folder,
        username=household.backup_smb_username,
        password=password,
        domain=household.backup_smb_domain,
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


@router.post(
    "/backups",
    operation_id="createBackup",
    response_model=BackupJob,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Create an on-demand encrypted backup",
)
async def create_backup(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupJob:
    household = repository.get_household(engine, session.household_id)
    backup_job_id = backup_processing.run_backup_once(
        engine,
        database_url=settings.database_url,
        staging_dir=settings.import_staging_dir,
        backup_dir=settings.backup_dir,
        encryption_key=settings.backup_encryption_key,
        retention_count=settings.backup_retention_count,
        smb_target=_smb_target(household, settings),
        max_bytes=household.backup_max_bytes if household else None,
    )
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


@router.post(
    "/backups/{backup_id}/restore",
    operation_id="restoreBackup",
    response_model=BackupJob,
    responses={
        400: {"description": "Backup is not restorable", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup not found", "model": ErrorResponse},
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

    try:
        backup_processing.restore_backup(
            engine,
            backup_id,
            database_url=settings.database_url,
            staging_dir=settings.import_staging_dir,
            backup_dir=settings.backup_dir,
            encryption_key=settings.backup_encryption_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("backup restored backup_id=%s", backup_id)
    updated = repository.get_backup_job(engine, backup_id)
    assert updated is not None
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.restored",
        "backup_job",
        backup_id,
        "Backup restore executed",
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
    household = repository.get_household(engine, session.household_id)
    jobs = repository.list_backup_jobs(engine)
    latest = next((j for j in jobs if j.status in ("completed", "failed")), None)
    return BackupConfig(
        frequency=household.backup_frequency if household else "daily",
        smb_host=household.backup_smb_host if household else None,
        smb_share=household.backup_smb_share if household else None,
        smb_folder=household.backup_smb_folder if household else None,
        smb_username=household.backup_smb_username if household else None,
        smb_domain=household.backup_smb_domain if household else None,
        has_password=bool(household and household.backup_smb_password_encrypted),
        max_bytes=household.backup_max_bytes if household else None,
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
    repository.set_backup_config(
        engine,
        session.household_id,
        frequency=payload.frequency,
        smb_host=payload.smb_host,
        smb_share=payload.smb_share,
        smb_folder=payload.smb_folder,
        smb_username=payload.smb_username,
        smb_password_encrypted=encrypted,
        smb_domain=payload.smb_domain,
        update_password=update_password,
        max_bytes=payload.max_bytes,
    )
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
        household = repository.get_household(engine, session.household_id)
        if household and household.backup_smb_password_encrypted:
            password = banksync.decrypt_credential(
                settings, household.backup_smb_password_encrypted)
    if not password:
        return BackupDestinationCheckResponse(
            writable=False, reason="Enter the Synology password to test the connection.")
    target = smb_backup.SmbTarget(
        host=payload.smb_host,
        share=payload.smb_share,
        folder=payload.smb_folder,
        username=payload.smb_username,
        password=password,
        domain=payload.smb_domain,
    )
    ok, reason = smb_backup.verify(target)
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
    household = repository.get_household(engine, session.household_id)
    target = _smb_target(household, settings)
    items = smb_backup.list_backups(target) if target is not None else []
    return RemoteBackupListResponse(
        backups=[
            RemoteBackup(
                filename=item["filename"],
                size_bytes=item["size_bytes"],
                modified_at=item["modified_at"],
            )
            for item in items
        ]
    )


@router.post(
    "/backups/remote/restore",
    operation_id="restoreRemoteBackup",
    response_model=BackupDestinationCheckResponse,
    responses={
        400: {"description": "Backup could not be restored", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Backup file not found on the share", "model": ErrorResponse},
    },
    summary="Restore from a backup file on the off-box share (destructive)",
)
async def restore_remote_backup(
    payload: RemoteRestoreRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupDestinationCheckResponse:
    household = repository.get_household(engine, session.household_id)
    target = _smb_target(household, settings)
    if target is None:
        raise HTTPException(status_code=400, detail="No Synology backup destination is configured")
    # Guard against path traversal — only a bare filename from the share.
    filename = os.path.basename(payload.filename)
    if filename != payload.filename or not filename.endswith(".enc"):
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    try:
        ciphertext = smb_backup.download(target, filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Backup file not found on the share") from exc

    try:
        backup_processing.restore_from_bytes(
            ciphertext,
            database_url=settings.database_url,
            staging_dir=settings.import_staging_dir,
            encryption_key=settings.backup_encryption_key,
        )
    except (ValueError, backup_processing.BackupConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "backup.restored_remote",
        "backup_file",
        os.path.splitext(filename)[0][:36],
        f"Restored from {filename}",
    )
    logger.info("backup restored from share filename=%s", filename)
    return BackupDestinationCheckResponse(writable=True, reason=None)


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
    if record.storage_path:
        full_path = os.path.join(settings.backup_dir, record.storage_path)
        if os.path.exists(full_path):
            os.remove(full_path)
    repository.delete_backup_job(engine, backup_id)
    audit.write_audit(
        engine, session.household_id, session.user_id,
        "backup.deleted", "backup_job", backup_id, "Deleted an on-box backup",
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
    household = repository.get_household(engine, session.household_id)
    target = _smb_target(household, settings)
    if target is None:
        raise HTTPException(status_code=400, detail="No Synology backup destination is configured")
    filename = os.path.basename(payload.filename)
    if filename != payload.filename or not filename.endswith(".enc"):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    try:
        smb_backup.delete(target, filename)
    except Exception as exc:  # noqa: BLE001
        return BackupDestinationCheckResponse(writable=False, reason=smb_backup._friendly(exc))
    audit.write_audit(
        engine, session.household_id, session.user_id,
        "backup.deleted_remote", "backup_file", os.path.splitext(filename)[0][:36],
        f"Deleted {filename} from Synology",
    )
    return BackupDestinationCheckResponse(writable=True, reason=None)
