from __future__ import annotations

import json
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, security
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
    PairingConfirmRequest,
    PairingLoginRequest,
    PairingSession,
    PairingSessionCreateRequest,
)

router = APIRouter(tags=["Pairing"])
logger = logging.getLogger(__name__)

PAIRING_SESSION_TTL = timedelta(minutes=10)
DEVICE_SESSION_TTL = timedelta(days=30)


def _api_base_url(request: Request) -> str:
    # Behind the nginx TLS proxy, request.base_url reflects the INTERNAL
    # http://api:8000 request, not the address a phone must reach. Prefer the
    # forwarded scheme + host — X-Forwarded-Host carries the external port
    # (e.g. 192.168.1.10:8443) — so the pairing QR points at the real,
    # reachable endpoint. Falls back to request.base_url for direct/dev access.
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if proto and host:
        return f"{proto}://{host}/api/v1"
    return f"{str(request.base_url).rstrip('/')}/api/v1"


def certificate_fingerprint(cert_path: str) -> str | None:
    """M83a: SHA-256 of the DER certificate — the value an iOS client pins."""
    import base64
    import hashlib
    import re
    from pathlib import Path

    if not cert_path:
        return None
    try:
        pem = Path(cert_path).read_text()
    except OSError:
        return None
    match = re.search(
        r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", pem, re.S
    )
    if match is None:
        return None
    der = base64.b64decode("".join(match.group(1).split()))
    return hashlib.sha256(der).hexdigest()


def _to_device_schema(record: repository.PairedDeviceRecord) -> PairedDevice:
    return PairedDevice(
        id=record.id,
        user_id=record.user_id,
        name=record.name,
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
        revoked_at=record.revoked_at,
    )


@router.post(
    "/pairing/sessions",
    operation_id="createPairingSession",
    response_model=PairingSession,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Target member not in this household", "model": ErrorResponse},
    },
    summary="Create a short-lived pairing session for a device",
)
async def create_pairing_session(
    request: Request,
    payload: PairingSessionCreateRequest = PairingSessionCreateRequest(),
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> PairingSession:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    # The device pairs AS this user. Normally that's the caller; an owner may mint
    # the code for another member so a regular member never signs into the
    # dashboard to pair their phone. An owner can only ever be targeted by
    # themselves — you can't mint owner-level access for someone else this way.
    pair_as_user_id = session.user_id
    if payload.user_id is not None and payload.user_id != session.user_id:
        if rights.DEVICES_MANAGE not in session.rights:
            raise HTTPException(
                status_code=403, detail="Pairing a device for another member needs device management"
            )
        target_role = repository.get_membership_role(
            engine, session.household_id, payload.user_id
        )
        if target_role is None:
            raise HTTPException(status_code=404, detail="That member is not in this household")
        if target_role == "owner":
            raise HTTPException(
                status_code=403, detail="Owners must pair their own device"
            )
        pair_as_user_id = payload.user_id

    # CSPRNG token, not a uuid4: this id is the QR-borne bearer secret for
    # unauthenticated /pairing/confirm (ADR 0010).
    pairing_session_id = security.generate_pairing_secret()
    expires_at = repository.utcnow() + PAIRING_SESSION_TTL
    from family_cfo_api.config import get_settings

    qr_payload = json.dumps(
        {
            "type": "family-cfo-pairing",
            "version": 1,
            "api_base_url": _api_base_url(request),
            "pairing_session_id": pairing_session_id,
            "household_id": household.id,
            "household_name": household.display_name,
            "expires_at": expires_at.isoformat(),
            # M83a: lets the iOS app pin the self-signed cert (ADR 0018 era
            # trust model) — null when the api cannot read the cert.
            "certificate_sha256": certificate_fingerprint(get_settings().tls_cert_path),
        },
        separators=(",", ":"),
        sort_keys=True,
    )

    # One valid QR per user: minting a new code invalidates any pending one, so a
    # previously shown (or leaked) code can no longer pair.
    repository.revoke_pending_pairing_sessions(engine, session.household_id, pair_as_user_id)

    record = repository.create_pairing_session(
        engine,
        pairing_session_id=pairing_session_id,
        household_id=session.household_id,
        created_by_user_id=pair_as_user_id,
        qr_payload=qr_payload,
        expires_at=expires_at,
    )

    logger.info(
        "pairing session created household_id=%s pairing_session_id=%s",
        session.household_id,
        record.id,
    )

    return PairingSession(id=record.id, qr_payload=record.qr_payload, expires_at=record.expires_at)


@router.post(
    "/pairing/confirm",
    operation_id="confirmPairing",
    response_model=DeviceCredential,
    responses={400: {"description": "Invalid pairing session", "model": ErrorResponse}},
    summary="Confirm mobile device pairing",
)
async def confirm_pairing(
    payload: PairingConfirmRequest,
    engine: Engine = Depends(get_engine),
) -> DeviceCredential:
    token = security.generate_access_token()
    expires_at = repository.utcnow() + DEVICE_SESSION_TTL
    credential = repository.confirm_pairing_session(
        engine,
        pairing_session_id=payload.pairing_session_id,
        device_name=payload.device_name,
        device_public_key=payload.device_public_key,
        access_token=token,
        token_hash=security.hash_token(token),
        expires_at=expires_at,
    )
    if credential is None:
        raise HTTPException(status_code=400, detail="Pairing session is invalid or expired")

    logger.info(
        "device paired pairing_session_id=%s device_id=%s",
        payload.pairing_session_id,
        credential.device_id,
    )
    audit.write_audit(
        engine,
        credential.household_id,
        credential.user_id,
        "pairing.confirmed",
        "paired_device",
        credential.device_id,
        f"Paired device '{payload.device_name}'",
    )

    return _device_credential(engine, credential)


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
        # M83: the device acts as the pairing session's creator; surfacing
        # that user's role lets the mobile app build its role-aware shell.
        role=repository.get_membership_role(engine, credential.household_id, credential.user_id),
        role_name=role_name or None,
        rights=sorted(member_rights),
        # ADR 0056: the login path has no QR payload to learn the household
        # from, so the credential carries it.
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
    summary="Sign a device in with email + password (credentialed pairing)",
)
async def create_device_session_with_password(
    payload: PairingLoginRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    rate_limiter: AuthRateLimiter = Depends(get_rate_limiter),
) -> DeviceCredential:
    """ADR 0056: the iOS login screen. Same outcome as a QR confirm — a paired
    device with a device-bound session — so the Devices page and revocation
    cover phones however they signed in. Shares the brute-force counters with
    web login (ADR 0010)."""
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
    },
    summary="Revoke a paired device",
)
async def revoke_paired_device(
    device_id: str,
    session: repository.SessionContext = Depends(require_right(rights.DEVICES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    revoked = repository.revoke_paired_device(engine, session.household_id, device_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Paired device not found")

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
