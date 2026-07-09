from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, backup_processing, repository
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_engine, require_role
from family_cfo_api.schemas import BackupJob, BackupJobListResponse, ErrorResponse

router = APIRouter(tags=["Backups"])
logger = logging.getLogger(__name__)


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
    session: repository.SessionContext = Depends(require_role("owner")),
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
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> BackupJob:
    backup_job_id = backup_processing.run_backup_once(
        engine,
        database_url=settings.database_url,
        staging_dir=settings.import_staging_dir,
        backup_dir=settings.backup_dir,
        encryption_key=settings.backup_encryption_key,
        retention_count=settings.backup_retention_count,
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
    session: repository.SessionContext = Depends(require_role("owner")),
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
