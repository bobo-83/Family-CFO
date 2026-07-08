from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import repository, security
from family_cfo_api.deps import get_engine
from family_cfo_api.schemas import AuthSession, AuthSessionCreateRequest, ErrorResponse

router = APIRouter(tags=["Authentication"])

SESSION_TTL = timedelta(hours=12)


@router.post(
    "/auth/sessions",
    operation_id="createAuthSession",
    response_model=AuthSession,
    status_code=201,
    responses={401: {"description": "Invalid credentials", "model": ErrorResponse}},
    summary="Create a local authentication session",
)
async def create_auth_session(
    payload: AuthSessionCreateRequest,
    engine: Engine = Depends(get_engine),
) -> AuthSession:
    user = repository.get_user_by_email(engine, payload.email)
    if user is None or not security.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    household_id = repository.get_primary_household_id(engine, user.id)
    role = household_id and repository.get_membership_role(engine, household_id, user.id)
    if household_id is None or role is None:
        raise HTTPException(status_code=401, detail="User has no household membership")

    token = security.generate_access_token()
    expires_at = repository.utcnow() + SESSION_TTL
    repository.create_auth_session(
        engine, user.id, household_id, security.hash_token(token), expires_at
    )

    return AuthSession(
        access_token=token,
        expires_at=expires_at,
        household_id=household_id,
        user_id=user.id,
        role=role,
    )
