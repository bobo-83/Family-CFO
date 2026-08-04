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
import logging
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
