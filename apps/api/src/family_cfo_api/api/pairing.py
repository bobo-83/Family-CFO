from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.engine import Engine

from family_cfo_api import (
    audit,
    device_reauthentication,
    household_crypto,
    repository,
    rights,
    security,
)
from family_cfo_api.deps import (
    client_ip,
    get_challenge_rate_limiter,
    get_current_session,
    get_engine,
    get_rate_limiter,
    require_right,
)
from family_cfo_api.ratelimit import AuthRateLimiter
from family_cfo_api.schemas import (
    DeviceCredential,
    DeviceReauthenticationChallenge,
    DeviceReauthenticationChallengeRequest,
    DeviceReauthenticationRequest,
    ErrorResponse,
    PairedDevice,
    PairedDeviceListResponse,
    PairingLoginRequest,
)

router = APIRouter(tags=["Pairing"])
logger = logging.getLogger(__name__)
DEVICE_SESSION_TTL = timedelta(days=30)


def _to_device_schema(record: repository.PairedDeviceRecord) -> PairedDevice:
    return PairedDevice(
        id=record.id,
        user_id=record.user_id,
        name=record.name,
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
        revoked_at=record.revoked_at,
    )


def _device_credential(
    engine: Engine, credential: repository.DeviceCredentialRecord
) -> DeviceCredential:
    member_rights, role_name = repository.resolve_member_rights(
        engine, credential.household_id, credential.user_id
    )
    household = repository.get_household(engine, credential.household_id)
    return DeviceCredential(
        device_id=credential.device_id,
        access_token=credential.access_token,
        expires_at=credential.expires_at,
        role=repository.get_membership_role(engine, credential.household_id, credential.user_id),
        role_name=role_name or None,
        rights=sorted(member_rights),
        household_id=credential.household_id,
        household_name=household.display_name if household else None,
    )


@router.post(
    "/pairing/login",
    operation_id="createDeviceSessionWithPassword",
    response_model=DeviceCredential,
    status_code=201,
    responses={
        401: {"description": "Invalid credentials", "model": ErrorResponse},
        429: {"description": "Too many attempts", "model": ErrorResponse},
    },
    summary="Sign a device in with email + password",
)
async def create_device_session_with_password(
    payload: PairingLoginRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    rate_limiter: AuthRateLimiter = Depends(get_rate_limiter),
) -> DeviceCredential:
    """ADR 0079: account-only new-device sign-in. Preserve member-owned device
    sessions, encryption wraps and the shared web-login brute-force counters.
    Already-issued device credentials need no bootstrap protocol to stay valid.
    """
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
    if household_id is None:
        rate_limiter.record_failure(limit_keys)
        raise HTTPException(status_code=401, detail="User has no household membership")
    rate_limiter.reset(limit_keys)

    token = security.generate_access_token()
    credential = repository.create_paired_device_with_session(
        engine,
        household_id=household_id,
        user_id=user.id,
        device_name=payload.device_name,
        device_public_key=payload.device_public_key,
        access_token=token,
        token_hash=security.hash_token(token),
        expires_at=repository.utcnow() + DEVICE_SESSION_TTL,
    )
    household_crypto.on_password_established(engine, household_id, user.id, payload.password)
    household_crypto.ensure_device_wrap(
        engine, household_id, credential.device_id, payload.device_public_key
    )
    audit.write_audit(
        engine,
        household_id,
        user.id,
        "pairing.login",
        "paired_device",
        credential.device_id,
        f"Signed in device '{payload.device_name}'",
    )
    return _device_credential(engine, credential)


@router.post(
    "/pairing/reauthentication/challenge",
    operation_id="createDeviceReauthenticationChallenge",
    response_model=DeviceReauthenticationChallenge,
    status_code=201,
    responses={429: {"description": "Too many attempts", "model": ErrorResponse}},
    summary="Issue a single-use challenge for existing device sign-in",
)
async def create_device_reauthentication_challenge(
    payload: DeviceReauthenticationChallengeRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    challenge_limiter: AuthRateLimiter = Depends(get_challenge_rate_limiter),
) -> DeviceReauthenticationChallenge:
    keys = [f"device-reauth-challenge-ip:{client_ip(request)}"]
    _check_reauthentication_rate_limit(challenge_limiter, keys)
    # Charge issuance — but against the challenge policy, whose counters reset
    # after a short pause. Password guessing stays on the login limiter below.
    challenge_limiter.record_failure(keys)
    challenge = device_reauthentication.issue_challenge(engine, payload.device_id)
    if challenge is None:
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in requests. Try again later.",
            headers={"Retry-After": str(int(device_reauthentication.CHALLENGE_TTL.total_seconds()))},
        )
    return DeviceReauthenticationChallenge(challenge=challenge)


