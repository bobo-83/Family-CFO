"""Household memory management (M57, ADR 0016).

Transparency and control over what the advisor remembers: list every stored
fact, teach one directly, or forget one. Deleting a conversation keeps its
extracted facts by design; deleting a memory here is the forget operation.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api.schemas import (
    ErrorResponse,
    Memory,
    MemoryCreateRequest,
    MemoryListResponse,
)

router = APIRouter(tags=["Memories"])


def _to_schema(record: repository.HouseholdMemoryRecord) -> Memory:
    return Memory(
        id=record.id,
        key=record.key,
        value=record.value,
        source=record.source,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get(
    "/memories",
    operation_id="listMemories",
    response_model=MemoryListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List what the advisor remembers about the household",
)
async def list_memories(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> MemoryListResponse:
    records = repository.list_household_memories(engine, session.household_id)
    return MemoryListResponse(memories=[_to_schema(record) for record in records])


@router.post(
    "/memories",
    operation_id="createMemory",
    response_model=Memory,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Teach the advisor a household fact",
)
async def create_memory(
    payload: MemoryCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.ADVISOR_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Memory:
    value = payload.value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="Memory value must not be empty")
    # Manual facts get a random key: they are free text, not the extractor's
    # stable identifiers, so they never collide with (or overwrite) each other.
    record = repository.upsert_household_memory(
        engine,
        session.household_id,
        f"note_{secrets.token_hex(4)}",
        value,
        source="manual",
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "memory.created",
        "memory",
        record.id,
        "Added an advisor memory",
        undo_token=undo_actions.created("memory", record.id),
    )
    return _to_schema(record)


@router.delete(
    "/memories/{memory_id}",
    operation_id="deleteMemory",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Memory not found", "model": ErrorResponse},
    },
    summary="Make the advisor forget a remembered fact",
)
async def delete_memory(
    memory_id: str,
    session: repository.SessionContext = Depends(require_right(rights.ADVISOR_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    # Capture the fact before deleting so undo can recreate it (M110). Fetch it
    # from the user-visible list, which is exactly the set this endpoint may
    # delete — a missing row is a 404 either way.
    before = next(
        (
            m
            for m in repository.list_household_memories(engine, session.household_id)
            if m.id == memory_id
        ),
        None,
    )
    if before is None or not repository.delete_household_memory(
        engine, session.household_id, memory_id
    ):
        raise HTTPException(status_code=404, detail="Memory not found")
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "memory.deleted",
        "memory",
        memory_id,
        "Deleted an advisor memory",
        undo_token=undo_actions.memory_deleted(before),
    )
    return Response(status_code=204)
