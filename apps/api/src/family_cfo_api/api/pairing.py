from __future__ import annotations

import json
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, security
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    DeviceCredential,
    ErrorResponse,
    PairedDevice,
    PairedDeviceListResponse,
    PairingConfirmRequest,
    PairingSession,
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
    },
    summary="Create a short-lived pairing session for a device",
)
async def create_pairing_session(
    request: Request,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> PairingSession:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

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

    record = repository.create_pairing_session(
        engine,
        pairing_session_id=pairing_session_id,
        household_id=session.household_id,
        created_by_user_id=session.user_id,
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

    return DeviceCredential(
        device_id=credential.device_id,
        access_token=credential.access_token,
        expires_at=credential.expires_at,
        # M83: the device acts as the pairing session's creator; surfacing
        # that user's role lets the mobile app build its role-aware shell.
        role=repository.get_membership_role(engine, credential.household_id, credential.user_id),
    )


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
    session: repository.SessionContext = Depends(require_role("owner")),
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
