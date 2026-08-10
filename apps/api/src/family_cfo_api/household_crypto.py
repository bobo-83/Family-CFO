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

# ADR 0072 Phase 3: sealed households have NO box wrap — their DEK exists here
# only while a member/device session keeps it alive (sliding TTL), and the
# guarantee is exactly that: restart the box, and sealed content is unreadable
# until a member signs in or a device posts its unwrapped key.
SESSION_KEYRING_TTL_SECONDS = 30 * 60
_session_keyring: dict[str, tuple[bytes, float]] = {}

CANARY_PLAINTEXT = b"family-cfo-canary-v1"


class HouseholdLockedError(Exception):
    """Sealed household with no live session key — mapped to HTTP 423."""

    def __init__(self, household_id: str):
        self.household_id = household_id
        super().__init__(
            "This household's data is sealed and currently locked. "
            "Sign in again to unlock it."
        )


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
            _write_canary(engine, household_id, dek)
        except Exception:
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


def _keyring_get(household_id: str) -> bytes | None:
    import time

    with _cache_lock:
        entry = _session_keyring.get(household_id)
        if entry is None:
            return None
        dek, expires = entry
        if time.monotonic() > expires:
            del _session_keyring[household_id]
            return None
        # Sliding TTL: activity keeps the household unlocked.
        _session_keyring[household_id] = (dek, time.monotonic() + SESSION_KEYRING_TTL_SECONDS)
        return dek


def _keyring_put(household_id: str, dek: bytes) -> None:
    import time

    with _cache_lock:
        _session_keyring[household_id] = (
            dek,
            time.monotonic() + SESSION_KEYRING_TTL_SECONDS,
        )


def _box_wrap_row(engine: Engine, household_id: str):
    from family_cfo_api import models

    with engine.connect() as conn:
        return conn.execute(
            select(
                models.household_keys.c.wrapped_dek, models.household_keys.c.canary
            ).where(models.household_keys.c.household_id == household_id)
        ).first()


def _resolve_dek(engine: Engine, household_id: str, master: Fernet) -> bytes:
    """Convenient mode: the box wrap (cached). Sealed mode: the session keyring
    or HouseholdLockedError."""
    with _cache_lock:
        cached = _dek_cache.get(household_id)
    if cached is not None:
        return cached
    row = _box_wrap_row(engine, household_id)
    if row is not None and row[0] is None:
        # Sealed: no box wrap on purpose.
        dek = _keyring_get(household_id)
        if dek is None:
            raise HouseholdLockedError(household_id)
        return dek
    try:
        return _get_or_create_dek(engine, household_id, master)
    except InvalidToken:
        # The box wrap exists but this master key can't open it — the
        # restored-onto-new-hardware case. Behave like a locked sealed
        # household: member passwords, devices, or the recovery key unlock,
        # and the unlock path re-mints the wrap under the CURRENT master.
        dek = _keyring_get(household_id)
        if dek is None:
            logger.warning(
                "box wrap is stale (master key changed?) household=%s — locked "
                "until a member password, device, or recovery key unlocks it",
                household_id,
            )
            raise HouseholdLockedError(household_id) from None
        return dek


def _row_fernet(engine: Engine, household_id: str) -> Fernet | None:
    master = _master_fernet()
    if master is None:
        return None
    dek = _resolve_dek(engine, household_id, master)
    return _subkey_fernet(dek, b"rows")


def dek_available(engine: Engine, household_id: str) -> bool:
    """True when content for this household can be read/written right now."""
    master = _master_fernet()
    if master is None:
        return True  # encryption off: plaintext passthrough
    try:
        _resolve_dek(engine, household_id, master)
        return True
    except HouseholdLockedError:
        return False


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
        _session_keyring.clear()


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
    return _resolve_dek(engine, household_id, master)


def _canary_ok(engine: Engine, household_id: str, dek: bytes) -> bool:
    """A posted/unwrapped DEK must decrypt the stored canary — the check that
    stops a wrong or stale key from silently poisoning new writes."""
    row = _box_wrap_row(engine, household_id)
    if row is None or row[1] is None:
        # No canary recorded (pre-Phase-3 rows): accept only when a box wrap
        # exists to compare against.
        if row is not None and row[0] is not None:
            master = _master_fernet()
            return master is not None and master.decrypt(row[0].encode()) == dek
        return True
    try:
        return _subkey_fernet(dek, b"rows").decrypt(row[1].encode()) == CANARY_PLAINTEXT
    except Exception:  # noqa: BLE001
        return False


