from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, security
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_engine, require_right
from family_cfo_api.schemas import (
    AuthSession,
    ErrorResponse,
    HostedHousehold,
    HostedHouseholdCreateRequest,
    HostedHouseholdCreateResponse,
    HostedHouseholdList,
    HouseholdCreateRequest,
)

router = APIRouter(tags=["Household"])

SESSION_TTL = timedelta(hours=12)


@router.post(
    "/households",
    operation_id="createHousehold",
    response_model=AuthSession,
    status_code=201,
    responses={
        409: {"description": "Email already in use", "model": ErrorResponse},
    },
    summary="Bootstrap a household with its first owner (self-hosted first-run setup)",
)
async def create_household(
    payload: HouseholdCreateRequest,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AuthSession:
    # M32: single-tenant lockout — this server already belongs to a family.
    if not settings.allow_multiple_households and repository.any_household_exists(engine):
        raise HTTPException(
            status_code=403,
            detail=(
                "This server already has a household. Ask its owner to add you "
                "from the Users page (or set FAMILY_CFO_ALLOW_MULTIPLE_HOUSEHOLDS=true)."
            ),
        )
    if repository.user_email_exists(engine, payload.owner_email):
        raise HTTPException(status_code=409, detail="Email already in use")

    result = repository.create_household_with_owner(
        engine,
        display_name=payload.display_name,
        base_currency=payload.base_currency.upper(),
        owner_email=payload.owner_email,
        owner_password_hash=security.hash_password(payload.owner_password),
        owner_display_name=payload.owner_display_name,
    )

    audit.write_audit(
        engine,
        result.household_id,
        result.user_id,
        "household.created",
        "household",
        result.household_id,
        "Bootstrapped household and owner",
    )

    token = security.generate_access_token()
    expires_at = repository.utcnow() + SESSION_TTL
    repository.create_auth_session(
        engine, result.user_id, result.household_id, security.hash_token(token), expires_at
    )

    member_rights, role_name = repository.resolve_member_rights(
        engine, result.household_id, result.user_id
    )
    return AuthSession(
        access_token=token,
        expires_at=expires_at,
        household_id=result.household_id,
        user_id=result.user_id,
        role=result.role,
        role_name=role_name or None,
        rights=sorted(member_rights),
    )


# #180: the invite a hosted family's first owner joins through lives 7 days,
# matching member invites.
HOSTED_INVITE_TTL = timedelta(days=7)


def _hosted_summary(engine, summary: dict, counts: dict, pending: set) -> HostedHousehold:
    return HostedHousehold(
        id=summary["id"],
        name=summary["display_name"],
        base_currency=summary["base_currency"],
        created_at=summary["created_at"],
        member_count=counts.get(summary["id"], 0),
        pending_owner_invite=summary["id"] in pending,
        sealed=bool(summary["sealed_mode"]),
    )


@router.get(
    "/households/hosted",
    operation_id="listHostedHouseholds",
    response_model=HostedHouseholdList,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Requires system administrator", "model": ErrorResponse},
    },
    summary="Every household on this box (operator view)",
)
async def list_hosted_households(
    session: repository.SessionContext = Depends(require_right(rights.SYSTEM_ADMIN)),
    engine: Engine = Depends(get_engine),
) -> HostedHouseholdList:
    counts = repository.household_member_counts(engine)
    pending = repository.households_with_pending_owner_invite(engine)
    return HostedHouseholdList(
        households=[
            _hosted_summary(engine, summary, counts, pending)
            for summary in repository.list_household_summaries(engine)
        ]
    )


@router.post(
    "/households/hosted",
    operation_id="createHostedHousehold",
    response_model=HostedHouseholdCreateResponse,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Requires system administrator", "model": ErrorResponse},
        409: {"description": "Email already has an account", "model": ErrorResponse},
    },
    summary="Create a household for a family you host, with its first-owner invite",
)
async def create_hosted_household(
    payload: HostedHouseholdCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.SYSTEM_ADMIN)),
    engine: Engine = Depends(get_engine),
) -> HostedHouseholdCreateResponse:
    # Deliberate: this does NOT check allow_multiple_households — that flag
    # locks PUBLIC signup. Hosting is an explicit operator action, and the
    # public door stays shut either way (#180).
    if repository.user_email_exists(engine, payload.owner_email):
        raise HTTPException(
            status_code=409,
            detail="That email already has an account on this box — invite them "
            "from their existing household instead.",
        )
    household_id, admin_role_id = repository.create_hosted_household(
        engine, payload.display_name, payload.base_currency.upper()
    )
    invite_token = security.generate_access_token()
    expires_at = repository.utcnow() + HOSTED_INVITE_TTL
    repository.create_invite(
        engine,
        household_id=household_id,
        email=payload.owner_email,
        role="owner",
        role_id=admin_role_id,
        token_hash=security.hash_token(invite_token),
        invited_by_user_id=session.user_id,
        expires_at=expires_at,
    )
    audit.write_audit(
        engine,
        household_id,
        session.user_id,
        "household.hosted_created",
        "household",
        household_id,
        f"Hosted household created; owner invite sent to {payload.owner_email}",
    )
    counts = repository.household_member_counts(engine)
    pending = repository.households_with_pending_owner_invite(engine)
    summary = next(
        s for s in repository.list_household_summaries(engine) if s["id"] == household_id
    )
    return HostedHouseholdCreateResponse(
        household=_hosted_summary(engine, summary, counts, pending),
        invite_token=invite_token,
        invite_expires_at=expires_at,
    )


@router.delete(
    "/households/hosted/{household_id}",
    operation_id="deleteHostedHousehold",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Requires system administrator", "model": ErrorResponse},
        404: {"description": "Household not found", "model": ErrorResponse},
        409: {"description": "Cannot delete your own household", "model": ErrorResponse},
    },
    summary="Delete a hosted household and everything it owns (irreversible)",
)
async def delete_hosted_household(
    household_id: str,
    session: repository.SessionContext = Depends(require_right(rights.SYSTEM_ADMIN)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
):
    import os as _os

    from fastapi import Response

    if household_id == session.household_id:
        raise HTTPException(
            status_code=409,
            detail="You can't delete the household you belong to.",
        )
    target = repository.get_household(engine, household_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Household not found")

    # Files first-collected, deleted after the rows are gone.
    file_paths = repository.household_file_paths(engine, household_id)
    counts = repository.delete_household_cascade(engine, household_id)
    removed_files = 0
    for rel in file_paths:  # already staging-relative (documents/… or attachments/…)
        try:
            _os.remove(_os.path.join(settings.import_staging_dir, rel))
            removed_files += 1
        except OSError:
            continue
    # The deleted household's audit trail died with it; the record of the
    # DELETION belongs to the operator's own household.
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "household.hosted_deleted",
        "household",
        household_id,
        f"Deleted hosted household \u201c{target.display_name}\u201d "
        f"({sum(counts.values())} rows, {removed_files} files)",
    )
    return Response(status_code=204)
