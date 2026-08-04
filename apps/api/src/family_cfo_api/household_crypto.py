"""Per-household envelope encryption (ADR 0072, Phase 1).

Each household gets a random data-encryption key (DEK), stored only wrapped
by the box master key (`FAMILY_CFO_MASTER_KEY`). Content columns are
encrypted under a per-purpose subkey derived from the DEK, so a database
dump, a stolen disk, or a whole-box backup no longer reads any household's
chats, advisor answers, memories, feedback, or document text.

Phase 1 scope (convenient mode for every household): the box holds the only
wrap, so unattended jobs keep working — the honest claim is "sealed against
offline artifacts", NOT "sealed against the box operator" (that is Phase 2/3,
member- and device-wrapped keys). ADR 0070's invariant applies: never claim
more than the mode delivers.

Format: encrypted values are `enc1:<fernet token>`. Reads treat anything
without the prefix as legacy plaintext, so existing rows keep working until
`python -m family_cfo_api.tools.encrypt_existing` re-encrypts them. With no
master key configured, encryption is OFF and every call passes through —
graceful for dev/test setups.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from family_cfo_api.config import get_settings

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc1:"

# Unwrapped DEKs cached per household — the wrap only changes on rotation
# (Phase 2), which restarts the process. Guarded for the worker's threads.
_dek_cache: dict[str, bytes] = {}
_cache_lock = threading.Lock()


def _master_fernet() -> Fernet | None:
    key = get_settings().master_key
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        logger.error("FAMILY_CFO_MASTER_KEY is not a valid Fernet key; encryption disabled")
        return None


def enabled() -> bool:
    return _master_fernet() is not None


def _get_or_create_dek(engine: Engine, household_id: str, master: Fernet) -> bytes:
    with _cache_lock:
        cached = _dek_cache.get(household_id)
    if cached is not None:
        return cached

    from family_cfo_api import models, repository

    with engine.connect() as conn:
        row = conn.execute(
            select(models.household_keys.c.wrapped_dek).where(
                models.household_keys.c.household_id == household_id
            )
        ).first()
    if row is not None:
        dek = master.decrypt(row[0].encode())
    else:
        dek = Fernet.generate_key()  # 32 random bytes, urlsafe-b64 encoded
        try:
            with engine.begin() as conn:
                conn.execute(
                    insert(models.household_keys).values(
                        id=repository.new_id(),
                        household_id=household_id,
                        wrapped_dek=master.encrypt(dek).decode(),
                        created_at=repository.utcnow(),
                    )
                )
        except Exception:  # noqa: BLE001 — concurrent first-write: reread the winner
            with engine.connect() as conn:
                row = conn.execute(
                    select(models.household_keys.c.wrapped_dek).where(
                        models.household_keys.c.household_id == household_id
                    )
                ).first()
            if row is None:
                raise
            dek = master.decrypt(row[0].encode())

    with _cache_lock:
        _dek_cache[household_id] = dek
    return dek


def _subkey_fernet(dek: bytes, purpose: bytes) -> Fernet:
    """A per-purpose key derived from the DEK, so row data and (later) backup
    archives never share a key even though they share a household."""
    derived = hashlib.sha256(b"family-cfo-hkdf:" + purpose + b":" + dek).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _row_fernet(engine: Engine, household_id: str) -> Fernet | None:
    master = _master_fernet()
    if master is None:
        return None
    dek = _get_or_create_dek(engine, household_id, master)
    return _subkey_fernet(dek, b"rows")


def encrypt_text(engine: Engine, household_id: str, value: str | None) -> str | None:
    """Encrypt a content column value for storage; passthrough when disabled."""
    if value is None:
        return None
    fernet = _row_fernet(engine, household_id)
    if fernet is None:
        return value
    return ENC_PREFIX + fernet.encrypt(value.encode()).decode()


def decrypt_text(engine: Engine, household_id: str, value: str | None) -> str | None:
    """Decrypt a stored value; legacy plaintext (no prefix) passes through."""
    if value is None or not value.startswith(ENC_PREFIX):
        return value
    fernet = _row_fernet(engine, household_id)
    if fernet is None:
        # Key configured away while encrypted rows exist — surface loudly
        # rather than hand ciphertext to the UI.
        logger.error("encrypted row present but no master key configured")
        return "[encrypted — master key unavailable]"
    try:
        return fernet.decrypt(value[len(ENC_PREFIX) :].encode()).decode()
    except InvalidToken:
        # Wrong household's key would land here — the cross-household guarantee.
        logger.error("row decryption failed for household=%s", household_id)
        return "[encrypted — key mismatch]"


def reset_cache_for_tests() -> None:
    with _cache_lock:
        _dek_cache.clear()


# --- Phase 2 (ADR 0072): member, device, and recovery wraps -------------------
#
# The DEK gains additional wraps beyond the box master key. In convenient mode
# they are dormant redundancy; they become the ONLY unwrap paths in sealed mode
# (Phase 3). Member wraps derive a KEK from the member's password (separate
# PBKDF2 salt from auth — the auth hash must never double as a KEK); device
# wraps use ECIES against the paired device's existing P-256 public key;
# the recovery wrap derives from a one-time-displayed secret.

MEMBER_KDF_ITERATIONS = 390_000


def _password_kek(password: str, salt: bytes) -> Fernet:
    derived = hashlib.pbkdf2_hmac(
        "sha256", b"family-cfo-kek:" + password.encode(), salt, MEMBER_KDF_ITERATIONS
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def _wrap_with_password(dek: bytes, password: str) -> str:
    salt = os.urandom(16)
    token = _password_kek(password, salt).encrypt(dek).decode()
    return json.dumps({"v": 1, "kdf": "pbkdf2_sha256", "salt": salt.hex(), "token": token})


def unwrap_with_password(payload_json: str, password: str) -> bytes | None:
    try:
        payload = json.loads(payload_json)
        salt = bytes.fromhex(payload["salt"])
        return _password_kek(password, salt).decrypt(payload["token"].encode())
    except Exception:  # noqa: BLE001 — wrong password or malformed wrap
        return None


def _wrap_with_p256(dek: bytes, public_key_raw: bytes) -> str:
    """ECIES: ephemeral P-256 ECDH against the device's stored public key,
    HKDF-SHA256 to an AES-GCM key. The device unwraps with its private key
    (CryptoKit imports the same raw key for KeyAgreement) in sealed mode."""
    from cryptography.hazmat.primitives import hashes as h
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    device_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b"\x04" + public_key_raw if len(public_key_raw) == 64 else public_key_raw
    )
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    shared = ephemeral.exchange(ec.ECDH(), device_pub)
    key = HKDF(algorithm=h.SHA256(), length=32, salt=None, info=b"family-cfo-device-wrap").derive(
        shared
    )
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, dek, None)
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    epk = ephemeral.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return json.dumps(
        {"v": 1, "alg": "ecies-p256-hkdf-aesgcm", "epk": epk.hex(), "nonce": nonce.hex(),
         "ct": ciphertext.hex()}
    )


def _dek_for_wrapping(engine: Engine, household_id: str) -> bytes | None:
    master = _master_fernet()
    if master is None:
        return None
    return _get_or_create_dek(engine, household_id, master)


def _upsert_wrap(
    engine: Engine, household_id: str, kind: str, subject_id: str | None, wrap_json: str
) -> None:
    from sqlalchemy import delete as sql_delete

    from family_cfo_api import models, repository

    with engine.begin() as conn:
        conn.execute(
            sql_delete(models.household_key_wraps).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind == kind,
                (models.household_key_wraps.c.subject_id == subject_id)
                if subject_id is not None
                else models.household_key_wraps.c.subject_id.is_(None),
            )
        )
        conn.execute(
            insert(models.household_key_wraps).values(
                id=repository.new_id(),
                household_id=household_id,
                kind=kind,
                subject_id=subject_id,
                wrap_json=wrap_json,
                created_at=repository.utcnow(),
            )
        )


def ensure_member_wrap(engine: Engine, household_id: str, user_id: str, password: str) -> None:
    """Create/refresh the member's password-derived wrap. Called wherever a
    password is PROVEN (login) or SET (invite accept, member create) — the only
    moments the plaintext password exists. Never raises: wrap upkeep must not
    break a login."""
    try:
        dek = _dek_for_wrapping(engine, household_id)
        if dek is None:
            return
        _upsert_wrap(engine, household_id, "member", user_id, _wrap_with_password(dek, password))
    except Exception:  # noqa: BLE001
        logger.exception("member wrap upkeep failed household=%s", household_id)


def ensure_device_wrap(
    engine: Engine, household_id: str, device_id: str, public_key_b64: str
) -> None:
    """Create/refresh a paired device's ECIES wrap from its stored public key."""
    try:
        dek = _dek_for_wrapping(engine, household_id)
        if dek is None:
            return
        raw = base64.b64decode(public_key_b64)
        _upsert_wrap(engine, household_id, "device", device_id, _wrap_with_p256(dek, raw))
    except Exception:  # noqa: BLE001
        logger.exception("device wrap upkeep failed household=%s", household_id)


