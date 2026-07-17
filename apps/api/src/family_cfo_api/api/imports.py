from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, undo_actions
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    ErrorResponse,
    ImportCreateRequest,
    ImportListResponse,
    ImportRecord,
)

router = APIRouter(tags=["Imports"])
logger = logging.getLogger(__name__)


def _to_schema(record: repository.ImportRecord) -> ImportRecord:
    return ImportRecord(
        id=record.id,
        source_type=record.source_type,
        filename=record.filename,
        status=record.status,
        error_message=record.error_message,
        skipped_row_count=record.skipped_row_count,
        created_at=record.created_at,
    )


def _staged_file_path(settings: Settings, storage_path: str) -> str:
    return os.path.join(settings.import_staging_dir, storage_path)


@router.get(
    "/imports",
    operation_id="listImports",
    response_model=ImportListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List import jobs",
)
async def list_imports(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> ImportListResponse:
    records = repository.list_imports(engine, session.household_id)
    return ImportListResponse(imports=[_to_schema(record) for record in records])


@router.post(
    "/imports",
    operation_id="createImport",
    response_model=ImportRecord,
    status_code=201,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Register a financial data import",
)
async def create_import(
    payload: ImportCreateRequest,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> ImportRecord:
    record = repository.create_import(
        engine,
        household_id=session.household_id,
        account_id=payload.account_id,
        source_type=payload.source_type,
        filename=payload.filename,
    )
    return _to_schema(record)


@router.post(
    "/imports/{import_id}/file",
    operation_id="uploadImportFile",
    response_model=ImportRecord,
    status_code=202,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Import not found", "model": ErrorResponse},
    },
    summary="Upload the file for a registered import",
)
async def upload_import_file(
    import_id: str,
    file: UploadFile,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> ImportRecord:
    record = repository.get_import(engine, session.household_id, import_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    if record.status != "pending":
        raise HTTPException(
            status_code=400, detail=f"Import is not awaiting a file (status={record.status})"
        )

    # Bounded read: never buffer more than the cap + 1 byte to detect overflow.
    content = await file.read(settings.max_upload_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the maximum allowed size")

    safe_filename = os.path.basename(file.filename or "upload")
    storage_path = f"{import_id}/{safe_filename}"
    full_path = _staged_file_path(settings, storage_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as staged_file:
        staged_file.write(content)

    repository.create_import_file(
        engine,
        import_id=import_id,
        storage_path=storage_path,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
    )

    logger.info("import file staged import_id=%s size_bytes=%s", import_id, len(content))

    updated = repository.get_import(engine, session.household_id, import_id)
    assert updated is not None
    return _to_schema(updated)


@router.post(
    "/imports/{import_id}/apply",
    operation_id="applyImport",
    response_model=ImportRecord,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Import not found", "model": ErrorResponse},
    },
    summary="Confirm an import's pending transactions as reviewed",
)
async def apply_import(
    import_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> ImportRecord:
    record = repository.get_import(engine, session.household_id, import_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")

    previous_status = record.status
    updated_count = repository.apply_import(engine, session.household_id, import_id)
    logger.info("import applied import_id=%s transactions_updated=%s", import_id, updated_count)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "import.applied",
        "import",
        import_id,
        f"Applied import ({updated_count} transactions confirmed)",
        undo_token=undo_actions.import_applied(import_id, previous_status),
    )

    updated = repository.get_import(engine, session.household_id, import_id)
    assert updated is not None
    return _to_schema(updated)


@router.post(
    "/imports/{import_id}/discard",
    operation_id="discardImport",
    response_model=ImportRecord,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Import not found", "model": ErrorResponse},
    },
    summary="Discard an import and delete its pending transactions",
)
async def discard_import(
    import_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> ImportRecord:
    record = repository.get_import(engine, session.household_id, import_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")

    deleted_count = repository.discard_import(engine, session.household_id, import_id)
    logger.info("import discarded import_id=%s transactions_deleted=%s", import_id, deleted_count)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "import.discarded",
        "import",
        import_id,
        f"Discarded import ({deleted_count} pending transactions deleted)",
    )

    updated = repository.get_import(engine, session.household_id, import_id)
    assert updated is not None
    return _to_schema(updated)
