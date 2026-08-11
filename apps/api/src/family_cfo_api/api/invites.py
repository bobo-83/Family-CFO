"""Copy-link household invites (ADR 0056).

An admin invites a new member by email and shares a one-time LINK themselves
(iMessage, their own mail app, …) — the box sends no email and holds no mailbox
credentials. The invitee opens the link, sets their OWN password, and joins.
The link's CSPRNG token is stored SHA-256-hashed; preview/accept are public
endpoints where the token itself is the bearer proof (same trust shape as the
QR pairing confirm, ADR 0010).
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.engine import Engine

from family_cfo_api import (
    audit,
    household_clock,
    household_crypto,
    repository,
    rights,
    security,
    undo_actions,
)
from family_cfo_api.api.auth import _issue_session
from family_cfo_api.api.members import _resolve_role
from family_cfo_api.config import Settings
from family_cfo_api.deps import (
    client_ip,
    get_app_settings,
    get_engine,
    get_rate_limiter,
    require_right,
)
from family_cfo_api.ratelimit import AuthRateLimiter
from family_cfo_api.schemas import (
    AuthSession,
    ErrorResponse,
    Invite,
    InviteAcceptRequest,
    InviteCreateRequest,
    InviteCreateResponse,
    InviteListResponse,
    InvitePreview,
    InvitePreviewRequest,
)

router = APIRouter(tags=["Household"])

# #103: what an INVITEE is told when the household they were invited to is
# sealed and locked. They are not a member yet, so "sign in to unlock it" names
# a door they cannot reach — the only person who can open it is the one who sent
# the link. Says the next action, not the machine's state.
INVITEE_LOCKED_MESSAGE = (
    "This household is locked. Ask whoever invited you to sign in, "
    "then open this link again."
)

INVITE_TTL = timedelta(days=7)


def _to_schema(engine: Engine, record: repository.InviteRecord) -> Invite:
    role_name = None
    if record.role_id:
        role = repository.get_role(engine, record.household_id, record.role_id)
        role_name = role.name if role else None
    return Invite(
        id=record.id,
        email=record.email,
        role=record.role,
        role_id=record.role_id,
        role_name=role_name,
        status=record.status,
        created_at=record.created_at,
        expires_at=record.expires_at,
        accepted_at=record.accepted_at,
        invited_by_display_name=record.invited_by_display_name,
    )


@router.post(
    "/household/invites",
    operation_id="createInvite",
    response_model=InviteCreateResponse,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "Email already has an account", "model": ErrorResponse},
    },
    summary="Invite a new member — returns the one-time join link token",
)
async def create_invite(
    payload: InviteCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.MEMBERS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> InviteCreateResponse:
    existing = repository.get_user_by_email(engine, payload.email)
    if existing is not None and repository.user_membership_count(engine, existing.id) > 0:
        raise HTTPException(
            status_code=409, detail="That email already has an account — they can sign in."
        )
    role = _resolve_role(engine, session.household_id, payload.role_id, payload.role)
    legacy = repository.legacy_role_for(role)

    token = security.generate_access_token()
    record = repository.create_invite(
        engine,
        household_id=session.household_id,
        email=payload.email.strip(),
        role=legacy,
        role_id=role.id,
        token_hash=security.hash_token(token),
        invited_by_user_id=session.user_id,
        expires_at=repository.utcnow() + INVITE_TTL,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "invite.created",
        "invite",
        record.id,
        f"Invited {payload.email}",
        undo_token=undo_actions.created("invite", record.id),
    )
    return InviteCreateResponse(invite=_to_schema(engine, record), invite_token=token)


@router.get(
    "/household/invites",
    operation_id="listInvites",
    response_model=InviteListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="List invitations with their status (pending/accepted/expired/revoked)",
)
async def list_invites(
    session: repository.SessionContext = Depends(require_right(rights.MEMBERS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> InviteListResponse:
    records = repository.list_invites(engine, session.household_id)
    return InviteListResponse(invites=[_to_schema(engine, r) for r in records])


@router.post(
    "/household/invites/{invite_id}/token",
    operation_id="regenerateInviteToken",
    response_model=InviteCreateResponse,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Invite not found", "model": ErrorResponse},
        409: {"description": "Invite already accepted", "model": ErrorResponse},
    },
    summary="Mint a fresh link for an unaccepted invite (the old link stops working)",
)
async def regenerate_invite_token(
    invite_id: str,
    session: repository.SessionContext = Depends(require_right(rights.MEMBERS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> InviteCreateResponse:
    record = repository.get_invite(engine, session.household_id, invite_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if record.accepted_at is not None:
        raise HTTPException(status_code=409, detail="Invite was already accepted")

    token = security.generate_access_token()
    repository.regenerate_invite_token(
        engine,
        session.household_id,
        invite_id,
        token_hash=security.hash_token(token),
        expires_at=repository.utcnow() + INVITE_TTL,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "invite.token_regenerated",
        "invite",
        invite_id,
        f"New invite link for {record.email}",
    )
    refreshed = repository.get_invite(engine, session.household_id, invite_id)
    assert refreshed is not None
    return InviteCreateResponse(invite=_to_schema(engine, refreshed), invite_token=token)


@router.delete(
    "/household/invites/{invite_id}",
    operation_id="revokeInvite",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Invite not found", "model": ErrorResponse},
        409: {"description": "Invite already accepted", "model": ErrorResponse},
    },
    summary="Revoke a pending invite (an accepted invite is history — remove the member instead)",
)
async def revoke_invite(
    invite_id: str,
    session: repository.SessionContext = Depends(require_right(rights.MEMBERS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    record = repository.get_invite(engine, session.household_id, invite_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if record.accepted_at is not None:
        raise HTTPException(status_code=409, detail="Invite was already accepted")
    repository.revoke_invite(engine, session.household_id, invite_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "invite.revoked",
        "invite",
        invite_id,
        f"Revoked invite for {record.email}",
        undo_token=undo_actions.invite_revoked(invite_id),
    )
    return Response(status_code=204)


def _limit_or_429(rate_limiter: AuthRateLimiter, request: Request) -> list[str]:
    # Namespaced key: join-page fumbles must not lock the family out of
    # password login from the same NAT IP (ADR 0010 defense-in-depth).
    keys = [f"invite-ip:{client_ip(request)}"]
    retry_after = rate_limiter.retry_after(keys)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    return keys


@router.post(
    "/invites/preview",
    operation_id="previewInvite",
    response_model=InvitePreview,
    responses={
        404: {"description": "Unknown invite token", "model": ErrorResponse},
        410: {"description": "Invite no longer usable", "model": ErrorResponse},
        429: {"description": "Too many attempts", "model": ErrorResponse},
    },
    summary="Public: what an invite link joins (household, email, role)",
)
async def preview_invite(
    payload: InvitePreviewRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    rate_limiter: AuthRateLimiter = Depends(get_rate_limiter),
) -> InvitePreview:
    keys = _limit_or_429(rate_limiter, request)
    record = repository.get_invite_by_token_hash(engine, security.hash_token(payload.token))
    if record is None:
        rate_limiter.record_failure(keys)
        raise HTTPException(status_code=404, detail="Unknown invite link")
    if record.status != "pending":
        rate_limiter.record_failure(keys)
        raise HTTPException(
            status_code=410, detail=f"This invite link is {record.status} — ask for a new one."
        )
    rate_limiter.reset(keys)
    household = repository.get_household(engine, record.household_id)
    role_name = None
    if record.role_id:
        role = repository.get_role(engine, record.household_id, record.role_id)
        role_name = role.name if role else None
    return InvitePreview(
        household_name=household.display_name if household else "your household",
        email=record.email,
        role_name=role_name,
        expires_at=record.expires_at,
    )


@router.post(
    "/invites/accept",
    operation_id="acceptInvite",
    response_model=AuthSession,
    status_code=201,
    responses={
        404: {"description": "Unknown invite token", "model": ErrorResponse},
        409: {"description": "Email already has an account", "model": ErrorResponse},
        410: {"description": "Invite no longer usable", "model": ErrorResponse},
        422: {"description": "Unknown timezone", "model": ErrorResponse},
        429: {"description": "Too many attempts", "model": ErrorResponse},
    },
    summary="Public: accept an invite — set your own password and join the household",
)
async def accept_invite(
    payload: InviteAcceptRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
    rate_limiter: AuthRateLimiter = Depends(get_rate_limiter),
) -> AuthSession:
    keys = _limit_or_429(rate_limiter, request)

    # #93: validated BEFORE the invite is claimed. Accepting is one-shot — a
    # typo'd zone must cost the invitee a retry, not their only link.
    # A blank field is "didn't answer", not a bad zone: clients that submit an
    # untouched input must land on today's behaviour, not a 422.
    timezone = (payload.timezone or "").strip() or None
    if timezone is not None and not household_clock.is_known_zone(timezone):
        raise HTTPException(status_code=422, detail=household_clock.UNKNOWN_TIMEZONE_DETAIL)

    token_hash = security.hash_token(payload.token)

    # #103: refuse BEFORE claiming the invite, not after.
    #
    # Minting the new member's key wrap needs the household key readable, and a
    # sealed household's key is only readable while someone's session keeps it
    # alive (30-minute TTL). Discovering that after `accept_invite` has already
    # burned the one-time token would make the message below a lie: the invitee
    # would come back to a 410. So the lock is checked here, where nothing has
    # been mutated yet and the link survives to be opened again.
    #
    # Only a token that is real and still pending gets this answer — anything
    # else falls through to the 404/410 it has always received, so a locked
    # household cannot become an oracle for guessed tokens.
    pending = repository.get_invite_by_token_hash(engine, token_hash)
    if (
        pending is not None
        and pending.status == "pending"
        and not household_crypto.dek_available(engine, pending.household_id)
    ):
        raise household_crypto.HouseholdLockedError(
            pending.household_id,
            message=INVITEE_LOCKED_MESSAGE,
            code=household_crypto.LOCKED_NEW_MEMBER_CODE,
        )

    result = repository.accept_invite(
        engine,
        token_hash=token_hash,
        password_hash=security.hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    if result.outcome == "not_found":
        rate_limiter.record_failure(keys)
        raise HTTPException(status_code=404, detail="Unknown invite link")
    if result.outcome == "gone":
        rate_limiter.record_failure(keys)
        raise HTTPException(
            status_code=410, detail="This invite link is no longer usable — ask for a new one."
        )
    if result.outcome == "conflict":
        rate_limiter.record_failure(keys)
        raise HTTPException(
            status_code=409, detail="That email already has an account — sign in instead."
        )
    rate_limiter.reset(keys)
    assert result.user_id and result.household_id and result.role
    # ADR 0072 Phase 2: the invitee just SET their password — mint their wrap.
    household_crypto.on_password_established(
        engine, result.household_id, result.user_id, payload.password
    )
    # #93: adopt the invitee's zone ONLY if the household has never had one.
    # A second member joining from elsewhere must not silently re-date every
    # bill for the people already there, so this is a conditional write, not a
    # set — see repository.set_household_timezone_if_unset.
    adopted = timezone is not None and repository.set_household_timezone_if_unset(
        engine, result.household_id, timezone
    )
    joined = f"{payload.display_name.strip()} joined via invite"
    audit.write_audit(
        engine,
        result.household_id,
        result.user_id,
        "invite.accepted",
        "member",
        result.user_id,
        # Never a silent mutation: the audit log names the zone when one was
        # adopted, so an owner can see why "today" moved.
        f"{joined} and set the household time zone to {timezone}" if adopted else joined,
    )
    return _issue_session(
        engine, result.user_id, result.household_id, result.role, settings.session_ttl_hours
    )
