from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, security, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    ErrorResponse,
    Member,
    MemberCreateRequest,
    MemberListResponse,
    MemberRoleUpdateRequest,
)

router = APIRouter(tags=["Household"])


def _to_schema(record: repository.MemberRecord) -> Member:
    return Member(
        user_id=record.user_id,
        email=record.email,
        display_name=record.display_name,
        role=record.role,
        created_at=record.created_at,
    )


@router.get(
    "/household/members",
    operation_id="listMembers",
    response_model=MemberListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List household members",
)
async def list_members(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> MemberListResponse:
    records = repository.list_members(engine, session.household_id)
    return MemberListResponse(members=[_to_schema(record) for record in records])


@router.post(
    "/household/members",
    operation_id="createMember",
    response_model=Member,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "Email already in use", "model": ErrorResponse},
    },
    summary="Add a household member",
)
async def create_member(
    payload: MemberCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
) -> Member:
    if repository.user_email_exists(engine, payload.email):
        raise HTTPException(status_code=409, detail="Email already in use")
    record = repository.create_member(
        engine,
        household_id=session.household_id,
        email=payload.email,
        password_hash=security.hash_password(payload.password),
        display_name=payload.display_name,
        role=payload.role,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "member.created",
        "user",
        record.user_id,
        f"Added member {record.email} as {record.role}",
        undo_token=undo_actions.created("member", record.user_id),
    )
    return _to_schema(record)


@router.patch(
    "/household/members/{user_id}",
    operation_id="updateMemberRole",
    response_model=Member,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Member not found", "model": ErrorResponse},
        409: {"description": "Household must keep at least one owner", "model": ErrorResponse},
    },
    summary="Change a member's role",
)
async def update_member_role(
    user_id: str,
    payload: MemberRoleUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
) -> Member:
    member = repository.get_member(engine, session.household_id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if (
        member.role == "owner"
        and payload.role != "owner"
        and repository.count_household_owners(engine, session.household_id) <= 1
    ):
        raise HTTPException(status_code=409, detail="Household must keep at least one owner")
    repository.update_member_role(engine, session.household_id, user_id, payload.role)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "member.role_changed",
        "user",
        user_id,
        f"Changed member role to {payload.role}",
        undo_token=undo_actions.member_role_changed(user_id, member.role),
    )
    updated = repository.get_member(engine, session.household_id, user_id)
    assert updated is not None
    return _to_schema(updated)


@router.delete(
    "/household/members/{user_id}",
    operation_id="deleteMember",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Member not found", "model": ErrorResponse},
        409: {"description": "Household must keep at least one owner", "model": ErrorResponse},
    },
    summary="Remove a household member",
)
async def delete_member(
    user_id: str,
    session: repository.SessionContext = Depends(require_role("owner")),
    engine: Engine = Depends(get_engine),
) -> Response:
    member = repository.get_member(engine, session.household_id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if (
        member.role == "owner"
        and repository.count_household_owners(engine, session.household_id) <= 1
    ):
        raise HTTPException(status_code=409, detail="Household must keep at least one owner")
    repository.delete_member(engine, session.household_id, user_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "member.removed",
        "user",
        user_id,
        f"Removed member {member.email}",
        undo_token=undo_actions.member_removed(user_id, member.role),
    )
    return Response(status_code=204)