def _write_canary(engine: Engine, household_id: str, dek: bytes) -> None:
    from sqlalchemy import update as sql_update

    from family_cfo_api import models

    canary = _subkey_fernet(dek, b"rows").encrypt(CANARY_PLAINTEXT).decode()
    with engine.begin() as conn:
        conn.execute(
            sql_update(models.household_keys)
            .where(models.household_keys.c.household_id == household_id)
            .values(canary=canary)
        )


def _heal_box_wrap(engine: Engine, household_id: str, dek: bytes) -> None:
    """Restored onto new hardware: the old box wrap can't be opened by the new
    master key. Once ANY household key unlocks the DEK, re-mint the wrap under
    the current master — convenient mode heals itself. Sealed households are
    left exactly as they are (no box wrap is the point)."""
    from sqlalchemy import update as sql_update

    from family_cfo_api import models

    master = _master_fernet()
    if master is None:
        return
    row = _box_wrap_row(engine, household_id)
    if row is None or row[0] is None:
        return  # sealed (or no key row): nothing to heal
    try:
        if master.decrypt(row[0].encode()) == dek:
            return  # wrap is current
    except InvalidToken:
        pass
    with engine.begin() as conn:
        conn.execute(
            sql_update(models.household_keys)
            .where(models.household_keys.c.household_id == household_id)
            .values(wrapped_dek=master.encrypt(dek).decode())
        )
    with _cache_lock:
        _dek_cache[household_id] = dek
    logger.info("box wrap re-minted under the current master key household=%s", household_id)


def unlock_household(engine: Engine, household_id: str, dek: bytes) -> bool:
    """Validate a DEK (device key-session or recovery flow) and open the
    keyring. False = the key is wrong; nothing is unlocked."""
    if not _canary_ok(engine, household_id, dek):
        return False
    _keyring_put(household_id, dek)
    _heal_box_wrap(engine, household_id, dek)
    return True


def unlock_with_recovery_key(engine: Engine, household_id: str, recovery_key: str) -> bool:
    """The recovery key's real job: unwrap the recovery wrap and open the
    keyring (healing a stale box wrap on the way). False = wrong key."""
    from family_cfo_api import models

    with engine.connect() as conn:
        row = conn.execute(
            select(models.household_key_wraps.c.wrap_json).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind == "recovery",
            )
        ).first()
    if row is None:
        return False
    dek = unwrap_with_password(row[0], recovery_key)
    if dek is None:
        return False
    return unlock_household(engine, household_id, dek)


def seal_household(engine: Engine, household_id: str) -> str | None:
    """Flip to sealed: drop the box wrap so the box can no longer open this
    household's content at rest. Returns an error string when preconditions
    fail (caller maps to 409), None on success. Requires the DEK NOW (the
    sealing session keeps working via the keyring)."""
    from sqlalchemy import update as sql_update

    from family_cfo_api import models

    master = _master_fernet()
    if master is None:
        return "Per-household encryption is not enabled on this box (no master key)."
    status = wrap_status(engine, household_id)
    if status["member_wraps"] < 1:
        return "Seal needs at least one member key — sign in with a password first."
    if not status["has_recovery_key"]:
        return "Seal needs a recovery key — create one first and store it safely."
    dek = _resolve_dek(engine, household_id, master)
    _write_canary(engine, household_id, dek)
    with engine.begin() as conn:
        conn.execute(
            sql_update(models.household_keys)
            .where(models.household_keys.c.household_id == household_id)
            .values(wrapped_dek=None)
        )
        conn.execute(
            sql_update(models.households)
            .where(models.households.c.id == household_id)
            .values(sealed_mode=True)
        )
    with _cache_lock:
        _dek_cache.pop(household_id, None)
    _keyring_put(household_id, dek)
    return None