def _check_reauthentication_rate_limit(limiter: AuthRateLimiter, keys: list[str]) -> None:
    retry_after = limiter.retry_after(keys)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def _check_completion_rate_limit(limiter: AuthRateLimiter, keys: list[str]) -> None:
    """The challenge policy's answer to a flood of failed completions."""
    retry_after = limiter.retry_after(keys)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many reauthentication attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


@router.post(
    "/pairing/reauthentication",
    operation_id="reauthenticateDevice",
    response_model=DeviceCredential,
    status_code=201,
    responses={
        401: {"description": "Invalid credentials or device proof", "model": ErrorResponse},
        429: {"description": "Too many attempts", "model": ErrorResponse},
    },
    summary="Sign in again on an existing device using a password and device proof",
)
async def reauthenticate_device(
    payload: DeviceReauthenticationRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    rate_limiter: AuthRateLimiter = Depends(get_rate_limiter),
    challenge_limiter: AuthRateLimiter = Depends(get_challenge_rate_limiter),
) -> DeviceCredential:
    keys = [f"ip:{client_ip(request)}", f"email:{payload.email.lower()}"]
    _check_reauthentication_rate_limit(rate_limiter, keys)
    # A flood of failed device proofs is its own kind of abuse: it never
    # touches the password policy, so the challenge policy answers it — on its
    # own key, so issuance is not gated by it.
    completion_keys = [f"device-reauth-completion-ip:{client_ip(request)}"]
    _check_completion_rate_limit(challenge_limiter, completion_keys)
    user = repository.get_user_by_email(engine, payload.email)
    credential = None
    password_proved = False
    if user is not None and security.verify_password(payload.password, user.password_hash):
        password_proved = True
        credential = device_reauthentication.replace_device_session(
            engine,
            device_id=payload.device_id,
            user_id=user.id,
            challenge=payload.challenge,
            signature=payload.signature,
            ttl=DEVICE_SESSION_TTL,
        )
    if credential is None:
        if password_proved:
            # The password was right; the device proof or the challenge was
            # not. That is not password guessing, so it must not charge the
            # household's password policy — a shared home IP renewing several
            # devices would lock the family's own phones out. It counts
            # against the challenge policy instead, on its own key.
            challenge_limiter.record_failure(completion_keys)
        else:
            # A wrong password is the brute-force the password policy exists
            # for (ADR 0010): charge it per IP and per account.
            rate_limiter.record_failure(keys)
        raise HTTPException(status_code=401, detail="Unable to sign in with this device")
    # A successful sign-in clears this account's own strikes. The IP counter is
    # shared with the household's other devices and its password guesses, so
    # one success here must not launder them.
    rate_limiter.reset([f"email:{payload.email.lower()}"])
    return _device_credential(engine, credential)


@router.get(
    "/pairing/devices",
    operation_id="listPairedDevices",
    response_model=PairedDeviceListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List paired devices",
)
async def list_paired_devices(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> PairedDeviceListResponse:
    records = repository.list_paired_devices(engine, session.household_id)
    return PairedDeviceListResponse(devices=[_to_device_schema(record) for record in records])


@router.delete(
    "/pairing/devices/{device_id}",
    operation_id="revokePairedDevice",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Paired device not found", "model": ErrorResponse},
        409: {"description": "Cannot revoke the calling device", "model": ErrorResponse},
    },
    summary="Revoke a paired device",
)
async def revoke_paired_device(
    device_id: str,
    session: repository.SessionContext = Depends(require_right(rights.DEVICES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    if session.device_id == device_id:
        raise HTTPException(
            status_code=409,
            detail="You can't revoke the device you're using. Revoke it from another "
            "device or the web dashboard.",
        )
    revoked = repository.revoke_paired_device(engine, session.household_id, device_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Paired device not found")
    household_crypto.delete_device_wrap(engine, session.household_id, device_id)
    logger.info(
        "paired device revoked household_id=%s device_id=%s", session.household_id, device_id
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "pairing.device_revoked",
        "paired_device",
        device_id,
        "Revoked paired device",
    )
    return Response(status_code=204)