def generate_recovery_key(engine: Engine, household_id: str) -> str | None:
    """Mint (or replace) the household recovery key: returned ONCE, stored only
    as a wrap. Losing every password, device, and this key loses the data —
    that is the guarantee working as designed (ADR 0072)."""
    dek = _dek_for_wrapping(engine, household_id)
    if dek is None:
        return None
    secret = "FCFO-" + secrets.token_urlsafe(32)
    _upsert_wrap(engine, household_id, "recovery", None, _wrap_with_password(dek, secret))
    return secret


def wrap_status(engine: Engine, household_id: str) -> dict:
    """Which unwrap paths exist — the Phase 2 posture the UI reports."""
    from family_cfo_api import models

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                models.household_key_wraps.c.kind, models.household_key_wraps.c.created_at
            ).where(models.household_key_wraps.c.household_id == household_id)
        ).all()
    kinds = [row[0] for row in rows]
    recovery_at = next((row[1] for row in rows if row[0] == "recovery"), None)
    return {
        "encryption_enabled": enabled(),
        "member_wraps": kinds.count("member"),
        "device_wraps": kinds.count("device"),
        "has_recovery_key": "recovery" in kinds,
        "recovery_key_created_at": recovery_at,
    }


#: Every sealed (table, columns, household-resolver) — shared by the one-shot
#: sealer and key rotation. Resolvers take a row mapping and the prefetch dict.
def sealed_tables(models_module):
    return [
        (models_module.conversation_messages, ["content"], "conversation"),
        (models_module.recommendations, ["answer"], "household_id"),
        (models_module.household_memories, ["value"], "household_id"),
        (models_module.advisor_feedback, ["note"], "household_id"),
        (models_module.document_extractions, ["text"], "document"),
        (models_module.transactions, ["merchant", "description", "note"], "household_id"),
        (models_module.accounts, ["name", "institution"], "household_id"),
        (models_module.bills, ["name"], "household_id"),
        (models_module.income_sources, ["name"], "household_id"),
        (models_module.goals, ["name"], "household_id"),
        (models_module.audit_events, ["summary", "undo_token"], "household_id"),
        (models_module.reports, ["explanation_text"], "household_id"),
    ]


