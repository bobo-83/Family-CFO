from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, household_crypto, repository, rights, security
from family_cfo_api.deps import (
    client_ip,
    get_current_session,
    get_engine,
    get_rate_limiter,
    require_right,
)
from family_cfo_api.ratelimit import AuthRateLimiter
from family_cfo_api.schemas import (
    DeviceCredential,
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
