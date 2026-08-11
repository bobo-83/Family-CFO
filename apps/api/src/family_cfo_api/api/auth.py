from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, household_crypto, repository, security
from family_cfo_api.config import Settings
from family_cfo_api.deps import (
    client_ip,
    get_app_settings,
    get_bearer_token,
    get_current_session,
    get_engine,
    get_rate_limiter,
)
from family_cfo_api.ratelimit import AuthRateLimiter
from family_cfo_api.schemas import (
    AuthSession,
    AuthSessionCreateRequest,
    ErrorResponse,
    PasswordChangeRequest,
    SessionInfo,
)

router = APIRouter(tags=["Authentication"])


def _issue_session(
    engine: Engine, user_id: str, household_id: str, role: str, ttl_hours: int
) -> AuthSession:
    token = security.generate_access_token()
    expires_at = repository.utcnow() + timedelta(hours=ttl_hours)
    repository.create_auth_session(
        engine, user_id, household_id, security.hash_token(token), expires_at
    )
    member_rights, role_name = repository.resolve_member_rights(engine, household_id, user_id)
    return AuthSession(
        access_token=token,
        expires_at=expires_at,
        household_id=household_id,
        user_id=user_id,
        role=role,
        role_name=role_name or None,
        rights=sorted(member_rights),
    )


@router.post(
    "/auth/sessions",
    operation_id="createAuthSession",
    response_model=AuthSession,
    status_code=201,
    responses={
        401: {"description": "Invalid credentials", "model": ErrorResponse},
        429: {"description": "Too many attempts", "model": ErrorResponse},
    },
    summary="Create a local authentication session",
)
async def create_auth_session(
    payload: AuthSessionCreateRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
    rate_limiter: AuthRateLimiter = Depends(get_rate_limiter),
) -> AuthSession:
    # Brute-force guard: throttle by client IP and by target account (ADR 0010).
    limit_keys = [f"ip:{client_ip(request)}", f"email:{payload.email.lower()}"]
    retry_after = rate_limiter.retry_after(limit_keys)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = repository.get_user_by_email(engine, payload.email)
    if user is None or not security.verify_password(payload.password, user.password_hash):
        rate_limiter.record_failure(limit_keys)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    household_id = repository.get_primary_household_id(engine, user.id)
    role = household_id and repository.get_membership_role(engine, household_id, user.id)
    if household_id is None or role is None:
        rate_limiter.record_failure(limit_keys)
        raise HTTPException(status_code=401, detail="User has no household membership")

    rate_limiter.reset(limit_keys)
    # ADR 0072 Phase 2: a proven password is the only moment a member wrap can
    # be minted/refreshed.
    household_crypto.on_password_established(engine, household_id, user.id, payload.password)
    audit.write_audit(
        engine, household_id, user.id, "auth.login", "user", user.id, "Signed in"
    )
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


@router.get(
    "/auth/session",
    operation_id="getSessionInfo",
    response_model=SessionInfo,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="The current session's identity and freshly-resolved rights",
)
async def get_session_info(
    session: repository.SessionContext = Depends(get_current_session),
) -> SessionInfo:
    # ADR 0065: rights move server-side (role edits, system-admin grants)
    # while clients cache their pairing/login snapshot. Read-only — the
    # token is untouched, so device-bound sessions can refresh freely.
    return SessionInfo(
        household_id=session.household_id,
        user_id=session.user_id,
        role=session.role,
        role_name=session.role_name or None,
        rights=sorted(session.rights),
        is_system_admin=session.is_system_admin,
        device_id=session.device_id,
    )


@router.post(
    "/auth/password",
    operation_id="changePassword",
    status_code=204,
    responses={
        400: {"description": "The new password is the current one", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Current password is wrong", "model": ErrorResponse},
        409: {"description": "The member key could not be re-minted", "model": ErrorResponse},
        429: {"description": "Too many attempts", "model": ErrorResponse},
    },
    summary="Change your own password (requires the current one)",
)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    session: repository.SessionContext = Depends(get_current_session),
    token: str = Depends(get_bearer_token),
    engine: Engine = Depends(get_engine),
    rate_limiter: AuthRateLimiter = Depends(get_rate_limiter),
) -> Response:
    """#97: a member retires their own password.

    A valid session is deliberately NOT sufficient — an unattended open laptop
    is a valid session — so the current password is proven again here.
    """
    # Namespaced keys (the invite flow's precedent): a hijacked session hammering
    # this form must not lock the real member out of /auth/sessions, but the
    # current-password check is still a password oracle and is throttled per
    # account and per IP like a login (ADR 0010).
    limit_keys = [
        f"password-change-ip:{client_ip(request)}",
        f"password-change-user:{session.user_id}",
    ]
    retry_after = rate_limiter.retry_after(limit_keys)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = repository.get_user_by_id(engine, session.user_id)
    if user is None or not security.verify_password(
        payload.current_password, user.password_hash
    ):
        rate_limiter.record_failure(limit_keys)
        # 403, not 401: the SESSION is fine, the re-authentication failed. A 401
        # here would trip the clients' dead-token handling and sign the member
        # out mid-form over a typo.
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=400, detail="The new password must differ from the current one"
        )
    rate_limiter.reset(limit_keys)

    # ADR 0072: the password derives this member's key wrap, so the wrap is
    # re-minted BEFORE the hash moves. If it can't be, nothing is written — a
    # member whose hash changed but whose wrap did not can sign in and can no
    # longer read their own household, which is worse than a failed change.
    #
    # The session's household is the ONLY one to re-wrap: a user belongs to
    # exactly one, enforced at the two doors that mint memberships (accepting
    # an invite as a user who already has one returns `conflict`, and
    # create_member refuses an email already in use). If that ever changes,
    # this call has to fan out over every membership or the other households'
    # wraps go stale — which is the retired password still working as a key.
    if not household_crypto.on_password_changed(
        engine,
        session.household_id,
        session.user_id,
        payload.current_password,
        payload.new_password,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Your encryption key could not be re-created, so the password was "
                "not changed. Sign in again and retry."
            ),
        )

    repository.set_user_password_hash(
        engine, session.user_id, security.hash_password(payload.new_password)
    )
    # The point of changing a password is usually that somebody else has it —
    # so every other session goes, and only the one in hand survives.
    revoked = repository.revoke_other_auth_sessions(
        engine, session.user_id, security.hash_token(token)
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "auth.password_changed",
        "user",
        session.user_id,
        "Changed their password and signed out their other sessions"
        if revoked
        else "Changed their password",
    )
    return Response(status_code=204)


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
