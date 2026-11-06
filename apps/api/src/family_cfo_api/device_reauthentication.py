"""Challenge storage and atomic replacement of an existing device's bearer."""

from __future__ import annotations

import base64
from datetime import timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from family_cfo_api import audit, models, repository, security

CHALLENGE_TTL = timedelta(minutes=5)
CHALLENGE_CAPACITY = 2048
# Distinct transaction advisory lock namespace for challenge issuance.
_ISSUANCE_LOCK = 0x4643464F52454155


def issue_challenge(engine: Engine, device_id: str) -> str | None:
    challenge = security.generate_access_token()
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(select(func.pg_advisory_xact_lock(_ISSUANCE_LOCK)))
        now = repository.utcnow()
        table = models.device_reauth_challenges
        # SQLite takes its write lock here before reading the shared capacity.
        conn.execute(delete(table).where(table.c.expires_at <= now))
        if conn.scalar(select(func.count()).select_from(table)) >= CHALLENGE_CAPACITY:
            return None
        conn.execute(
            insert(table).values(
                challenge_hash=security.hash_token(challenge),
                device_id=device_id,
                expires_at=now + CHALLENGE_TTL,
            )
        )
    return challenge


def prune_expired_challenges(engine: Engine) -> None:
    with engine.begin() as conn:
        table = models.device_reauth_challenges
        conn.execute(delete(table).where(table.c.expires_at <= repository.utcnow()))


def verify_proof(public_key: str, device_id: str, challenge: str, signature: str) -> bool:
    message = f"family-cfo/device-reauth/v1\n{device_id}\n{challenge}".encode()
    try:
        raw_key = base64.b64decode(public_key, validate=True)
        point = b"\x04" + raw_key if len(raw_key) == 64 else raw_key
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        raw_sig = base64.b64decode(signature + "==", altchars=b"-_", validate=True)
        if len(raw_sig) != 64:
            return False
        der = encode_dss_signature(
            int.from_bytes(raw_sig[:32], "big"), int.from_bytes(raw_sig[32:], "big")
        )
        key.verify(der, message, ec.ECDSA(hashes.SHA256()))
    except (ValueError, InvalidSignature):
        return False
    return True


def replace_device_session(
    engine: Engine,
    *,
    device_id: str,
    user_id: str,
    challenge: str,
    signature: str,
    ttl: timedelta,
) -> repository.DeviceCredentialRecord | None:
    devices = models.paired_devices
    challenges = models.device_reauth_challenges
    sessions = models.auth_sessions
    memberships = models.household_memberships
    with engine.begin() as conn:
        # The device row is the shared write lock with revocation. Updating an
        # unchanged field also takes the write lock on SQLite.
        device = conn.execute(
            update(devices)
            .where(
                devices.c.id == device_id,
                devices.c.user_id == user_id,
                devices.c.revoked_at.is_(None),
            )
            .values(last_seen_at=devices.c.last_seen_at)
            .returning(devices.c.household_id, devices.c.public_key)
        ).one_or_none()
        if device is None:
            return None
        if not conn.scalar(
            select(
                select(memberships.c.user_id)
                .where(
                    memberships.c.user_id == user_id,
                    memberships.c.household_id == device.household_id,
                )
                .exists()
            )
        ) or not verify_proof(device.public_key, device_id, challenge, signature):
            return None

        now = repository.utcnow()
        consumed = conn.execute(
            delete(challenges).where(
                challenges.c.challenge_hash == security.hash_token(challenge),
                challenges.c.device_id == device_id,
                challenges.c.expires_at > now,
            )
        )
        if consumed.rowcount != 1:
            return None
        conn.execute(
            update(sessions)
            .where(
                sessions.c.device_id == device_id,
                sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        token = security.generate_access_token()
        expires_at = now + ttl
        conn.execute(
            insert(sessions).values(
                id=repository.new_id(),
                user_id=user_id,
                household_id=device.household_id,
                device_id=device_id,
                token_hash=security.hash_token(token),
                created_at=now,
                expires_at=expires_at,
            )
        )
        audit.write_audit(
            engine,
            device.household_id,
            user_id,
            "pairing.reauthenticate",
            "paired_device",
            device_id,
            "Signed in again on existing device",
            connection=conn,
        )
    return repository.DeviceCredentialRecord(
        device_id, token, expires_at, device.household_id, user_id
    )
