from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, banksync, repository
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    ConnectionCreateRequest,
    ConnectionListResponse,
    ConnectionSyncResult,
    ErrorResponse,
    InstitutionConnection,
)

router = APIRouter(tags=["Connections"])
logger = logging.getLogger(__name__)


def _to_schema(record: repository.InstitutionConnectionRecord) -> InstitutionConnection:
    return InstitutionConnection(
        id=record.id,
        provider=record.provider,
        display_name=record.display_name,
        status=record.status,
        last_synced_at=record.last_synced_at,
        last_sync_error=record.last_sync_error,
        created_at=record.created_at,
    )


@router.get(
    "/connections",
    operation_id="listConnections",
    response_model=ConnectionListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List linked financial institutions",
)
async def list_connections(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> ConnectionListResponse:
    records = repository.list_institution_connections(engine, session.household_id)
    return ConnectionListResponse(connections=[_to_schema(r) for r in records])


@router.post(
    "/connections",
    operation_id="createConnection",
    response_model=InstitutionConnection,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        422: {"description": "Invalid setup token", "model": ErrorResponse},
        503: {"description": "Encryption key not configured", "model": ErrorResponse},
    },
    summary="Link a financial institution (exchange a SimpleFIN setup token)",
)
async def create_connection(
    payload: ConnectionCreateRequest,
    background: BackgroundTasks,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> InstitutionConnection:
    if not settings.backup_encryption_key:
        raise HTTPException(
            status_code=503,
            detail="Set FAMILY_CFO_BACKUP_ENCRYPTION_KEY before linking an institution",
        )
    connector = banksync.SimpleFINConnector()
    try:
        access_url = connector.claim(payload.setup_token)
    except banksync.BankSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record = repository.create_institution_connection(
        engine,
        household_id=session.household_id,
        provider=payload.provider,
        display_name=payload.display_name,
        access_url_encrypted=banksync.encrypt_credential(settings, access_url),
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "connection.created",
        "institution_connection",
        record.id,
        f"Linked institution '{payload.display_name}' via {payload.provider}",
    )
    # First sync runs immediately in the background (the daily worker job would
    # otherwise leave a fresh link empty for up to 24h). Errors are recorded on
    # the connection (last_sync_error), never raised here.
    background.add_task(_initial_sync, engine, settings, record.id, session.household_id)
    return _to_schema(record)


def _initial_sync(
    engine: Engine, settings: Settings, connection_id: str, household_id: str
) -> None:
    record = repository.get_institution_connection(engine, household_id, connection_id)
    if record is None:
        return
    try:
        result = banksync.sync_connection(engine, settings, record)
        logger.info(
            "initial sync completed connection_id=%s imported=%s duplicates=%s",
            connection_id,
            result.imported,
            result.duplicates_skipped,
        )
    except banksync.BankSyncError:
        logger.warning("initial sync failed connection_id=%s (recorded on connection)", connection_id)


@router.delete(
    "/connections/{connection_id}",
    operation_id="deleteConnection",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Connection not found", "model": ErrorResponse},
    },
    summary="Unlink a financial institution",
)
async def delete_connection(
    connection_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    if not repository.delete_institution_connection(engine, session.household_id, connection_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "connection.deleted",
        "institution_connection",
        connection_id,
        "Unlinked institution",
    )
    return Response(status_code=204)


@router.post(
    "/connections/{connection_id}/sync",
    operation_id="syncConnection",
    response_model=ConnectionSyncResult,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Connection not found", "model": ErrorResponse},
        502: {"description": "Institution fetch failed", "model": ErrorResponse},
    },
    summary="Pull the latest statements from a linked institution",
)
async def sync_connection(
    connection_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> ConnectionSyncResult:
    record = repository.get_institution_connection(engine, session.household_id, connection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        result = banksync.sync_connection(engine, settings, record)
    except banksync.BankSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ConnectionSyncResult(
        accounts_synced=result.accounts_synced,
        imported=result.imported,
        duplicates_skipped=result.duplicates_skipped,
    )
