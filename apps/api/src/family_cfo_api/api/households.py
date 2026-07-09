from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, security
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_engine
from family_cfo_api.schemas import AuthSession, ErrorResponse, HouseholdCreateRequest

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

    return AuthSession(
        access_token=token,
        expires_at=expires_at,
        household_id=result.household_id,
        user_id=result.user_id,
        role=result.role,
    )