def rotate_household_key(engine: Engine, household_id: str) -> bool:
    """Member removal (ADR 0072): new DEK, re-encrypt every sealed row, then
    fix the wraps — device wraps re-wrap from stored public keys; member wraps
    are DELETED (a password can't be re-derived server-side) and come back at
    each member's next login; the recovery wrap is deleted and the household is
    told to mint a new key (wrap_status shows has_recovery_key=false)."""
    from sqlalchemy import delete as sql_delete, update as sql_update

    from family_cfo_api import models

    master = _master_fernet()
    if master is None:
        return False
    old_dek = _get_or_create_dek(engine, household_id, master)
    old_rows = _subkey_fernet(old_dek, b"rows")
    new_dek = Fernet.generate_key()
    new_rows = _subkey_fernet(new_dek, b"rows")

    with engine.connect() as conn:
        conversation_households = {
            row[0]: row[1]
            for row in conn.execute(
                select(models.conversations.c.id, models.conversations.c.household_id)
            )
        }
        document_households = {
            row[0]: row[1]
            for row in conn.execute(
                select(models.documents.c.id, models.documents.c.household_id)
            )
        }

    def belongs(row, resolver) -> bool:
        if resolver == "household_id":
            return row["household_id"] == household_id
        if resolver == "conversation":
            return conversation_households.get(row["conversation_id"]) == household_id
        return document_households.get(row["document_id"]) == household_id

    for table, columns, resolver in sealed_tables(models):
        with engine.connect() as conn:
            rows = conn.execute(select(table)).mappings().all()
        for row in rows:
            if not belongs(row, resolver):
                continue
            values = {}
            for column in columns:
                value = row[column]
                if value is None or not value.startswith(ENC_PREFIX):
                    continue
                plaintext = old_rows.decrypt(value[len(ENC_PREFIX):].encode())
                values[column] = ENC_PREFIX + new_rows.encrypt(plaintext).decode()
            if values:
                with engine.begin() as conn:
                    conn.execute(
                        sql_update(table).where(table.c.id == row["id"]).values(**values)
                    )

    with engine.begin() as conn:
        conn.execute(
            sql_update(models.household_keys)
            .where(models.household_keys.c.household_id == household_id)
            .values(wrapped_dek=master.encrypt(new_dek).decode())
        )
        conn.execute(
            sql_delete(models.household_key_wraps).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind.in_(("member", "recovery")),
            )
        )
    with _cache_lock:
        _dek_cache[household_id] = new_dek

    # Device wraps re-wrap from the stored public keys.
    from family_cfo_api import repository

    for device in repository.list_paired_devices(engine, household_id):
        if device.revoked_at is None and getattr(device, "public_key", None):
            ensure_device_wrap(engine, household_id, device.id, device.public_key)
    return True
