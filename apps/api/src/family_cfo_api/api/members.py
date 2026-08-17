import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, household_crypto, repository, rights, security, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api.schemas import (
    ErrorResponse,
    Member,
    MemberCreateRequest,
    MemberListResponse,
    MemberRoleUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Household"])

# #103: what an OWNER is told when the household is sealed and locked. Unlike
# the invitee (api/invites.py), they can fix this themselves in ten seconds —
# so the sentence leads with the action and explains second.
OWNER_LOCKED_MESSAGE = (
    "Sign in to this household first, then add the member. "
    "Its key is locked and a new member needs it to be readable."
)


def _to_schema(record: repository.MemberRecord) -> Member:
    return Member(
        user_id=record.user_id,
        email=record.email,
        display_name=record.display_name,
        role=record.role,
        role_id=record.role_id,
        role_name=record.role_name or None,
        created_at=record.created_at,
    )


def _resolve_role(
    engine: Engine, household_id: str, role_id: str | None, legacy_role: str | None
) -> repository.RoleRecord:
    """The RoleRecord a member request names — by id, by legacy tier's preset,
    or the User preset when neither is given (ADR 0034)."""
    if role_id is not None:
        record = repository.get_role(engine, household_id, role_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Role not found")
        return record
    preset = rights.LEGACY_ROLE_TO_PRESET.get(legacy_role or "", rights.PRESET_USER)
    record = repository.get_role_by_name(engine, household_id, preset)
    if record is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return record


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
    session: repository.SessionContext = Depends(require_right(rights.MEMBERS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Member:
    # #103: checked first, and before anything is written. A new member's key
    # wrap can only be minted while the household key is readable; raising it
    # here rather than letting `on_password_established` raise it afterwards is
    # what stops a half-made member — a users row with no wrap, an account that
    # could never open anything.
    if not household_crypto.dek_available(engine, session.household_id):
        raise household_crypto.HouseholdLockedError(
            session.household_id,
            message=OWNER_LOCKED_MESSAGE,
            code=household_crypto.LOCKED_NEW_MEMBER_CODE,
        )
    if repository.user_email_exists(engine, payload.email):
        raise HTTPException(status_code=409, detail="Email already in use")
    assigned = _resolve_role(engine, session.household_id, payload.role_id, payload.role)
    record = repository.create_member(
        engine,
        household_id=session.household_id,
        email=payload.email,
        password_hash=security.hash_password(payload.password),
        display_name=payload.display_name,
        role=repository.legacy_role_for(assigned),
        role_id=assigned.id,
    )
    household_crypto.on_password_established(
        engine, session.household_id, record.user_id, payload.password
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "member.created",
        "user",
        record.user_id,
        f"Added member {record.email} as {assigned.name}",
        undo_token=undo_actions.created("member", record.user_id),
    )
    import dataclasses

    return _to_schema(dataclasses.replace(record, role_name=assigned.name))


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
    session: repository.SessionContext = Depends(require_right(rights.MEMBERS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Member:
    member = repository.get_member(engine, session.household_id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    assigned = _resolve_role(engine, session.household_id, payload.role_id, payload.role)
    new_legacy = repository.legacy_role_for(assigned)
    if (
        member.role == "owner"
        and new_legacy != "owner"
        and repository.count_household_owners(engine, session.household_id) <= 1
    ):
        raise HTTPException(status_code=409, detail="Household must keep at least one owner")
    repository.assign_member_role(engine, session.household_id, user_id, assigned)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "member.role_changed",
        "user",
        user_id,
        f"Changed member role to {assigned.name}",
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
        409: {
            "description": (
                "Household must keep at least one owner, or is sealed with no "
                "device to carry the rotated key"
            ),
            "model": ErrorResponse,
        },
    },
    summary="Remove a household member",
)
async def delete_member(
    user_id: str,
    session: repository.SessionContext = Depends(require_right(rights.MEMBERS_MANAGE)),
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
    # Checked BEFORE the member row goes: removal without the rotation that
    # follows it would leave someone who knows the old key with data still
    # encrypted under it, and a rotation that cannot place the new key would
    # strand the household's data instead. Neither half is safe alone, so if
    # the second cannot run, the first must not either.
    if household_crypto.rotation_would_strand_key(engine, session.household_id):
        raise HTTPException(status_code=409, detail=household_crypto.ROTATION_STRANDED_MESSAGE)
    _, archived_email = repository.delete_member(engine, session.household_id, user_id)
    # ADR 0072 Phase 2: a removed member may know the old data key (their wrap
    # covered it) — rotate, re-encrypting every sealed row. Remaining members'
    # wraps return at their next login; the recovery key must be re-minted.
    rotated = household_crypto.rotate_household_key(
        engine, session.household_id, session.user_id
    )
    if household_crypto.enabled() and not rotated:
        # The precondition above passed, so this is the narrow race where the
        # last device was revoked in between. The member is already gone and
        # cannot be un-removed, so the honest move is to say so loudly rather
        # than let "removed but still holding a live key" pass unrecorded.
        logger.error(
            "member removed but the key rotation did not run household=%s — "
            "the removed member's key wrap covered data still encrypted under it; "
            "rotate manually once a device is paired",
            session.household_id,
        )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "member.removed",
        "user",
        user_id,
        f"Removed member {member.email}",
        undo_token=undo_actions.member_removed(user_id, member.role, archived_email),
    )
    return Response(status_code=204)
