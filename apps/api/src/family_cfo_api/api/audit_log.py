import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import repository, undo_actions
from family_cfo_api.deps import get_engine, require_role
from family_cfo_api.schemas import AuditEvent, AuditEventListResponse, ErrorResponse

router = APIRouter(tags=["Audit"])


def _to_schema(record: repository.AuditEventRecord) -> AuditEvent:
    return AuditEvent(
        id=record.id,
        actor_user_id=record.actor_user_id,
        action=record.action,
        entity_type=record.entity_type,
        entity_id=record.entity_id,
        summary=record.summary,
        created_at=record.created_at,
        undoable=record.undo_token is not None and record.reverted_at is None,
        reverted_at=record.reverted_at,
    )


@router.get(
    "/audit",
    operation_id="listAuditEvents",
    response_model=AuditEventListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="List the household's audit events",
)
async def list_audit_events(
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
) -> AuditEventListResponse:
    records = repository.list_audit_events(engine, session.household_id)
    return AuditEventListResponse(events=[_to_schema(record) for record in records])


@router.post(
    "/audit/{audit_id}/undo",
    operation_id="undoAuditEvent",
    response_model=AuditEvent,
    responses={
        400: {"description": "This action can't be undone", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Audit event not found", "model": ErrorResponse},
        409: {"description": "Already undone", "model": ErrorResponse},
    },
    summary="Reverse a previously-recorded action",
)
async def undo_audit_event(
    audit_id: str,
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
) -> AuditEvent:
    record = repository.get_audit_event(engine, session.household_id, audit_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    if record.reverted_at is not None:
        raise HTTPException(status_code=409, detail="This action has already been undone")
    if record.undo_token is None:
        raise HTTPException(status_code=400, detail="This action can't be undone")

    try:
        token = json.loads(record.undo_token)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="This action can't be undone")

    _reverse(engine, session.household_id, token)
    repository.mark_audit_reverted(engine, session.household_id, audit_id)
    updated = repository.get_audit_event(engine, session.household_id, audit_id)
    assert updated is not None
    return _to_schema(updated)


def _reverse(engine: Engine, household_id: str, token: dict) -> None:
    """Apply the inverse of the recorded action via the undo framework (M108).
    Anything the framework can't reverse raises, surfaced as 400/404."""
    try:
        undo_actions.reverse(engine, household_id, token)
    except undo_actions.UndoError as exc:
        detail = str(exc)
        status = 404 if "exist" in detail else 400
        raise HTTPException(status_code=status, detail=detail[:1].upper() + detail[1:])