def unseal_household(engine: Engine, household_id: str) -> str | None:
    """Flip back to convenient: re-mint the box wrap. Requires the household to
    be UNLOCKED right now (the DEK must be in the keyring to re-wrap)."""
    from sqlalchemy import update as sql_update

    from family_cfo_api import models

    master = _master_fernet()
    if master is None:
        return "Per-household encryption is not enabled on this box (no master key)."
    dek = _keyring_get(household_id)
    if dek is None:
        return "Unlock the household first (sign in), then switch modes."
    with engine.begin() as conn:
        conn.execute(
            sql_update(models.household_keys)
            .where(models.household_keys.c.household_id == household_id)
            .values(wrapped_dek=master.encrypt(dek).decode())
        )
        conn.execute(
            sql_update(models.households)
            .where(models.households.c.id == household_id)
            .values(sealed_mode=False)
        )
    with _cache_lock:
        _dek_cache[household_id] = dek
    return None


def delete_device_wrap(engine: Engine, household_id: str, device_id: str) -> None:
    """Revocation hygiene: a revoked device's wrap is unusable (its private key
    is what opens it), but leaving it makes the key-status count lie."""
    from sqlalchemy import delete as sql_delete

    from family_cfo_api import models

    with engine.begin() as conn:
        conn.execute(
            sql_delete(models.household_key_wraps).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind == "device",
                models.household_key_wraps.c.subject_id == device_id,
            )
        )


def device_wrap_json(engine: Engine, household_id: str, device_id: str) -> str | None:
    from family_cfo_api import models

    with engine.connect() as conn:
        row = conn.execute(
            select(models.household_key_wraps.c.wrap_json).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind == "device",
                models.household_key_wraps.c.subject_id == device_id,
            )
        ).first()
    return row[0] if row is not None else None


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


def _member_wrap_json(engine: Engine, household_id: str, user_id: str) -> str | None:
    from family_cfo_api import models

    with engine.connect() as conn:
        row = conn.execute(
            select(models.household_key_wraps.c.wrap_json).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind == "member",
                models.household_key_wraps.c.subject_id == user_id,
            )
        ).first()
    return row[0] if row is not None else None


def ensure_member_wrap(engine: Engine, household_id: str, user_id: str, password: str) -> None:
    """Create/refresh the member's password-derived wrap — and, for a sealed
    household, UNLOCK it: a proven password is a sealed household's front door
    (Phase 3). Called wherever a password is proven (login) or set (invite
    accept, member create). Never raises: key upkeep must not break a login."""
    try:
        try:
            dek = _dek_for_wrapping(engine, household_id)
        except HouseholdLockedError:
            # Sealed and locked: this member's own wrap is the way in.
            existing = _member_wrap_json(engine, household_id, user_id)
            dek = unwrap_with_password(existing, password) if existing else None
            if dek is None:
                logger.warning(
                    "sealed household locked and member has no usable wrap household=%s",
                    household_id,
                )
                return
            if not _canary_ok(engine, household_id, dek):
                logger.error("member wrap failed canary check household=%s", household_id)
                return
            _keyring_put(household_id, dek)
            _heal_box_wrap(engine, household_id, dek)
        if dek is None:
            return
        _upsert_wrap(engine, household_id, "member", user_id, _wrap_with_password(dek, password))
    except Exception:
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
    except Exception:
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


def on_password_established(
    engine: Engine, household_id: str, user_id: str, password: str
) -> None:
    """THE single seam every auth path funnels through when a plaintext password
    is proven or set (#196). Today it mints the member wrap; a future key
    responsibility tied to "the password just existed" belongs here too, so a new
    login/set-password path can't silently skip half of it. Delegates to
    ensure_member_wrap (which also unlocks a sealed household)."""
    ensure_member_wrap(engine, household_id, user_id, password)


