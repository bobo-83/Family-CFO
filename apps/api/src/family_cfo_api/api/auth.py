from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import repository, security
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_bearer_token, get_current_session, get_engine
from family_cfo_api.schemas import AuthSession, AuthSessionCreateRequest, ErrorResponse

router = APIRouter(tags=["Authentication"])


def _issue_session(
    engine: Engine, user_id: str, household_id: str, role: str, ttl_hours: int
) -> AuthSession:
    token = security.generate_access_token()
    expires_at = repository.utcnow() + timedelta(hours=ttl_hours)
    repository.create_auth_session(
        engine, user_id, household_id, security.hash_token(token), expires_at
    )
    return AuthSession(
        access_token=token,
        expires_at=expires_at,
        household_id=household_id,
        user_id=user_id,
        role=role,
    )


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
    settings: Settings = Depends(get_app_settings),
) -> AuthSession:
    user = repository.get_user_by_email(engine, payload.email)
    if user is None or not security.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    household_id = repository.get_primary_household_id(engine, user.id)
    role = household_id and repository.get_membership_role(engine, household_id, user.id)
    if household_id is None or role is None:
        raise HTTPException(status_code=401, detail="User has no household membership")

    return _issue_session(engine, user.id, household_id, role, settings.session_ttl_hours)


@router.post(
    "/auth/sessions/refresh",
    operation_id="refreshAuthSession",
    response_model=AuthSession,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Rotate the current session token",
)
async def refresh_auth_session(
    session: repository.SessionContext = Depends(get_current_session),
    token: str = Depends(get_bearer_token),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> AuthSession:
    # Revoke the presented token, then issue a fresh one. The old token no
    # longer authenticates after this returns.
    repository.revoke_auth_session(engine, security.hash_token(token))
    return _issue_session(
        engine, session.user_id, session.household_id, session.role, settings.session_ttl_hours
    )


@router.delete(
    "/auth/sessions",
    operation_id="deleteAuthSession",
    status_code=204,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Log out (revoke the current session)",
)
async def delete_auth_session(
    _session: repository.SessionContext = Depends(get_current_session),
    token: str = Depends(get_bearer_token),
    engine: Engine = Depends(get_engine),
) -> Response:
    repository.revoke_auth_session(engine, security.hash_token(token))
    return Response(status_code=204)
