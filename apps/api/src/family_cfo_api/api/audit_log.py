from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_engine, require_role
from family_cfo_api.schemas import AuditEvent, AuditEventListResponse, ErrorResponse

router = APIRouter(tags=["Audit"])


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
    return AuditEventListResponse(
        events=[
            AuditEvent(
                id=record.id,
                actor_user_id=record.actor_user_id,
                action=record.action,
                entity_type=record.entity_type,
                entity_id=record.entity_id,
                summary=record.summary,
                created_at=record.created_at,
            )
            for record in records
        ]
    )