def households_missing_member_wraps(engine: Engine) -> list[str]:
    """#196 consistency check: households with at least one member but ZERO
    member key wraps — they cannot be sealed until a member signs in (which
    mints the wrap). Empty when encryption is off (nothing to check)."""
    if not enabled():
        return []
    from sqlalchemy import func as _func

    from family_cfo_api import models

    with engine.connect() as conn:
        member_counts = {
            row[0]: row[1]
            for row in conn.execute(
                select(
                    models.household_memberships.c.household_id, _func.count()
                ).group_by(models.household_memberships.c.household_id)
            )
        }
        wrap_counts = {
            row[0]: row[1]
            for row in conn.execute(
                select(
                    models.household_key_wraps.c.household_id, _func.count()
                )
                .where(models.household_key_wraps.c.kind == "member")
                .group_by(models.household_key_wraps.c.household_id)
            )
        }
    return [
        household_id
        for household_id, members in member_counts.items()
        if members > 0 and wrap_counts.get(household_id, 0) == 0
    ]


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
    box_row = _box_wrap_row(engine, household_id)
    sealed = box_row is not None and box_row[0] is None
    return {
        "encryption_enabled": enabled(),
        "member_wraps": kinds.count("member"),
        "device_wraps": kinds.count("device"),
        "has_recovery_key": "recovery" in kinds,
        "recovery_key_created_at": recovery_at,
        "mode": "sealed" if sealed else "convenient",
        "unlocked": dek_available(engine, household_id),
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
        (
            models_module.transactions,
            ["merchant", "description", "note", "amount_minor"],
            "household_id",
        ),
        (models_module.accounts, ["name", "institution"], "household_id"),
        (models_module.bills, ["name"], "household_id"),
        (models_module.income_sources, ["name"], "household_id"),
        (models_module.goals, ["name"], "household_id"),
        (models_module.audit_events, ["summary", "undo_token"], "household_id"),
        (models_module.reports, ["explanation_text"], "household_id"),
        # #11: statement amounts are financial figures like any other.
        (
            models_module.card_statements,
            ["statement_balance_minor", "minimum_due_minor"],
            "household_id",
        ),
        # #25: statement line items carry merchant text and amounts.
        (
            models_module.card_statement_lines,
            ["description", "amount_minor"],
            "household_id",
        ),
    ]


def rotate_household_key(
    engine: Engine, household_id: str, actor_user_id: str | None = None
) -> bool:
    """Member removal (ADR 0072): new DEK, re-encrypt every sealed row, then
    fix the wraps — device wraps re-wrap from stored public keys; member wraps
    are DELETED (a password can't be re-derived server-side) and come back at
    each member's next login; the recovery wrap is deleted and the household is
    told to mint a new key (wrap_status shows has_recovery_key=false).

    #63: a successful rotation emits ``household.key_rotated`` — reserved in
    UNDO_POLICY since ADR 0023 and, until now, never emitted. The summary states
    that the recovery key was invalidated, because that consequence is otherwise
    visible only as a flag on a settings screen nobody was told to look at."""
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import update as sql_update

    from family_cfo_api import models

    master = _master_fernet()
    if master is None:
        return False
    old_dek = _resolve_dek(engine, household_id, master)
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

    box_row = _box_wrap_row(engine, household_id)
    sealed = box_row is not None and box_row[0] is None
    new_canary = _subkey_fernet(new_dek, b"rows").encrypt(CANARY_PLAINTEXT).decode()
    with engine.begin() as conn:
        conn.execute(
            sql_update(models.household_keys)
            .where(models.household_keys.c.household_id == household_id)
            .values(
                wrapped_dek=None if sealed else master.encrypt(new_dek).decode(),
                canary=new_canary,
            )
        )
        conn.execute(
            sql_delete(models.household_key_wraps).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind.in_(("member", "recovery")),
            )
        )
    if sealed:
        with _cache_lock:
            _dek_cache.pop(household_id, None)
        _keyring_put(household_id, new_dek)
    else:
        with _cache_lock:
            _dek_cache[household_id] = new_dek

    # Device wraps re-wrap from the stored public keys.
    from family_cfo_api import repository

    for device in repository.list_paired_devices(engine, household_id):
        if device.revoked_at is None and getattr(device, "public_key", None):
            ensure_device_wrap(engine, household_id, device.id, device.public_key)

    # Imported here (not at module scope): repository imports this module, so a
    # top-level `audit` import would close the cycle.
    from family_cfo_api import audit

    audit.write_audit(
        engine,
        household_id,
        actor_user_id,
        "household.key_rotated",
        "household",
        household_id,
        "Rotated the household encryption key and re-encrypted every sealed row — "
        "the recovery key was invalidated and must be generated again; each member "
        "regains access at their next sign-in",
    )
    return True
