from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from family_cfo_api import models


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- Auth -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: str
    email: str
    password_hash: str
    display_name: str


@dataclass(frozen=True, slots=True)
class SessionContext:
    user_id: str
    household_id: str
    role: str
    # ADR 0034: the resolved rights of the member's assigned role (falls back to
    # the legacy role's preset for un-backfilled rows). Permissions check THIS.
    # ADR 0065: box-level rights (system.admin, ai_runtime.manage) are injected
    # here when the USER is a system admin — and stripped otherwise, even if a
    # legacy household role still carries the string.
    rights: frozenset[str] = frozenset()
    role_name: str = ""
    is_system_admin: bool = False


def get_user_by_email(engine: Engine, email: str) -> UserRecord | None:
    with engine.connect() as conn:
        row = (
            conn.execute(select(models.users).where(models.users.c.email == email))
            .mappings()
            .first()
        )

    if row is None:
        return None

    return UserRecord(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        display_name=row["display_name"],
    )


def get_primary_household_id(engine: Engine, user_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(models.household_memberships.c.household_id)
            .where(models.household_memberships.c.user_id == user_id)
            .order_by(models.household_memberships.c.created_at)
        ).first()

    return row[0] if row else None


# Sentinel distinguishing "leave unchanged" from an explicit None/clear.
_UNSET: Any = object()


def get_membership_role(engine: Engine, household_id: str, user_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(models.household_memberships.c.role).where(
                models.household_memberships.c.household_id == household_id,
                models.household_memberships.c.user_id == user_id,
            )
        ).first()

    return row[0] if row else None


def create_auth_session(
    engine: Engine,
    user_id: str,
    household_id: str,
    token_hash: str,
    expires_at: datetime,
    device_id: str | None = None,
) -> str:
    session_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.auth_sessions).values(
                id=session_id,
                user_id=user_id,
                household_id=household_id,
                device_id=device_id,
                token_hash=token_hash,
                created_at=utcnow(),
                expires_at=expires_at,
                revoked_at=None,
            )
        )
    return session_id


def revoke_auth_session(engine: Engine, token_hash: str) -> bool:
    """Revoke the (unrevoked) session backing a token. Returns True if one was revoked."""
    with engine.begin() as conn:
        result = conn.execute(
            update(models.auth_sessions)
            .where(
                models.auth_sessions.c.token_hash == token_hash,
                models.auth_sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
    return result.rowcount > 0


def get_session_context(engine: Engine, token_hash: str) -> SessionContext | None:
    with engine.connect() as conn:
        session_row = (
            conn.execute(
                select(models.auth_sessions).where(models.auth_sessions.c.token_hash == token_hash)
            )
            .mappings()
            .first()
        )

        if session_row is None or session_row["revoked_at"] is not None:
            return None

        if _as_aware(session_row["expires_at"]) < utcnow():
            return None

        if session_row["device_id"] is not None:
            device_row = conn.execute(
                select(models.paired_devices.c.revoked_at).where(
                    models.paired_devices.c.id == session_row["device_id"],
                    models.paired_devices.c.household_id == session_row["household_id"],
                )
            ).first()
            if device_row is None or device_row[0] is not None:
                return None

        role_row = conn.execute(
            select(
                models.household_memberships.c.role,
                models.household_memberships.c.role_id,
            ).where(
                models.household_memberships.c.household_id == session_row["household_id"],
                models.household_memberships.c.user_id == session_row["user_id"],
            )
        ).mappings().first()

        assigned = None
        if role_row is not None and role_row["role_id"] is not None:
            assigned = conn.execute(
                select(models.roles.c.name, models.roles.c.rights_json).where(
                    models.roles.c.id == role_row["role_id"]
                )
            ).mappings().first()

    if role_row is None:
        return None

    from family_cfo_api import rights as rights_catalog

    if assigned is not None:
        member_rights = frozenset(assigned["rights_json"] or [])
        role_name = assigned["name"]
    else:
        member_rights = rights_catalog.rights_for_legacy_role(role_row["role"])
        role_name = rights_catalog.LEGACY_ROLE_TO_PRESET.get(role_row["role"], "")

    # ADR 0065: box-level rights come from the system-admin roster, never from
    # a household role (legacy roles may still carry the string — strip it).
    admin = is_system_admin(engine, session_row["user_id"])
    member_rights = member_rights - rights_catalog.BOX_RIGHTS
    if admin:
        member_rights = member_rights | rights_catalog.BOX_RIGHTS

    return SessionContext(
        user_id=session_row["user_id"],
        household_id=session_row["household_id"],
        role=role_row["role"],
        rights=member_rights,
        role_name=role_name,
        is_system_admin=admin,
    )


# --- Pairing and devices ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PairingSessionRecord:
    id: str
    household_id: str
    created_by_user_id: str
    qr_payload: str
    expires_at: datetime
    confirmed_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class PairedDeviceRecord:
    id: str
    household_id: str
    user_id: str
    name: str
    created_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeviceCredentialRecord:
    device_id: str
    access_token: str
    expires_at: datetime
    household_id: str = ""
    user_id: str = ""


def revoke_pending_pairing_sessions(engine: Engine, household_id: str, user_id: str) -> None:
    """Invalidate any still-pending (unconfirmed, unrevoked, whether or not
    expired) pairing session for this user — so minting a new QR leaves exactly
    one valid code per user, and an old code that leaked can never pair."""
    with engine.begin() as conn:
        conn.execute(
            update(models.pairing_sessions)
            .where(
                models.pairing_sessions.c.household_id == household_id,
                models.pairing_sessions.c.created_by_user_id == user_id,
                models.pairing_sessions.c.confirmed_at.is_(None),
                models.pairing_sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )


def create_pairing_session(
    engine: Engine,
    pairing_session_id: str,
    household_id: str,
    created_by_user_id: str,
    qr_payload: str,
    expires_at: datetime,
) -> PairingSessionRecord:
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.pairing_sessions).values(
                id=pairing_session_id,
                household_id=household_id,
                created_by_user_id=created_by_user_id,
                qr_payload=qr_payload,
                created_at=now,
                expires_at=expires_at,
                confirmed_at=None,
                revoked_at=None,
            )
        )

    return PairingSessionRecord(
        id=pairing_session_id,
        household_id=household_id,
        created_by_user_id=created_by_user_id,
        qr_payload=qr_payload,
        expires_at=expires_at,
        confirmed_at=None,
        revoked_at=None,
    )


def confirm_pairing_session(
    engine: Engine,
    pairing_session_id: str,
    device_name: str,
    device_public_key: str,
    access_token: str,
    token_hash: str,
    expires_at: datetime,
) -> DeviceCredentialRecord | None:
    now = utcnow()
    device_id = new_id()
    auth_session_id = new_id()

    with engine.begin() as conn:
        session = (
            conn.execute(
                select(models.pairing_sessions).where(
                    models.pairing_sessions.c.id == pairing_session_id
                )
            )
            .mappings()
            .first()
        )

        if session is None:
            return None
        if session["revoked_at"] is not None or session["confirmed_at"] is not None:
            return None
        if _as_aware(session["expires_at"]) < now:
            return None

        claimed = conn.execute(
            update(models.pairing_sessions)
            .where(
                models.pairing_sessions.c.id == pairing_session_id,
                models.pairing_sessions.c.confirmed_at.is_(None),
                models.pairing_sessions.c.revoked_at.is_(None),
                models.pairing_sessions.c.expires_at >= now,
            )
            .values(confirmed_at=now)
        )
        if claimed.rowcount == 0:
            return None

        conn.execute(
            insert(models.paired_devices).values(
                id=device_id,
                household_id=session["household_id"],
                user_id=session["created_by_user_id"],
                name=device_name,
                public_key=device_public_key,
                created_at=now,
                last_seen_at=None,
                revoked_at=None,
            )
        )
        conn.execute(
            insert(models.auth_sessions).values(
                id=auth_session_id,
                user_id=session["created_by_user_id"],
                household_id=session["household_id"],
                device_id=device_id,
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
                revoked_at=None,
            )
        )

    return DeviceCredentialRecord(
        device_id=device_id,
        access_token=access_token,
        expires_at=expires_at,
        household_id=session["household_id"],
        user_id=session["created_by_user_id"],
    )


def list_paired_devices(engine: Engine, household_id: str) -> list[PairedDeviceRecord]:
    query = (
        select(models.paired_devices)
        .where(models.paired_devices.c.household_id == household_id)
        .order_by(models.paired_devices.c.created_at.desc())
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        PairedDeviceRecord(
            id=row["id"],
            household_id=row["household_id"],
            user_id=row["user_id"],
            name=row["name"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
        )
        for row in rows
    ]


def revoke_paired_device(engine: Engine, household_id: str, device_id: str) -> bool:
    now = utcnow()
    with engine.begin() as conn:
        result = conn.execute(
            update(models.paired_devices)
            .where(
                models.paired_devices.c.id == device_id,
                models.paired_devices.c.household_id == household_id,
                models.paired_devices.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        if result.rowcount == 0:
            return False

        conn.execute(
            update(models.auth_sessions)
            .where(
                models.auth_sessions.c.device_id == device_id,
                models.auth_sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    return True


def create_paired_device_with_session(
    engine: Engine,
    household_id: str,
    user_id: str,
    device_name: str,
    device_public_key: str,
    access_token: str,
    token_hash: str,
    expires_at: datetime,
) -> DeviceCredentialRecord:
    """ADR 0056: credentialed pairing — the iOS login screen. Creates the same
    paired-device row + device-bound session that QR confirm creates, so a
    signed-in phone shows on the Devices page and revocation works identically."""
    now = utcnow()
    device_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.paired_devices).values(
                id=device_id,
                household_id=household_id,
                user_id=user_id,
                name=device_name,
                public_key=device_public_key,
                created_at=now,
                last_seen_at=None,
                revoked_at=None,
            )
        )
        conn.execute(
            insert(models.auth_sessions).values(
                id=new_id(),
                user_id=user_id,
                household_id=household_id,
                device_id=device_id,
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
                revoked_at=None,
            )
        )
    return DeviceCredentialRecord(
        device_id=device_id,
        access_token=access_token,
        expires_at=expires_at,
        household_id=household_id,
        user_id=user_id,
    )


# --- Household invites (ADR 0056) ---------------------------------------------


@dataclass(frozen=True, slots=True)
class InviteRecord:
    id: str
    household_id: str
    email: str
    role: str
    role_id: str | None
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_user_id: str | None
    revoked_at: datetime | None
    invited_by_display_name: str | None = None

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.accepted_at is not None:
            return "accepted"
        if _as_aware(self.expires_at) < utcnow():
            return "expired"
        return "pending"


def _invite_record(row, invited_by: str | None = None) -> InviteRecord:
    return InviteRecord(
        id=row["id"],
        household_id=row["household_id"],
        email=row["email"],
        role=row["role"],
        role_id=row["role_id"],
        created_at=_as_aware(row["created_at"]),
        expires_at=_as_aware(row["expires_at"]),
        accepted_at=_as_aware(row["accepted_at"]) if row["accepted_at"] else None,
        accepted_user_id=row["accepted_user_id"],
        revoked_at=_as_aware(row["revoked_at"]) if row["revoked_at"] else None,
        invited_by_display_name=invited_by,
    )


def create_invite(
    engine: Engine,
    household_id: str,
    email: str,
    role: str,
    role_id: str | None,
    token_hash: str,
    invited_by_user_id: str,
    expires_at: datetime,
) -> InviteRecord:
    """Revokes any prior pending invite for the same email first — one valid
    link per invitee, mirroring the pairing one-valid-QR rule."""
    now = utcnow()
    invite_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            update(models.household_invites)
            .where(
                models.household_invites.c.household_id == household_id,
                func.lower(models.household_invites.c.email) == email.lower(),
                models.household_invites.c.accepted_at.is_(None),
                models.household_invites.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        conn.execute(
            insert(models.household_invites).values(
                id=invite_id,
                household_id=household_id,
                email=email,
                role=role,
                role_id=role_id,
                token_hash=token_hash,
                invited_by_user_id=invited_by_user_id,
                created_at=now,
                expires_at=expires_at,
                accepted_at=None,
                accepted_user_id=None,
                revoked_at=None,
            )
        )
    return InviteRecord(
        id=invite_id, household_id=household_id, email=email, role=role,
        role_id=role_id, created_at=now, expires_at=expires_at,
        accepted_at=None, accepted_user_id=None, revoked_at=None,
    )


def list_invites(engine: Engine, household_id: str) -> list[InviteRecord]:
    query = (
        select(models.household_invites, models.users.c.display_name.label("invited_by"))
        .select_from(
            models.household_invites.join(
                models.users,
                models.household_invites.c.invited_by_user_id == models.users.c.id,
            )
        )
        .where(models.household_invites.c.household_id == household_id)
        .order_by(models.household_invites.c.created_at.desc())
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_invite_record(row, invited_by=row["invited_by"]) for row in rows]


def get_invite(engine: Engine, household_id: str, invite_id: str) -> InviteRecord | None:
    query = select(models.household_invites).where(
        models.household_invites.c.id == invite_id,
        models.household_invites.c.household_id == household_id,
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _invite_record(row) if row else None


def get_invite_by_token_hash(engine: Engine, token_hash: str) -> InviteRecord | None:
    query = select(models.household_invites).where(
        models.household_invites.c.token_hash == token_hash
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _invite_record(row) if row else None


def revoke_invite(engine: Engine, household_id: str, invite_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            update(models.household_invites)
            .where(
                models.household_invites.c.id == invite_id,
                models.household_invites.c.household_id == household_id,
                models.household_invites.c.accepted_at.is_(None),
                models.household_invites.c.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
    return result.rowcount > 0


def unrevoke_invite(engine: Engine, household_id: str, invite_id: str) -> bool:
    """Undo of a revocation: the token hash was never touched, so the original
    link works again (unless meanwhile expired)."""
    with engine.begin() as conn:
        result = conn.execute(
            update(models.household_invites)
            .where(
                models.household_invites.c.id == invite_id,
                models.household_invites.c.household_id == household_id,
                models.household_invites.c.accepted_at.is_(None),
                models.household_invites.c.revoked_at.is_not(None),
            )
            .values(revoked_at=None)
        )
    return result.rowcount > 0


def delete_invite(engine: Engine, household_id: str, invite_id: str) -> bool:
    """Undo of creation — removes the invite outright (kills the link)."""
    with engine.begin() as conn:
        result = conn.execute(
            models.household_invites.delete().where(
                models.household_invites.c.id == invite_id,
                models.household_invites.c.household_id == household_id,
                models.household_invites.c.accepted_at.is_(None),
            )
        )
    return result.rowcount > 0


def regenerate_invite_token(
    engine: Engine, household_id: str, invite_id: str, token_hash: str, expires_at: datetime
) -> bool:
    """A fresh secret for an unaccepted invite: replaces the hash, extends the
    expiry, and clears any revocation. The old link stops working."""
    with engine.begin() as conn:
        result = conn.execute(
            update(models.household_invites)
            .where(
                models.household_invites.c.id == invite_id,
                models.household_invites.c.household_id == household_id,
                models.household_invites.c.accepted_at.is_(None),
            )
            .values(token_hash=token_hash, expires_at=expires_at, revoked_at=None)
        )
    return result.rowcount > 0


def user_membership_count(engine: Engine, user_id: str) -> int:
    query = select(func.count()).where(
        models.household_memberships.c.user_id == user_id
    )
    with engine.connect() as conn:
        return int(conn.execute(query).scalar_one())


@dataclass(frozen=True, slots=True)
class InviteAcceptResult:
    outcome: str  # "ok" | "not_found" | "gone" | "conflict"
    user_id: str | None = None
    household_id: str | None = None
    role: str | None = None


def accept_invite(
    engine: Engine, token_hash: str, password_hash: str, display_name: str
) -> InviteAcceptResult:
    """Claim the invite and create (or revive) the member, atomically.

    - Unknown token -> not_found; revoked/expired/already-accepted (or losing a
      concurrent double-accept race) -> gone.
    - Email owning an account WITH any membership -> conflict: never overwrite a
      live account's password.
    - Email owning an account with NO membership (a previously-removed member —
      the users row survives removal) -> revive it: new password + display name,
      fresh membership. The old password granted nothing (ADR 0056)."""
    now = utcnow()
    with engine.begin() as conn:
        invite = (
            conn.execute(
                select(models.household_invites).where(
                    models.household_invites.c.token_hash == token_hash
                )
            )
            .mappings()
            .first()
        )
        if invite is None:
            return InviteAcceptResult(outcome="not_found")
        if (
            invite["revoked_at"] is not None
            or invite["accepted_at"] is not None
            or _as_aware(invite["expires_at"]) < now
        ):
            return InviteAcceptResult(outcome="gone")

        existing = (
            conn.execute(
                select(models.users).where(
                    func.lower(models.users.c.email) == invite["email"].lower()
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            memberships = conn.execute(
                select(func.count()).where(
                    models.household_memberships.c.user_id == existing["id"]
                )
            ).scalar_one()
            if memberships > 0:
                return InviteAcceptResult(outcome="conflict")

        user_id = existing["id"] if existing is not None else new_id()

        # Claim FIRST with the timestamp only: the user row may not exist yet,
        # and Postgres enforces the accepted_user_id FK immediately (SQLite in
        # tests does not — this ordering is load-bearing).
        claimed = conn.execute(
            update(models.household_invites)
            .where(
                models.household_invites.c.id == invite["id"],
                models.household_invites.c.accepted_at.is_(None),
                models.household_invites.c.revoked_at.is_(None),
                models.household_invites.c.expires_at >= now,
            )
            .values(accepted_at=now)
        )
        if claimed.rowcount == 0:
            return InviteAcceptResult(outcome="gone")

        if existing is not None:
            conn.execute(
                update(models.users)
                .where(models.users.c.id == user_id)
                .values(password_hash=password_hash, display_name=display_name, updated_at=now)
            )
        else:
            conn.execute(
                insert(models.users).values(
                    id=user_id,
                    email=invite["email"],
                    password_hash=password_hash,
                    display_name=display_name,
                    created_at=now,
                    updated_at=now,
                )
            )
        conn.execute(
            insert(models.household_memberships).values(
                id=new_id(),
                household_id=invite["household_id"],
                user_id=user_id,
                role=invite["role"],
                role_id=invite["role_id"],
                created_at=now,
            )
        )
        # Now that the user row exists, stamp who accepted (FK satisfied).
        conn.execute(
            update(models.household_invites)
            .where(models.household_invites.c.id == invite["id"])
            .values(accepted_user_id=user_id)
        )
    return InviteAcceptResult(
        outcome="ok",
        user_id=user_id,
        household_id=invite["household_id"],
        role=invite["role"],
    )


# --- Household and accounts ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class HouseholdRecord:
    id: str
    display_name: str
    base_currency: str
    # M43: null means "use the default target".
    emergency_fund_target_months: float | None = None
    # M61: null = defaults (married_joint; deposits treated as take-home pay).
    tax_filing_status: str | None = None
    income_treated_as_net: bool | None = None
    # M65: USPS state code for state income tax (null = not set).
    state: str | None = None
    # M96: pays credit cards in full monthly → full balances count as committed.
    credit_cards_paid_in_full: bool = False
    # M98: off-box backup destination (mounted share) + cadence.
    backup_destination_path: str | None = None
    backup_frequency: str = "daily"
    # M98: Synology SMB target (the app uploads over SMB). Password encrypted.
    backup_smb_host: str | None = None
    backup_smb_share: str | None = None
    backup_smb_folder: str | None = None
    backup_smb_username: str | None = None
    backup_smb_password_encrypted: str | None = None
    backup_smb_domain: str | None = None
    backup_max_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class AccountBalanceRecord:
    account_id: str
    name: str
    account_type: str
    currency: str
    balance_minor: int
    annual_interest_rate: float | None = None
    minimum_payment_minor: int | None = None
    maturity_date: date | None = None
    next_payment_due_date: date | None = None
    emergency_fund_percent: float | None = None
    emergency_fund_minor: int | None = None


def get_household(engine: Engine, household_id: str) -> HouseholdRecord | None:
    with engine.connect() as conn:
        row = (
            conn.execute(select(models.households).where(models.households.c.id == household_id))
            .mappings()
            .first()
        )

    if row is None:
        return None

    return HouseholdRecord(
        id=row["id"],
        display_name=row["display_name"],
        base_currency=row["base_currency"],
        emergency_fund_target_months=row["emergency_fund_target_months"],
        tax_filing_status=row["tax_filing_status"],
        income_treated_as_net=row["income_treated_as_net"],
        state=row["state"],
        credit_cards_paid_in_full=bool(row["credit_cards_paid_in_full"]),
        backup_destination_path=row["backup_destination_path"],
        backup_frequency=row["backup_frequency"] or "daily",
        backup_smb_host=row["backup_smb_host"],
        backup_smb_share=row["backup_smb_share"],
        backup_smb_folder=row["backup_smb_folder"],
        backup_smb_username=row["backup_smb_username"],
        backup_smb_password_encrypted=row["backup_smb_password_encrypted"],
        backup_smb_domain=row["backup_smb_domain"],
        backup_max_bytes=row["backup_max_bytes"],
    )


def set_credit_cards_paid_in_full(engine: Engine, household_id: str, value: bool) -> None:
    """M96: whether the household pays its credit cards in full each month."""
    with engine.begin() as conn:
        conn.execute(
            update(models.households)
            .where(models.households.c.id == household_id)
            .values(credit_cards_paid_in_full=value, updated_at=utcnow())
        )


BACKUP_FREQUENCIES = ("every_15min", "hourly", "every_6h", "daily", "weekly", "off")


def set_backup_config(
    engine: Engine,
    household_id: str,
    *,
    frequency: str,
    smb_host: str | None,
    smb_share: str | None,
    smb_folder: str | None,
    smb_username: str | None,
    smb_password_encrypted: str | None,
    smb_domain: str | None,
    update_password: bool,
    max_bytes: int | None,
) -> None:
    """M98: the Synology SMB target off-box backups upload to, and the cadence. The
    password is only written when `update_password` is set, so leaving the field
    blank in the UI keeps the stored one."""
    values: dict[str, Any] = {
        "backup_frequency": frequency,
        "backup_smb_host": smb_host or None,
        "backup_smb_share": smb_share or None,
        "backup_smb_folder": smb_folder or None,
        "backup_smb_username": smb_username or None,
        "backup_smb_domain": smb_domain or None,
        "backup_max_bytes": max_bytes,
        "updated_at": utcnow(),
    }
    if update_password:
        values["backup_smb_password_encrypted"] = smb_password_encrypted
    with engine.begin() as conn:
        conn.execute(
            update(models.households)
            .where(models.households.c.id == household_id)
            .values(**values)
        )


def update_emergency_fund_target(
    engine: Engine, household_id: str, target_months: float | None
) -> None:
    """M43: set (or clear, when None) the household's emergency-fund target."""
    with engine.begin() as conn:
        conn.execute(
            update(models.households)
            .where(models.households.c.id == household_id)
            .values(emergency_fund_target_months=target_months, updated_at=utcnow())
        )


def update_tax_settings(
    engine: Engine,
    household_id: str,
    *,
    tax_filing_status: str | None,
    income_treated_as_net: bool | None,
    state: str | None = None,
) -> None:
    """M61/M65: set (or clear, when None) the household's tax-estimate settings."""
    with engine.begin() as conn:
        conn.execute(
            update(models.households)
            .where(models.households.c.id == household_id)
            .values(
                tax_filing_status=tax_filing_status,
                income_treated_as_net=income_treated_as_net,
                state=state,
                updated_at=utcnow(),
            )
        )


def upsert_overview_snapshot(
    engine: Engine, household_id: str, month: str, snapshot_json: str
) -> None:
    """M96: store (or refresh) a month's full Overview snapshot. The current month
    is overwritten as it changes; once the month passes it is never rewritten, so
    it freezes at its final captured state."""
    now = utcnow()
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.overview_snapshots.c.id).where(
                models.overview_snapshots.c.household_id == household_id,
                models.overview_snapshots.c.month == month,
            )
        ).first()
        if existing is None:
            conn.execute(
                insert(models.overview_snapshots).values(
                    id=new_id(),
                    household_id=household_id,
                    month=month,
                    snapshot=snapshot_json,
                    captured_at=now,
                )
            )
        else:
            conn.execute(
                update(models.overview_snapshots)
                .where(models.overview_snapshots.c.id == existing[0])
                .values(snapshot=snapshot_json, captured_at=now)
            )


def get_overview_snapshot(engine: Engine, household_id: str, month: str) -> str | None:
    """The stored Overview JSON for a month, or None if none was captured."""
    with engine.connect() as conn:
        row = conn.execute(
            select(models.overview_snapshots.c.snapshot).where(
                models.overview_snapshots.c.household_id == household_id,
                models.overview_snapshots.c.month == month,
            )
        ).first()
    return row[0] if row is not None else None


def account_name_map(engine: Engine, household_id: str) -> dict[str, str]:
    """Every account's id → display name (including accounts with no balance yet)."""
    query = select(models.accounts.c.id, models.accounts.c.name).where(
        models.accounts.c.household_id == household_id
    )
    with engine.connect() as conn:
        return {row.id: row.name for row in conn.execute(query)}


def account_type_map(engine: Engine, household_id: str) -> dict[str, str]:
    """Every account's id → its type (checking, savings, auto_loan, …)."""
    query = select(models.accounts.c.id, models.accounts.c.type).where(
        models.accounts.c.household_id == household_id
    )
    with engine.connect() as conn:
        return {row.id: row.type for row in conn.execute(query)}


def account_institution_map(engine: Engine, household_id: str) -> dict[str, str]:
    """Account id → the institution (bank) behind it, when known — so the UI can
    say where to look a transaction up. Absent for manual accounts and for synced
    ones not yet backfilled from the provider's org."""
    query = select(models.accounts.c.id, models.accounts.c.institution).where(
        models.accounts.c.household_id == household_id,
        models.accounts.c.institution.is_not(None),
    )
    with engine.connect() as conn:
        return {row.id: row.institution for row in conn.execute(query)}


def list_account_balances(engine: Engine, household_id: str) -> list[AccountBalanceRecord]:
    latest_balance = (
        select(
            models.account_balances.c.account_id,
            func.max(models.account_balances.c.as_of).label("max_as_of"),
        )
        .group_by(models.account_balances.c.account_id)
        .subquery()
    )

    query = (
        select(
            models.accounts.c.id,
            models.accounts.c.name,
            models.accounts.c.type,
            models.accounts.c.currency,
            models.account_balances.c.balance_minor,
            models.accounts.c.annual_interest_rate,
            models.accounts.c.minimum_payment_minor,
            models.accounts.c.maturity_date,
            models.accounts.c.next_payment_due_date,
            models.accounts.c.emergency_fund_percent,
            models.accounts.c.emergency_fund_minor,
        )
        .select_from(models.accounts)
        .join(latest_balance, latest_balance.c.account_id == models.accounts.c.id)
        .join(
            models.account_balances,
            (models.account_balances.c.account_id == latest_balance.c.account_id)
            & (models.account_balances.c.as_of == latest_balance.c.max_as_of),
        )
        .where(models.accounts.c.household_id == household_id)
        .order_by(models.accounts.c.name)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).all()

    return [
        AccountBalanceRecord(
            account_id=row.id,
            name=row.name,
            account_type=row.type,
            currency=row.currency,
            balance_minor=row.balance_minor,
            annual_interest_rate=row.annual_interest_rate,
            minimum_payment_minor=row.minimum_payment_minor,
            maturity_date=row.maturity_date,
            next_payment_due_date=row.next_payment_due_date,
            emergency_fund_percent=row.emergency_fund_percent,
            emergency_fund_minor=row.emergency_fund_minor,
        )
        for row in rows
    ]


# --- Transactions, bills, income ---------------------------------------------


@dataclass(frozen=True, slots=True)
class TransactionRecord:
    id: str
    account_id: str
    occurred_at: date
    amount_minor: int
    currency: str
    merchant: str | None
    category: str | None
    description: str | None
    category_id: str | None = None
    # M97: NULL normally; 'flagged'/'dismissed'/'disputed' for the Review queue.
    duplicate_state: str | None = None
    # M97: the bank/aggregator's own reference for this record — the one thing that
    # differs between two otherwise-identical duplicate legs.
    external_id: str | None = None
    # M100: user note + optional attached image (path on disk, content type).
    note: str | None = None
    attachment_path: str | None = None
    attachment_content_type: str | None = None


@dataclass(frozen=True, slots=True)
class RecurringRecord:
    id: str
    name: str
    amount_minor: int
    currency: str
    frequency: str
    # Bills carry a due date; income sources leave it None.
    next_due_date: date | None = None
    # M96: a bill may be filed under a spending category (e.g. Subscriptions);
    # income sources leave it None.
    category_id: str | None = None


def list_transactions(
    engine: Engine,
    household_id: str,
    limit: int = 200,
    *,
    start: date | None = None,
    end: date | None = None,
    duplicate_states: tuple[str, ...] | None = None,
) -> list[TransactionRecord]:
    conditions = [models.transactions.c.household_id == household_id]
    if start is not None:
        conditions.append(models.transactions.c.occurred_at >= start)
    if end is not None:
        conditions.append(models.transactions.c.occurred_at <= end)
    if duplicate_states is not None:
        conditions.append(models.transactions.c.duplicate_state.in_(duplicate_states))
    query = (
        select(
            models.transactions.c.id,
            models.transactions.c.account_id,
            models.transactions.c.occurred_at,
            models.transactions.c.amount_minor,
            models.transactions.c.currency,
            models.transactions.c.merchant,
            models.transaction_categories.c.name.label("category"),
            models.transactions.c.category_id,
            models.transactions.c.description,
            models.transactions.c.duplicate_state,
            models.transactions.c.external_id,
            models.transactions.c.note,
            models.transactions.c.attachment_path,
            models.transactions.c.attachment_content_type,
        )
        .select_from(models.transactions)
        .join(
            models.transaction_categories,
            models.transaction_categories.c.id == models.transactions.c.category_id,
            isouter=True,
        )
        .where(*conditions)
        .order_by(models.transactions.c.occurred_at.desc())
        .limit(limit)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).all()

    return [
        TransactionRecord(
            id=row.id,
            account_id=row.account_id,
            occurred_at=row.occurred_at,
            amount_minor=row.amount_minor,
            currency=row.currency,
            merchant=row.merchant,
            category=row.category,
            category_id=row.category_id,
            description=row.description,
            duplicate_state=row.duplicate_state,
            external_id=row.external_id,
            note=row.note,
            attachment_path=row.attachment_path,
            attachment_content_type=row.attachment_content_type,
        )
        for row in rows
    ]


# --- Categories (M45) --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CategoryRecord:
    id: str
    name: str


def list_categories(engine: Engine, household_id: str) -> list[CategoryRecord]:
    query = (
        select(models.transaction_categories.c.id, models.transaction_categories.c.name)
        .where(models.transaction_categories.c.household_id == household_id)
        .order_by(models.transaction_categories.c.name)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return [CategoryRecord(id=row.id, name=row.name) for row in rows]


def get_category(engine: Engine, household_id: str, category_id: str) -> CategoryRecord | None:
    query = select(models.transaction_categories.c.id, models.transaction_categories.c.name).where(
        models.transaction_categories.c.household_id == household_id,
        models.transaction_categories.c.id == category_id,
    )
    with engine.connect() as conn:
        row = conn.execute(query).first()
    return CategoryRecord(id=row.id, name=row.name) if row is not None else None


def category_name_exists(engine: Engine, household_id: str, name: str) -> bool:
    query = select(models.transaction_categories.c.id).where(
        models.transaction_categories.c.household_id == household_id,
        models.transaction_categories.c.name == name,
    )
    with engine.connect() as conn:
        return conn.execute(query).first() is not None


def update_category(engine: Engine, household_id: str, category_id: str, name: str) -> bool:
    """Rename a category. Returns False when it doesn't belong to the household."""
    with engine.begin() as conn:
        result = conn.execute(
            models.transaction_categories.update()
            .where(
                models.transaction_categories.c.household_id == household_id,
                models.transaction_categories.c.id == category_id,
            )
            .values(name=name)
        )
    return result.rowcount > 0


def create_category(engine: Engine, household_id: str, name: str) -> CategoryRecord:
    category_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.transaction_categories).values(
                id=category_id,
                household_id=household_id,
                name=name,
                parent_category_id=None,
                created_at=utcnow(),
            )
        )
    return CategoryRecord(id=category_id, name=name)


def delete_category(engine: Engine, household_id: str, category_id: str) -> bool:
    """Delete a category, first nulling it on any transactions that reference it.

    Any budget envelope for the category (M46) is deleted with it.
    """
    with engine.begin() as conn:
        exists = conn.execute(
            select(models.transaction_categories.c.id).where(
                models.transaction_categories.c.household_id == household_id,
                models.transaction_categories.c.id == category_id,
            )
        ).first()
        if exists is None:
            return False
        conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.category_id == category_id,
            )
            .values(category_id=None)
        )
        conn.execute(
            delete(models.budgets).where(
                models.budgets.c.household_id == household_id,
                models.budgets.c.category_id == category_id,
            )
        )
        conn.execute(
            delete(models.transaction_categories).where(
                models.transaction_categories.c.id == category_id
            )
        )
    return True


# --- Budgets (M46) -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetRecord:
    id: str
    category_id: str
    category_name: str
    limit_minor: int
    currency: str


def list_budgets(engine: Engine, household_id: str) -> list[BudgetRecord]:
    query = (
        select(
            models.budgets.c.id,
            models.budgets.c.category_id,
            models.transaction_categories.c.name.label("category_name"),
            models.budgets.c.limit_minor,
            models.budgets.c.currency,
        )
        .select_from(models.budgets)
        .join(
            models.transaction_categories,
            models.transaction_categories.c.id == models.budgets.c.category_id,
        )
        .where(models.budgets.c.household_id == household_id)
        .order_by(models.transaction_categories.c.name)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return [
        BudgetRecord(
            id=row.id,
            category_id=row.category_id,
            category_name=row.category_name,
            limit_minor=row.limit_minor,
            currency=row.currency,
        )
        for row in rows
    ]


def get_budget(engine: Engine, household_id: str, budget_id: str) -> BudgetRecord | None:
    for budget in list_budgets(engine, household_id):
        if budget.id == budget_id:
            return budget
    return None


def budget_exists_for_category(engine: Engine, household_id: str, category_id: str) -> bool:
    query = select(models.budgets.c.id).where(
        models.budgets.c.household_id == household_id,
        models.budgets.c.category_id == category_id,
    )
    with engine.connect() as conn:
        return conn.execute(query).first() is not None


def create_budget(
    engine: Engine, household_id: str, category_id: str, limit_minor: int, currency: str
) -> str:
    budget_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.budgets).values(
                id=budget_id,
                household_id=household_id,
                category_id=category_id,
                limit_minor=limit_minor,
                currency=currency,
                created_at=now,
                updated_at=now,
            )
        )
    return budget_id


def update_budget_limit(
    engine: Engine, household_id: str, budget_id: str, limit_minor: int
) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            update(models.budgets)
            .where(
                models.budgets.c.household_id == household_id,
                models.budgets.c.id == budget_id,
            )
            .values(limit_minor=limit_minor, updated_at=utcnow())
        )
    return result.rowcount > 0


def delete_budget(engine: Engine, household_id: str, budget_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.budgets).where(
                models.budgets.c.household_id == household_id,
                models.budgets.c.id == budget_id,
            )
        )
    return result.rowcount > 0


def sum_spending_by_category(
    engine: Engine, household_id: str, start: date, end: date, currency: str
) -> dict[str, int]:
    """Outflow (positive) per category id over [start, end]; uncategorized excluded."""
    total = func.sum(-models.transactions.c.amount_minor).label("total")
    query = (
        select(models.transactions.c.category_id, total)
        .where(
            _spending_window(household_id, start, end, currency),
            models.transactions.c.category_id.is_not(None),
        )
        .group_by(models.transactions.c.category_id)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return {row.category_id: int(row.total) for row in rows}


# --- Spending insights (M42) -------------------------------------------------


# Categories (by name, case-insensitive) whose transactions are money moving
# between the household's own accounts — credit-card payments, bank-to-bank
# transfers — not consumption. Excluded from spending so a transfer settled from
# checking isn't double-counted against the expense it paid off.
TRANSFER_CATEGORY_NAMES = ("transfers", "transfer")


# The category (by name, case-insensitive) that marks an inflow as earnings.
INCOME_CATEGORY_NAMES = ("income",)

# Tax withholding (e.g. RSU sell-to-cover) — a non-discretionary outflow tracked
# on its own, kept out of the discretionary spending breakdown.
TAXES_CATEGORY_NAMES = ("taxes",)

# Categories that are not discretionary consumption and must never appear in a
# spending total or breakdown: money moving between the household's own accounts,
# earnings, and tax withholding.
NON_SPENDING_CATEGORY_NAMES = (
    TRANSFER_CATEGORY_NAMES + INCOME_CATEGORY_NAMES + TAXES_CATEGORY_NAMES
)


def sum_taxes(
    engine: Engine, household_id: str, start: date, end: date, currency: str
) -> int:
    """Total outflow (positive) filed under the Taxes category over [start, end]."""
    tax_ids = select(models.transaction_categories.c.id).where(
        (models.transaction_categories.c.household_id == household_id)
        & (func.lower(models.transaction_categories.c.name).in_(TAXES_CATEGORY_NAMES))
    )
    query = select(func.coalesce(func.sum(-models.transactions.c.amount_minor), 0)).where(
        (models.transactions.c.household_id == household_id)
        & (models.transactions.c.currency == currency)
        & (models.transactions.c.amount_minor < 0)
        & (models.transactions.c.occurred_at >= start)
        & (models.transactions.c.occurred_at <= end)
        & (models.transactions.c.category_id.in_(tax_ids))
    )
    with engine.connect() as conn:
        return int(conn.execute(query).scalar_one())


def _non_spending_category_ids(household_id: str):
    return select(models.transaction_categories.c.id).where(
        (models.transaction_categories.c.household_id == household_id)
        & (func.lower(models.transaction_categories.c.name).in_(NON_SPENDING_CATEGORY_NAMES))
    )


def sum_income(
    engine: Engine, household_id: str, start: date, end: date, currency: str
) -> int:
    """Total inflow (positive) filed under the Income category over [start, end]."""
    income_ids = select(models.transaction_categories.c.id).where(
        (models.transaction_categories.c.household_id == household_id)
        & (func.lower(models.transaction_categories.c.name).in_(INCOME_CATEGORY_NAMES))
    )
    query = select(func.coalesce(func.sum(models.transactions.c.amount_minor), 0)).where(
        (models.transactions.c.household_id == household_id)
        & (models.transactions.c.currency == currency)
        & (models.transactions.c.amount_minor > 0)
        & (models.transactions.c.occurred_at >= start)
        & (models.transactions.c.occurred_at <= end)
        & (models.transactions.c.category_id.in_(income_ids))
    )
    with engine.connect() as conn:
        return int(conn.execute(query).scalar_one())


def _spending_window(household_id: str, start: date, end: date, currency: str):
    """A predicate selecting spending in [start, end], base currency, summed as
    -amount so outflows add and refunds subtract.

    - Non-spending categories (Transfers, Income, Taxes) are excluded entirely.
    - Every outflow counts (categorized or not).
    - A categorized INFLOW is a refund/credit for that category, so it nets against
      its spending (a Lululemon return filed under Shopping cancels the purchase).
    - An UNcategorized inflow is a stray deposit, not a refund, so it is excluded.
    """
    return (
        (models.transactions.c.household_id == household_id)
        & (models.transactions.c.currency == currency)
        & (models.transactions.c.occurred_at >= start)
        & (models.transactions.c.occurred_at <= end)
        # NULL-safe: uncategorized outflows still count as spending.
        & (
            models.transactions.c.category_id.is_(None)
            | models.transactions.c.category_id.not_in(_non_spending_category_ids(household_id))
        )
        # Outflows always; categorized inflows (refunds) net; uncategorized inflows out.
        & (
            (models.transactions.c.amount_minor < 0)
            | models.transactions.c.category_id.is_not(None)
        )
    )


def sum_spending(
    engine: Engine, household_id: str, start: date, end: date, currency: str
) -> int:
    """Total outflow (positive) over [start, end]; income is excluded."""
    query = select(func.coalesce(func.sum(-models.transactions.c.amount_minor), 0)).where(
        _spending_window(household_id, start, end, currency)
    )
    with engine.connect() as conn:
        return int(conn.execute(query).scalar_one())


@dataclass(frozen=True, slots=True)
class MerchantSpend:
    merchant: str
    amount_minor: int


def top_spending_merchants(
    engine: Engine, household_id: str, start: date, end: date, currency: str, limit: int = 5
) -> list[MerchantSpend]:
    """Merchants ranked by outflow over [start, end]; NULL merchant folds into 'Other'."""
    merchant = func.coalesce(models.transactions.c.merchant, "Other").label("merchant")
    total = func.sum(-models.transactions.c.amount_minor).label("total")
    query = (
        select(merchant, total)
        .where(_spending_window(household_id, start, end, currency))
        .group_by(merchant)
        .order_by(total.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return [MerchantSpend(merchant=row.merchant, amount_minor=int(row.total)) for row in rows]


def list_bills(engine: Engine, household_id: str) -> list[RecurringRecord]:
    query = (
        select(models.bills)
        .where(models.bills.c.household_id == household_id)
        .order_by(models.bills.c.name)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        RecurringRecord(
            id=row["id"],
            name=row["name"],
            amount_minor=row["amount_minor"],
            currency=row["currency"],
            frequency=row["frequency"],
            next_due_date=row["next_due_date"],
            category_id=row["category_id"],
        )
        for row in rows
    ]


def list_income_sources(engine: Engine, household_id: str) -> list[RecurringRecord]:
    query = (
        select(models.income_sources)
        .where(models.income_sources.c.household_id == household_id)
        .order_by(models.income_sources.c.name)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        RecurringRecord(
            id=row["id"],
            name=row["name"],
            amount_minor=row["amount_minor"],
            currency=row["currency"],
            frequency=row["frequency"],
        )
        for row in rows
    ]


# --- Goals ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoalRecord:
    id: str
    name: str
    goal_type: str
    target_minor: int
    current_minor: int
    currency: str
    target_date: date | None
    priority: int
    # M118: planned monthly contribution (None = no plan declared).
    monthly_contribution_minor: int | None = None


def list_goals(engine: Engine, household_id: str) -> list[GoalRecord]:
    query = (
        select(models.goals)
        .where(models.goals.c.household_id == household_id)
        .order_by(models.goals.c.priority, models.goals.c.name)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [_goal_record_from_row(row) for row in rows]


def create_goal(
    engine: Engine,
    household_id: str,
    name: str,
    goal_type: str,
    target_minor: int,
    currency: str,
    target_date: date | None,
    priority: int,
    monthly_contribution_minor: int | None = None,
    current_minor: int = 0,
) -> GoalRecord:
    goal_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.goals).values(
                id=goal_id,
                household_id=household_id,
                name=name,
                type=goal_type,
                target_minor=target_minor,
                current_minor=current_minor,
                currency=currency,
                target_date=target_date,
                priority=priority,
                monthly_contribution_minor=monthly_contribution_minor,
                created_at=now,
                updated_at=now,
            )
        )
        row = (
            conn.execute(select(models.goals).where(models.goals.c.id == goal_id))
            .mappings()
            .first()
        )

    assert row is not None
    return _goal_record_from_row(row)


def _goal_record_from_row(row: Any) -> GoalRecord:
    return GoalRecord(
        id=row["id"],
        name=row["name"],
        goal_type=row["type"],
        target_minor=row["target_minor"],
        current_minor=row["current_minor"],
        currency=row["currency"],
        target_date=row["target_date"],
        priority=row["priority"],
        monthly_contribution_minor=row["monthly_contribution_minor"],
    )


def delete_goal(engine: Engine, household_id: str, goal_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.goals).where(
                models.goals.c.household_id == household_id, models.goals.c.id == goal_id
            )
        )
    return result.rowcount > 0


def get_goal(engine: Engine, household_id: str, goal_id: str) -> GoalRecord | None:
    query = select(models.goals).where(
        models.goals.c.household_id == household_id, models.goals.c.id == goal_id
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _goal_record_from_row(row) if row is not None else None


def update_goal(
    engine: Engine,
    household_id: str,
    goal_id: str,
    *,
    name: str | None = None,
    target_minor: int | None = None,
    target_date: date | None = _UNSET,  # type: ignore[assignment]
    priority: int | None = None,
    monthly_contribution_minor: int | None = _UNSET,  # type: ignore[assignment]
) -> bool:
    """M118: update a goal's declared fields. `_UNSET` distinguishes "leave
    unchanged" from an explicit clear (None)."""
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if target_minor is not None:
        values["target_minor"] = target_minor
    if target_date is not _UNSET:
        values["target_date"] = target_date
    if priority is not None:
        values["priority"] = priority
    if monthly_contribution_minor is not _UNSET:
        values["monthly_contribution_minor"] = monthly_contribution_minor
    if not values:
        return get_goal(engine, household_id, goal_id) is not None
    values["updated_at"] = utcnow()
    with engine.begin() as conn:
        result = conn.execute(
            update(models.goals)
            .where(models.goals.c.household_id == household_id, models.goals.c.id == goal_id)
            .values(**values)
        )
    return result.rowcount > 0


# --- Financial calculation audit ------------------------------------------------


def record_calculation(
    engine: Engine,
    household_id: str,
    calculation_type: str,
    version: str,
    inputs: dict[str, Any],
    assumptions: list[str],
    warnings: list[str],
    outputs: dict[str, Any],
) -> str:
    calculation_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.financial_calculations).values(
                id=calculation_id,
                household_id=household_id,
                calculation_type=calculation_type,
                version=version,
                inputs_json=inputs,
                assumptions_json=assumptions,
                warnings_json=warnings,
                outputs_json=outputs,
                created_at=utcnow(),
            )
        )
    return calculation_id


# --- Scenarios and recommendations ---------------------------------------------


def create_scenario(
    engine: Engine,
    household_id: str,
    created_by_user_id: str,
    name: str,
    description: str | None,
    input_json: dict[str, Any],
) -> str:
    scenario_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.scenarios).values(
                id=scenario_id,
                household_id=household_id,
                created_by_user_id=created_by_user_id,
                name=name,
                description=description,
                input_json=input_json,
                created_at=utcnow(),
            )
        )
    return scenario_id


def create_recommendation(
    engine: Engine,
    household_id: str,
    scenario_id: str | None,
    answer: str,
    assumptions: list[str],
    impacts: list[dict[str, Any]],
    tradeoffs: list[str],
    alternatives: list[str],
    confidence: float,
    calculation_refs: list[str],
    warnings: list[str],
    explanation_source: str,
    model_version: str | None = None,
    prompt_version: str | None = None,
) -> str:
    recommendation_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.recommendations).values(
                id=recommendation_id,
                household_id=household_id,
                scenario_id=scenario_id,
                answer=answer,
                assumptions_json=assumptions,
                impacts_json=impacts,
                tradeoffs_json=tradeoffs,
                alternatives_json=alternatives,
                confidence=confidence,
                calculation_refs_json=calculation_refs,
                warnings_json=warnings,
                explanation_source=explanation_source,
                model_version=model_version,
                prompt_version=prompt_version,
                created_at=utcnow(),
            )
        )
    return recommendation_id


# --- AI runtime configuration ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class AiRuntimeConfigRecord:
    household_id: str
    provider: str
    base_url: str
    model: str
    enabled: bool


def get_ai_runtime_config(engine: Engine, household_id: str) -> AiRuntimeConfigRecord | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(models.ai_runtime_configs).where(
                    models.ai_runtime_configs.c.household_id == household_id
                )
            )
            .mappings()
            .first()
        )

    if row is None:
        return None

    return AiRuntimeConfigRecord(
        household_id=row["household_id"],
        provider=row["provider"],
        base_url=row["base_url"],
        model=row["model"],
        enabled=bool(row["enabled"]),
    )


@dataclass(frozen=True, slots=True)
class SystemAdminRecord:
    user_id: str
    email: str
    display_name: str
    granted_at: datetime
    granted_by_user_id: str | None


def is_system_admin(engine: Engine, user_id: str) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(
                select(models.system_admins.c.id).where(
                    models.system_admins.c.user_id == user_id
                )
            ).first()
            is not None
        )


def list_system_admins(engine: Engine) -> list[SystemAdminRecord]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                models.system_admins.c.user_id,
                models.system_admins.c.created_at,
                models.system_admins.c.granted_by_user_id,
                models.users.c.email,
                models.users.c.display_name,
            )
            .join(models.users, models.users.c.id == models.system_admins.c.user_id)
            .order_by(models.system_admins.c.created_at)
        ).mappings()
        return [
            SystemAdminRecord(
                user_id=r["user_id"],
                email=r["email"],
                display_name=r["display_name"],
                granted_at=_as_aware(r["created_at"]),
                granted_by_user_id=r["granted_by_user_id"],
            )
            for r in rows
        ]


def count_system_admins(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(models.system_admins)).scalar_one()


def grant_system_admin(engine: Engine, user_id: str, granted_by_user_id: str | None) -> bool:
    """Grant box-admin to a user; False when already granted (idempotent)."""
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.system_admins.c.id).where(models.system_admins.c.user_id == user_id)
        ).first()
        if existing is not None:
            return False
        conn.execute(
            insert(models.system_admins).values(
                id=new_id(),
                user_id=user_id,
                granted_by_user_id=granted_by_user_id,
                created_at=utcnow(),
            )
        )
        return True


def revoke_system_admin(engine: Engine, user_id: str) -> bool:
    """Remove a user from the roster; False when they were not on it.

    The caller enforces the never-remove-the-last-admin rule — this function
    is also the undo path for a granted entry and must stay unconditional.
    """
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.system_admins).where(models.system_admins.c.user_id == user_id)
        )
        return result.rowcount > 0


def repoint_ai_runtime_configs(
    engine: Engine, *, provider: str, base_url: str, model: str
) -> int:
    """Point every household config on this runtime at the newly served model.

    A swap replaces the model for the WHOLE box — one vLLM serves all
    households — so leaving other households' rows on the old model name
    silently downgrades them to deterministic answers (found 2026-07-22 when
    the demo household kept requesting the pre-swap model). Returns the number
    of rows updated."""
    with engine.begin() as conn:
        result = conn.execute(
            update(models.ai_runtime_configs)
            .where(models.ai_runtime_configs.c.provider == provider)
            .where(models.ai_runtime_configs.c.base_url == base_url)
            .values(model=model, updated_at=utcnow())
        )
        return result.rowcount


def upsert_ai_runtime_config(
    engine: Engine,
    household_id: str,
    provider: str,
    base_url: str,
    model: str,
    enabled: bool,
) -> AiRuntimeConfigRecord:
    now = utcnow()
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.ai_runtime_configs.c.id).where(
                models.ai_runtime_configs.c.household_id == household_id
            )
        ).first()

        if existing is None:
            conn.execute(
                insert(models.ai_runtime_configs).values(
                    id=new_id(),
                    household_id=household_id,
                    provider=provider,
                    base_url=base_url,
                    model=model,
                    enabled=enabled,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            conn.execute(
                update(models.ai_runtime_configs)
                .where(models.ai_runtime_configs.c.household_id == household_id)
                .values(
                    provider=provider,
                    base_url=base_url,
                    model=model,
                    enabled=enabled,
                    updated_at=now,
                )
            )

    return AiRuntimeConfigRecord(
        household_id=household_id,
        provider=provider,
        base_url=base_url,
        model=model,
        enabled=enabled,
    )


# --- Imports ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportRecord:
    id: str
    household_id: str
    account_id: str | None
    source_type: str
    filename: str
    status: str
    error_message: str | None
    skipped_row_count: int
    retry_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImportFileRecord:
    id: str
    import_id: str
    storage_path: str
    content_type: str
    size_bytes: int


def _import_record_from_row(row: Any) -> ImportRecord:
    return ImportRecord(
        id=row["id"],
        household_id=row["household_id"],
        account_id=row["account_id"],
        source_type=row["source_type"],
        filename=row["filename"],
        status=row["status"],
        error_message=row["error_message"],
        skipped_row_count=row["skipped_row_count"],
        retry_count=row["retry_count"],
        created_at=row["created_at"],
    )


def create_import(
    engine: Engine,
    household_id: str,
    account_id: str | None,
    source_type: str,
    filename: str,
) -> ImportRecord:
    import_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.imports).values(
                id=import_id,
                household_id=household_id,
                account_id=account_id,
                source_type=source_type,
                filename=filename,
                status="pending",
                error_message=None,
                skipped_row_count=0,
                retry_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        row = (
            conn.execute(select(models.imports).where(models.imports.c.id == import_id))
            .mappings()
            .first()
        )

    assert row is not None
    return _import_record_from_row(row)


def get_import(engine: Engine, household_id: str, import_id: str) -> ImportRecord | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(models.imports).where(
                    models.imports.c.id == import_id, models.imports.c.household_id == household_id
                )
            )
            .mappings()
            .first()
        )

    return _import_record_from_row(row) if row is not None else None


def list_imports(engine: Engine, household_id: str) -> list[ImportRecord]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(models.imports)
                .where(models.imports.c.household_id == household_id)
                .order_by(models.imports.c.created_at.desc())
            )
            .mappings()
            .all()
        )

    return [_import_record_from_row(row) for row in rows]


def create_import_file(
    engine: Engine, import_id: str, storage_path: str, content_type: str, size_bytes: int
) -> ImportFileRecord:
    file_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.import_files).values(
                id=file_id,
                import_id=import_id,
                storage_path=storage_path,
                content_type=content_type,
                size_bytes=size_bytes,
                created_at=utcnow(),
            )
        )

    return ImportFileRecord(
        id=file_id,
        import_id=import_id,
        storage_path=storage_path,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def get_import_file(engine: Engine, import_id: str) -> ImportFileRecord | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(models.import_files).where(models.import_files.c.import_id == import_id)
            )
            .mappings()
            .first()
        )

    if row is None:
        return None

    return ImportFileRecord(
        id=row["id"],
        import_id=row["import_id"],
        storage_path=row["storage_path"],
        content_type=row["content_type"],
        size_bytes=row["size_bytes"],
    )


def update_import_status(
    engine: Engine,
    import_id: str,
    status: str,
    error_message: str | None = None,
    skipped_row_count: int | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "updated_at": utcnow()}
    if error_message is not None:
        values["error_message"] = error_message
    if skipped_row_count is not None:
        values["skipped_row_count"] = skipped_row_count

    with engine.begin() as conn:
        conn.execute(
            update(models.imports).where(models.imports.c.id == import_id).values(**values)
        )


def increment_import_retry_count(engine: Engine, import_id: str) -> int:
    with engine.begin() as conn:
        current = conn.execute(
            select(models.imports.c.retry_count).where(models.imports.c.id == import_id)
        ).scalar_one()
        new_count = current + 1
        conn.execute(
            update(models.imports)
            .where(models.imports.c.id == import_id)
            .values(retry_count=new_count, updated_at=utcnow())
        )

    return new_count


def list_processable_imports(engine: Engine) -> list[tuple[ImportRecord, ImportFileRecord]]:
    """Every household's pending imports that have an uploaded file, for the worker to process.

    Runs outside any single household's request context, so it is
    deliberately not household-scoped.
    """
    query = (
        select(
            models.imports.c.id.label("import_id"),
            models.imports.c.household_id,
            models.imports.c.account_id,
            models.imports.c.source_type,
            models.imports.c.filename,
            models.imports.c.status,
            models.imports.c.error_message,
            models.imports.c.skipped_row_count,
            models.imports.c.retry_count,
            models.imports.c.created_at,
            models.import_files.c.id.label("file_id"),
            models.import_files.c.storage_path,
            models.import_files.c.content_type,
            models.import_files.c.size_bytes,
        )
        .select_from(models.imports)
        .join(models.import_files, models.import_files.c.import_id == models.imports.c.id)
        .where(models.imports.c.status == "pending")
    )

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    results: list[tuple[ImportRecord, ImportFileRecord]] = []
    for row in rows:
        import_record = ImportRecord(
            id=row["import_id"],
            household_id=row["household_id"],
            account_id=row["account_id"],
            source_type=row["source_type"],
            filename=row["filename"],
            status=row["status"],
            error_message=row["error_message"],
            skipped_row_count=row["skipped_row_count"],
            retry_count=row["retry_count"],
            created_at=row["created_at"],
        )
        file_record = ImportFileRecord(
            id=row["file_id"],
            import_id=row["import_id"],
            storage_path=row["storage_path"],
            content_type=row["content_type"],
            size_bytes=row["size_bytes"],
        )
        results.append((import_record, file_record))

    return results


def transaction_exists(
    engine: Engine, household_id: str, account_id: str, occurred_at: date, amount_minor: int
) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            select(models.transactions.c.id).where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.account_id == account_id,
                models.transactions.c.occurred_at == occurred_at,
                models.transactions.c.amount_minor == amount_minor,
            )
        ).first()

    return row is not None


def create_transaction(
    engine: Engine,
    household_id: str,
    account_id: str,
    occurred_at: date,
    amount_minor: int,
    currency: str,
    merchant: str | None,
    description: str | None,
    import_source: str | None,
    import_id: str | None,
    review_state: str,
    possible_duplicate: bool = False,
    category_id: str | None = None,
) -> str:
    transaction_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.transactions).values(
                id=transaction_id,
                household_id=household_id,
                account_id=account_id,
                occurred_at=occurred_at,
                amount_minor=amount_minor,
                currency=currency,
                merchant=merchant,
                category_id=category_id,
                description=description,
                import_source=import_source,
                import_id=import_id,
                possible_duplicate=possible_duplicate,
                review_state=review_state,
                created_at=utcnow(),
            )
        )

    return transaction_id


def apply_import(engine: Engine, household_id: str, import_id: str) -> int:
    """Mark every pending transaction from this import as reviewed. Returns the count updated."""
    with engine.begin() as conn:
        result = conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.import_id == import_id,
                models.transactions.c.household_id == household_id,
                models.transactions.c.review_state == "pending",
            )
            .values(review_state="reviewed")
        )
        conn.execute(
            update(models.imports)
            .where(models.imports.c.id == import_id, models.imports.c.household_id == household_id)
            .values(status="completed", updated_at=utcnow())
        )

    return result.rowcount


def unapply_import(
    engine: Engine, household_id: str, import_id: str, previous_status: str
) -> None:
    """Undo of apply_import (M117): the import's reviewed transactions go back to
    pending and the import returns to its pre-apply status."""
    with engine.begin() as conn:
        conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.import_id == import_id,
                models.transactions.c.household_id == household_id,
                models.transactions.c.review_state == "reviewed",
            )
            .values(review_state="pending")
        )
        conn.execute(
            update(models.imports)
            .where(models.imports.c.id == import_id, models.imports.c.household_id == household_id)
            .values(status=previous_status, updated_at=utcnow())
        )


def discard_import(engine: Engine, household_id: str, import_id: str) -> int:
    """Delete every pending transaction from this import. Returns the count deleted."""
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.transactions).where(
                models.transactions.c.import_id == import_id,
                models.transactions.c.household_id == household_id,
                models.transactions.c.review_state == "pending",
            )
        )
        conn.execute(
            update(models.imports)
            .where(models.imports.c.id == import_id, models.imports.c.household_id == household_id)
            .values(status="discarded", updated_at=utcnow())
        )

    return result.rowcount


# --- Documents ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: str
    household_id: str
    content_type: str
    storage_path: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentExtractionRecord:
    id: str
    document_id: str
    extraction_type: str
    text: str
    structured_fields: dict[str, Any]
    confidence: float
    warnings: list[str]
    created_at: datetime


def create_document(
    engine: Engine,
    household_id: str,
    content_type: str,
    storage_path: str,
    import_id: str | None = None,
) -> DocumentRecord:
    document_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.documents).values(
                id=document_id,
                household_id=household_id,
                import_id=import_id,
                content_type=content_type,
                storage_path=storage_path,
                created_at=now,
            )
        )

    return DocumentRecord(
        id=document_id,
        household_id=household_id,
        content_type=content_type,
        storage_path=storage_path,
        created_at=now,
    )


def create_document_extraction(
    engine: Engine,
    document_id: str,
    extraction_type: str,
    text: str,
    structured_fields: dict[str, Any],
    confidence: float,
    warnings: list[str],
) -> DocumentExtractionRecord:
    extraction_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.document_extractions).values(
                id=extraction_id,
                document_id=document_id,
                extraction_type=extraction_type,
                text=text,
                structured_fields_json=structured_fields,
                confidence=confidence,
                warnings_json=warnings,
                created_at=now,
            )
        )

    return DocumentExtractionRecord(
        id=extraction_id,
        document_id=document_id,
        extraction_type=extraction_type,
        text=text,
        structured_fields=structured_fields,
        confidence=confidence,
        warnings=warnings,
        created_at=now,
    )


def list_documents_with_extractions(
    engine: Engine, household_id: str
) -> list[tuple[DocumentRecord, DocumentExtractionRecord | None]]:
    query = (
        select(
            models.documents.c.id.label("document_id"),
            models.documents.c.household_id,
            models.documents.c.content_type,
            models.documents.c.storage_path,
            models.documents.c.created_at.label("document_created_at"),
            models.document_extractions.c.id.label("extraction_id"),
            models.document_extractions.c.extraction_type,
            models.document_extractions.c.text,
            models.document_extractions.c.structured_fields_json,
            models.document_extractions.c.confidence,
            models.document_extractions.c.warnings_json,
            models.document_extractions.c.created_at.label("extraction_created_at"),
        )
        .select_from(models.documents)
        .join(
            models.document_extractions,
            models.document_extractions.c.document_id == models.documents.c.id,
            isouter=True,
        )
        .where(models.documents.c.household_id == household_id)
        .order_by(models.documents.c.created_at.desc())
    )

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    results: list[tuple[DocumentRecord, DocumentExtractionRecord | None]] = []
    for row in rows:
        document = DocumentRecord(
            id=row["document_id"],
            household_id=row["household_id"],
            content_type=row["content_type"],
            storage_path=row["storage_path"],
            created_at=row["document_created_at"],
        )
        extraction = None
        if row["extraction_id"] is not None:
            extraction = DocumentExtractionRecord(
                id=row["extraction_id"],
                document_id=row["document_id"],
                extraction_type=row["extraction_type"],
                text=row["text"],
                structured_fields=row["structured_fields_json"],
                confidence=row["confidence"],
                warnings=row["warnings_json"],
                created_at=row["extraction_created_at"],
            )
        results.append((document, extraction))

    return results


# --- Reports ---------------------------------------------------------------------


def list_transactions_in_range(
    engine: Engine, household_id: str, start: date, end_exclusive: date
) -> list[TransactionRecord]:
    query = (
        select(
            models.transactions.c.id,
            models.transactions.c.account_id,
            models.transactions.c.occurred_at,
            models.transactions.c.amount_minor,
            models.transactions.c.currency,
            models.transactions.c.merchant,
            models.transaction_categories.c.name.label("category"),
            models.transactions.c.description,
        )
        .select_from(models.transactions)
        .join(
            models.transaction_categories,
            models.transaction_categories.c.id == models.transactions.c.category_id,
            isouter=True,
        )
        .where(
            models.transactions.c.household_id == household_id,
            models.transactions.c.occurred_at >= start,
            models.transactions.c.occurred_at < end_exclusive,
        )
        .order_by(models.transactions.c.occurred_at)
    )

    with engine.connect() as conn:
        rows = conn.execute(query).all()

    return [
        TransactionRecord(
            id=row.id,
            account_id=row.account_id,
            occurred_at=row.occurred_at,
            amount_minor=row.amount_minor,
            currency=row.currency,
            merchant=row.merchant,
            category=row.category,
            description=row.description,
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class ReportRecord:
    id: str
    household_id: str
    report_type: str
    period_start: date
    period_end: date
    summary: dict[str, Any]
    explanation_text: str
    explanation_source: str
    model_version: str | None
    prompt_version: str | None
    calculation_version: str
    generated_at: datetime


def _report_record_from_row(row: Any) -> ReportRecord:
    return ReportRecord(
        id=row["id"],
        household_id=row["household_id"],
        report_type=row["report_type"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        summary=row["summary_json"],
        explanation_text=row["explanation_text"],
        explanation_source=row["explanation_source"],
        model_version=row["model_version"],
        prompt_version=row["prompt_version"],
        calculation_version=row["calculation_version"],
        generated_at=row["generated_at"],
    )


def upsert_report(
    engine: Engine,
    household_id: str,
    report_type: str,
    period_start: date,
    period_end: date,
    summary: dict[str, Any],
    explanation_text: str,
    explanation_source: str,
    calculation_version: str,
    model_version: str | None = None,
    prompt_version: str | None = None,
) -> ReportRecord:
    """Create or replace the report for this (household, report_type, period_start).

    Regenerating the same period is idempotent (an update, not a duplicate
    row), so the scheduled job can safely run more than once for the same
    period without operator intervention.
    """
    now = utcnow()
    with engine.begin() as conn:
        existing_id = conn.execute(
            select(models.reports.c.id).where(
                models.reports.c.household_id == household_id,
                models.reports.c.report_type == report_type,
                models.reports.c.period_start == period_start,
            )
        ).scalar_one_or_none()

        report_id = existing_id or new_id()
        values = dict(
            household_id=household_id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            summary_json=summary,
            explanation_text=explanation_text,
            explanation_source=explanation_source,
            model_version=model_version,
            prompt_version=prompt_version,
            calculation_version=calculation_version,
            generated_at=now,
        )

        if existing_id is not None:
            conn.execute(
                update(models.reports).where(models.reports.c.id == existing_id).values(**values)
            )
        else:
            conn.execute(insert(models.reports).values(id=report_id, **values))

    return ReportRecord(
        id=report_id,
        household_id=household_id,
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        summary=summary,
        explanation_text=explanation_text,
        explanation_source=explanation_source,
        model_version=model_version,
        prompt_version=prompt_version,
        calculation_version=calculation_version,
        generated_at=now,
    )


def get_report(engine: Engine, household_id: str, report_id: str) -> ReportRecord | None:
    query = select(models.reports).where(
        models.reports.c.household_id == household_id, models.reports.c.id == report_id
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _report_record_from_row(row) if row is not None else None


def get_report_by_period(
    engine: Engine, household_id: str, report_type: str, period_start: date
) -> ReportRecord | None:
    query = select(models.reports).where(
        models.reports.c.household_id == household_id,
        models.reports.c.report_type == report_type,
        models.reports.c.period_start == period_start,
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _report_record_from_row(row) if row is not None else None


def list_reports(engine: Engine, household_id: str) -> list[ReportRecord]:
    query = (
        select(models.reports)
        .where(models.reports.c.household_id == household_id)
        .order_by(models.reports.c.period_start.desc())
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_report_record_from_row(row) for row in rows]


def list_households(engine: Engine) -> list[str]:
    query = select(models.households.c.id)
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(query).all()]


# --- Net-worth history (M40) -------------------------------------------------


@dataclass(frozen=True, slots=True)
class NetWorthSnapshotRecord:
    as_of: date
    net_worth_minor: int
    currency: str


def record_net_worth_snapshot(
    engine: Engine, household_id: str, as_of: date, net_worth_minor: int, currency: str
) -> None:
    """Upsert today's snapshot: one row per household per day, latest value wins."""
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.net_worth_snapshots.c.id).where(
                models.net_worth_snapshots.c.household_id == household_id,
                models.net_worth_snapshots.c.as_of == as_of,
            )
        ).first()
        if existing is not None:
            conn.execute(
                update(models.net_worth_snapshots)
                .where(models.net_worth_snapshots.c.id == existing[0])
                .values(net_worth_minor=net_worth_minor, currency=currency)
            )
        else:
            conn.execute(
                insert(models.net_worth_snapshots).values(
                    id=new_id(),
                    household_id=household_id,
                    as_of=as_of,
                    net_worth_minor=net_worth_minor,
                    currency=currency,
                    created_at=utcnow(),
                )
            )


def earliest_transaction_month(engine: Engine, household_id: str) -> str | None:
    """The 'YYYY-MM' of the household's oldest transaction, so the month picker can
    stop there instead of scrolling into empty months forever. None if no history."""
    query = select(func.min(models.transactions.c.occurred_at)).where(
        models.transactions.c.household_id == household_id
    )
    with engine.connect() as conn:
        earliest = conn.execute(query).scalar_one_or_none()
    if earliest is None:
        return None
    return f"{earliest.year}-{earliest.month:02d}"


def transaction_month_range(engine: Engine, household_id: str) -> tuple[str | None, str | None]:
    """The 'YYYY-MM' of the household's oldest and newest transactions, so the
    advisor knows which months hold data. (None, None) when there's no history."""
    query = select(
        func.min(models.transactions.c.occurred_at),
        func.max(models.transactions.c.occurred_at),
    ).where(models.transactions.c.household_id == household_id)
    with engine.connect() as conn:
        earliest, latest = conn.execute(query).one()
    if earliest is None or latest is None:
        return None, None
    return f"{earliest.year}-{earliest.month:02d}", f"{latest.year}-{latest.month:02d}"


def net_worth_as_of(engine: Engine, household_id: str, on_or_before: date, currency: str) -> int:
    """Net worth from the latest snapshot on or before a date (0 if none). Used for
    a past month's historical Overview when no full snapshot was captured."""
    query = (
        select(models.net_worth_snapshots.c.net_worth_minor)
        .where(
            models.net_worth_snapshots.c.household_id == household_id,
            models.net_worth_snapshots.c.currency == currency,
            models.net_worth_snapshots.c.as_of <= on_or_before,
        )
        .order_by(models.net_worth_snapshots.c.as_of.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(query).first()
    return int(row[0]) if row is not None else 0


def list_net_worth_snapshots(
    engine: Engine, household_id: str, limit: int = 30
) -> list[NetWorthSnapshotRecord]:
    """The most recent `limit` snapshots, returned oldest-first for charting."""
    query = (
        select(
            models.net_worth_snapshots.c.as_of,
            models.net_worth_snapshots.c.net_worth_minor,
            models.net_worth_snapshots.c.currency,
        )
        .where(models.net_worth_snapshots.c.household_id == household_id)
        .order_by(models.net_worth_snapshots.c.as_of.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return [
        NetWorthSnapshotRecord(as_of=row.as_of, net_worth_minor=row.net_worth_minor, currency=row.currency)
        for row in reversed(rows)
    ]


# --- Backups -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupJobRecord:
    id: str
    status: str
    storage_path: str | None
    size_bytes: int | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    pruned_at: datetime | None
    created_at: datetime
    # M98: whether the completed archive reached the off-box share, and why not.
    remote_status: str | None = None
    remote_error: str | None = None


def _backup_job_record_from_row(row: Any) -> BackupJobRecord:
    return BackupJobRecord(
        id=row["id"],
        status=row["status"],
        storage_path=row["storage_path"],
        size_bytes=row["size_bytes"],
        error_message=row["error_message"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        pruned_at=row["pruned_at"],
        created_at=row["created_at"],
        remote_status=row.get("remote_status"),
        remote_error=row.get("remote_error"),
    )


def create_backup_job(engine: Engine) -> BackupJobRecord:
    backup_job_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.backup_jobs).values(
                id=backup_job_id,
                status="pending",
                storage_path=None,
                size_bytes=None,
                error_message=None,
                started_at=now,
                completed_at=None,
                pruned_at=None,
                created_at=now,
            )
        )
    return BackupJobRecord(
        id=backup_job_id,
        status="pending",
        storage_path=None,
        size_bytes=None,
        error_message=None,
        started_at=now,
        completed_at=None,
        pruned_at=None,
        created_at=now,
    )


def update_backup_job(
    engine: Engine,
    backup_job_id: str,
    status: str,
    storage_path: str | None = None,
    size_bytes: int | None = None,
    error_message: str | None = None,
    remote_status: str | None = None,
    remote_error: str | None = None,
) -> None:
    values: dict[str, Any] = {"status": status}
    if storage_path is not None:
        values["storage_path"] = storage_path
    if size_bytes is not None:
        values["size_bytes"] = size_bytes
    if error_message is not None:
        values["error_message"] = error_message
    if remote_status is not None:
        values["remote_status"] = remote_status
    if remote_error is not None:
        values["remote_error"] = remote_error
    if status in ("completed", "failed"):
        values["completed_at"] = utcnow()

    with engine.begin() as conn:
        conn.execute(
            update(models.backup_jobs)
            .where(models.backup_jobs.c.id == backup_job_id)
            .values(**values)
        )


def get_backup_job(engine: Engine, backup_job_id: str) -> BackupJobRecord | None:
    query = select(models.backup_jobs).where(models.backup_jobs.c.id == backup_job_id)
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _backup_job_record_from_row(row) if row is not None else None


def list_backup_jobs(engine: Engine) -> list[BackupJobRecord]:
    query = select(models.backup_jobs).order_by(models.backup_jobs.c.created_at.desc())
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_backup_job_record_from_row(row) for row in rows]


def delete_backup_job(engine: Engine, backup_job_id: str) -> None:
    """Remove a backup job row (its .enc file is removed by the caller)."""
    with engine.begin() as conn:
        conn.execute(
            delete(models.backup_jobs).where(models.backup_jobs.c.id == backup_job_id)
        )


def list_completed_backup_jobs_for_retention(engine: Engine) -> list[BackupJobRecord]:
    """Completed, not-yet-pruned backups, oldest first (the order retention deletes in)."""
    query = (
        select(models.backup_jobs)
        .where(models.backup_jobs.c.status == "completed", models.backup_jobs.c.pruned_at.is_(None))
        .order_by(models.backup_jobs.c.completed_at.asc())
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_backup_job_record_from_row(row) for row in rows]


def mark_backup_job_pruned(engine: Engine, backup_job_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(models.backup_jobs)
            .where(models.backup_jobs.c.id == backup_job_id)
            .values(pruned_at=utcnow(), storage_path=None)
        )


# --- Audit events ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    id: str
    household_id: str
    actor_user_id: str | None
    action: str
    entity_type: str
    entity_id: str | None
    summary: str
    created_at: datetime
    undo_token: str | None = None
    reverted_at: datetime | None = None


def record_audit_event(
    engine: Engine,
    household_id: str,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    summary: str,
    undo_token: str | None = None,
) -> str:
    audit_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.audit_events).values(
                id=audit_id,
                household_id=household_id,
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                created_at=utcnow(),
                undo_token=undo_token,
            )
        )
    return audit_id


def _audit_record(row: Any) -> AuditEventRecord:
    return AuditEventRecord(
        id=row["id"],
        household_id=row["household_id"],
        actor_user_id=row["actor_user_id"],
        action=row["action"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        summary=row["summary"],
        created_at=row["created_at"],
        undo_token=row["undo_token"],
        reverted_at=row["reverted_at"],
    )


def list_audit_events(
    engine: Engine, household_id: str, limit: int = 200
) -> list[AuditEventRecord]:
    query = (
        select(models.audit_events)
        .where(models.audit_events.c.household_id == household_id)
        .order_by(models.audit_events.c.created_at.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_audit_record(row) for row in rows]


def get_audit_event(
    engine: Engine, household_id: str, audit_id: str
) -> AuditEventRecord | None:
    query = select(models.audit_events).where(
        models.audit_events.c.household_id == household_id,
        models.audit_events.c.id == audit_id,
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _audit_record(row) if row is not None else None


def mark_audit_reverted(engine: Engine, household_id: str, audit_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(models.audit_events)
            .where(
                models.audit_events.c.household_id == household_id,
                models.audit_events.c.id == audit_id,
            )
            .values(reverted_at=utcnow())
        )


# --- Household bootstrap and membership --------------------------------------


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    household_id: str
    user_id: str
    role: str


@dataclass(frozen=True, slots=True)
class MemberRecord:
    user_id: str
    email: str
    display_name: str
    role: str
    created_at: datetime
    role_id: str | None = None
    role_name: str = ""


def user_email_exists(engine: Engine, email: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(select(models.users.c.id).where(models.users.c.email == email)).first()
    return row is not None


def create_household_with_owner(
    engine: Engine,
    display_name: str,
    base_currency: str,
    owner_email: str,
    owner_password_hash: str,
    owner_display_name: str,
) -> BootstrapResult:
    household_id = new_id()
    user_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.households).values(
                id=household_id,
                display_name=display_name,
                base_currency=base_currency,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(models.users).values(
                id=user_id,
                email=owner_email,
                password_hash=owner_password_hash,
                display_name=owner_display_name,
                created_at=now,
                updated_at=now,
            )
        )
        preset_ids = _seed_preset_roles(conn, household_id, now)
        conn.execute(
            insert(models.household_memberships).values(
                id=new_id(),
                household_id=household_id,
                user_id=user_id,
                role="owner",
                role_id=preset_ids["Admin"],
                created_at=now,
            )
        )
        # ADR 0065: whoever deploys the box and creates the first household is
        # its first SYSTEM ADMIN — box-global powers (model swaps) need one,
        # and there is nobody else yet to grant it. The demo seed bypasses
        # this function, so showcase credentials never qualify.
        roster_empty = (
            conn.execute(select(func.count()).select_from(models.system_admins)).scalar_one()
            == 0
        )
        if roster_empty:
            conn.execute(
                insert(models.system_admins).values(
                    id=new_id(),
                    user_id=user_id,
                    granted_by_user_id=None,
                    created_at=now,
                )
            )
    return BootstrapResult(household_id=household_id, user_id=user_id, role="owner")


# --- Roles & rights (ADR 0034) ------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoleRecord:
    id: str
    name: str
    rights: frozenset[str]
    built_in: bool
    member_count: int = 0


def _role_record_from_row(row: Any, member_count: int = 0) -> RoleRecord:
    return RoleRecord(
        id=row["id"],
        name=row["name"],
        rights=frozenset(row["rights_json"] or []),
        built_in=bool(row["built_in"]),
        member_count=member_count,
    )


def _seed_preset_roles(conn, household_id: str, now: datetime) -> dict[str, str]:
    """Insert the built-in preset roles for a household; returns name -> id."""
    from family_cfo_api import rights as rights_catalog

    ids: dict[str, str] = {}
    for name, preset_rights in rights_catalog.PRESET_RIGHTS.items():
        role_id = new_id()
        ids[name] = role_id
        conn.execute(
            insert(models.roles).values(
                id=role_id,
                household_id=household_id,
                name=name,
                rights_json=sorted(preset_rights),
                built_in=True,
                created_at=now,
                updated_at=now,
            )
        )
    return ids


def seed_preset_roles(engine: Engine, household_id: str) -> dict[str, str]:
    with engine.begin() as conn:
        return _seed_preset_roles(conn, household_id, utcnow())


def list_roles(engine: Engine, household_id: str) -> list[RoleRecord]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(models.roles)
            .where(models.roles.c.household_id == household_id)
            .order_by(models.roles.c.built_in.desc(), models.roles.c.name)
        ).mappings().all()
        counts = dict(
            conn.execute(
                select(
                    models.household_memberships.c.role_id,
                    func.count().label("n"),
                )
                .where(models.household_memberships.c.household_id == household_id)
                .group_by(models.household_memberships.c.role_id)
            ).all()
        )
    return [_role_record_from_row(row, counts.get(row["id"], 0)) for row in rows]


def get_role(engine: Engine, household_id: str, role_id: str) -> RoleRecord | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(models.roles).where(
                models.roles.c.household_id == household_id, models.roles.c.id == role_id
            )
        ).mappings().first()
        if row is None:
            return None
        count = conn.execute(
            select(func.count()).select_from(models.household_memberships).where(
                models.household_memberships.c.household_id == household_id,
                models.household_memberships.c.role_id == role_id,
            )
        ).scalar_one()
    return _role_record_from_row(row, count)


def get_role_by_name(engine: Engine, household_id: str, name: str) -> RoleRecord | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(models.roles).where(
                models.roles.c.household_id == household_id, models.roles.c.name == name
            )
        ).mappings().first()
    return _role_record_from_row(row) if row is not None else None


def create_role(
    engine: Engine, household_id: str, name: str, role_rights: set[str],
    role_id: str | None = None, built_in: bool = False,
) -> RoleRecord | None:
    """None when the name is already taken in this household."""
    role_id = role_id or new_id()
    now = utcnow()
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(models.roles).values(
                    id=role_id,
                    household_id=household_id,
                    name=name,
                    rights_json=sorted(role_rights),
                    built_in=built_in,
                    created_at=now,
                    updated_at=now,
                )
            )
    except IntegrityError:
        return None
    return RoleRecord(id=role_id, name=name, rights=frozenset(role_rights), built_in=built_in)


def update_role(
    engine: Engine, household_id: str, role_id: str,
    name: str | None = None, role_rights: set[str] | None = None,
) -> bool:
    values: dict[str, Any] = {"updated_at": utcnow()}
    if name is not None:
        values["name"] = name
    if role_rights is not None:
        values["rights_json"] = sorted(role_rights)
    with engine.begin() as conn:
        result = conn.execute(
            update(models.roles)
            .where(models.roles.c.household_id == household_id, models.roles.c.id == role_id)
            .values(**values)
        )
    return result.rowcount > 0


def delete_role(engine: Engine, household_id: str, role_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.roles).where(
                models.roles.c.household_id == household_id, models.roles.c.id == role_id
            )
        )
    return result.rowcount > 0


def resolve_member_rights(
    engine: Engine, household_id: str, user_id: str
) -> tuple[frozenset[str], str]:
    """(rights, role_name) for a member — the assigned role's rights, or the
    legacy role's preset for an un-backfilled row (ADR 0034)."""
    from family_cfo_api import rights as rights_catalog

    with engine.connect() as conn:
        row = conn.execute(
            select(
                models.household_memberships.c.role,
                models.roles.c.name,
                models.roles.c.rights_json,
            )
            .select_from(models.household_memberships)
            .outerjoin(models.roles, models.roles.c.id == models.household_memberships.c.role_id)
            .where(
                models.household_memberships.c.household_id == household_id,
                models.household_memberships.c.user_id == user_id,
            )
        ).mappings().first()
    if row is None:
        return frozenset(), ""
    if row["name"] is not None:
        member_rights = frozenset(row["rights_json"] or [])
        role_name = row["name"]
    else:
        member_rights = rights_catalog.rights_for_legacy_role(row["role"])
        role_name = rights_catalog.LEGACY_ROLE_TO_PRESET.get(row["role"], "")
    # ADR 0065: box-level rights come only from the system-admin roster (see
    # get_session_context, which applies the identical rule per request).
    member_rights = member_rights - rights_catalog.BOX_RIGHTS
    if is_system_admin(engine, user_id):
        member_rights = member_rights | rights_catalog.BOX_RIGHTS
    return member_rights, role_name


def legacy_role_for(role: RoleRecord) -> str:
    """The wire-compat legacy tier for a role: presets map exactly; a custom role
    travels as 'adult' (the constraint's closest non-admin tier)."""
    from family_cfo_api import rights as rights_catalog

    return rights_catalog.PRESET_TO_LEGACY_ROLE.get(role.name, "adult")


def assign_member_role(
    engine: Engine, household_id: str, user_id: str, role: RoleRecord
) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            update(models.household_memberships)
            .where(
                models.household_memberships.c.household_id == household_id,
                models.household_memberships.c.user_id == user_id,
            )
            .values(role=legacy_role_for(role), role_id=role.id)
        )
    return result.rowcount > 0


def list_members(engine: Engine, household_id: str) -> list[MemberRecord]:
    query = (
        select(
            models.users.c.id.label("user_id"),
            models.users.c.email,
            models.users.c.display_name,
            models.household_memberships.c.role,
            models.household_memberships.c.role_id,
            models.roles.c.name.label("role_name"),
            models.household_memberships.c.created_at,
        )
        .select_from(models.household_memberships)
        .join(models.users, models.users.c.id == models.household_memberships.c.user_id)
        .outerjoin(models.roles, models.roles.c.id == models.household_memberships.c.role_id)
        .where(models.household_memberships.c.household_id == household_id)
        .order_by(models.household_memberships.c.created_at)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        MemberRecord(
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
            created_at=row["created_at"],
            role_id=row["role_id"],
            role_name=row["role_name"] or "",
        )
        for row in rows
    ]


def get_member(engine: Engine, household_id: str, user_id: str) -> MemberRecord | None:
    for member in list_members(engine, household_id):
        if member.user_id == user_id:
            return member
    return None


def count_household_owners(engine: Engine, household_id: str) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count())
                .select_from(models.household_memberships)
                .where(
                    models.household_memberships.c.household_id == household_id,
                    models.household_memberships.c.role == "owner",
                )
            ).scalar_one()
        )


def create_member(
    engine: Engine,
    household_id: str,
    email: str,
    password_hash: str,
    display_name: str,
    role: str,
    role_id: str | None = None,
) -> MemberRecord:
    user_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.users).values(
                id=user_id,
                email=email,
                password_hash=password_hash,
                display_name=display_name,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(models.household_memberships).values(
                id=new_id(),
                household_id=household_id,
                user_id=user_id,
                role=role,
                role_id=role_id,
                created_at=now,
            )
        )
    return MemberRecord(
        user_id=user_id, email=email, display_name=display_name, role=role,
        created_at=now, role_id=role_id,
    )


def update_member_role(engine: Engine, household_id: str, user_id: str, role: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            update(models.household_memberships)
            .where(
                models.household_memberships.c.household_id == household_id,
                models.household_memberships.c.user_id == user_id,
            )
            .values(role=role)
        )
    return result.rowcount > 0


def restore_membership(engine: Engine, household_id: str, user_id: str, role: str) -> None:
    """Undo of a member removal (M117): the user row survives removal, so
    re-inserting the membership restores access. No-op if already a member."""
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.household_memberships.c.id).where(
                models.household_memberships.c.household_id == household_id,
                models.household_memberships.c.user_id == user_id,
            )
        ).first()
        if existing is not None:
            return
        from family_cfo_api import rights as rights_catalog

        preset = rights_catalog.LEGACY_ROLE_TO_PRESET.get(role, "User")
        preset_row = conn.execute(
            select(models.roles.c.id).where(
                models.roles.c.household_id == household_id,
                models.roles.c.name == preset,
            )
        ).first()
        conn.execute(
            insert(models.household_memberships).values(
                id=new_id(),
                household_id=household_id,
                user_id=user_id,
                role=role,
                role_id=preset_row[0] if preset_row else None,
                created_at=utcnow(),
            )
        )


def delete_ai_runtime_config(engine: Engine, household_id: str) -> None:
    """Undo of the FIRST runtime configuration (M117): back to 'not configured'."""
    with engine.begin() as conn:
        conn.execute(
            delete(models.ai_runtime_configs).where(
                models.ai_runtime_configs.c.household_id == household_id
            )
        )


def delete_member(engine: Engine, household_id: str, user_id: str) -> bool:
    now = utcnow()
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.household_memberships).where(
                models.household_memberships.c.household_id == household_id,
                models.household_memberships.c.user_id == user_id,
            )
        )
        if result.rowcount > 0:
            conn.execute(
                update(models.auth_sessions)
                .where(
                    models.auth_sessions.c.user_id == user_id,
                    models.auth_sessions.c.household_id == household_id,
                    models.auth_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
    return result.rowcount > 0


# --- Account writes ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountRecord:
    id: str
    name: str
    account_type: str
    currency: str
    annual_interest_rate: float | None = None
    minimum_payment_minor: int | None = None
    maturity_date: date | None = None
    next_payment_due_date: date | None = None
    emergency_fund_percent: float | None = None
    emergency_fund_minor: int | None = None


def _account_record_from_row(row: Any) -> AccountRecord:
    return AccountRecord(
        id=row["id"],
        name=row["name"],
        account_type=row["type"],
        currency=row["currency"],
        annual_interest_rate=row["annual_interest_rate"],
        minimum_payment_minor=row["minimum_payment_minor"],
        maturity_date=row["maturity_date"],
        next_payment_due_date=row["next_payment_due_date"],
        emergency_fund_percent=row["emergency_fund_percent"],
        emergency_fund_minor=row["emergency_fund_minor"],
    )


def get_account(engine: Engine, household_id: str, account_id: str) -> AccountRecord | None:
    query = select(models.accounts).where(
        models.accounts.c.household_id == household_id, models.accounts.c.id == account_id
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _account_record_from_row(row) if row is not None else None


def emergency_fund_reserved_minor(
    percent: float | None, fixed_minor: int | None, balance_minor: int
) -> int:
    """M36: derived emergency-fund reservation for one account.

    Percent of the latest balance (round half-up) or the fixed amount — never
    negative, never more than the balance. Zero when the account has no
    designation or a non-positive balance.
    """
    available = max(balance_minor, 0)
    if percent is not None:
        reserved = int(
            (Decimal(str(percent)) * available / 100).to_integral_value(rounding=ROUND_HALF_UP)
        )
        return min(available, max(reserved, 0))
    if fixed_minor is not None:
        return min(available, max(fixed_minor, 0))
    return 0


# A 401(k) loan is a liability for cash-flow purposes (its monthly repayment is a
# real claim on cash, so it flows through list_debts_with_terms into safe-to-spend),
# but it is owed to your own retirement — so it is excluded from external-debt
# reporting and is net-worth-neutral (see the engine's RETIREMENT_LOAN_TYPES).
RETIREMENT_LOAN_TYPES = frozenset({"401k_loan"})
LIABILITY_ACCOUNT_TYPES = frozenset(
    {"credit_card", "mortgage", "auto_loan", "student_loan", "other_liability", "401k_loan"}
)


@dataclass(frozen=True, slots=True)
class DebtAccountRecord:
    account_id: str
    name: str
    currency: str
    balance_owed_minor: int  # positive amount owed (abs of the negative balance)
    annual_interest_rate: float
    minimum_payment_minor: int
    account_type: str = "other_liability"


def list_liability_accounts(engine: Engine, household_id: str) -> list[AccountRecord]:
    """All liability-type accounts in the household (with or without debt terms)."""
    query = (
        select(models.accounts)
        .where(
            models.accounts.c.household_id == household_id,
            models.accounts.c.type.in_(tuple(LIABILITY_ACCOUNT_TYPES)),
        )
        .order_by(models.accounts.c.name)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_account_record_from_row(row) for row in rows]


def list_debts_with_terms(engine: Engine, household_id: str) -> list[DebtAccountRecord]:
    """Liability accounts that carry both debt terms and a latest balance, ready for payoff modeling."""
    balances = {b.account_id: b for b in list_account_balances(engine, household_id)}
    debts: list[DebtAccountRecord] = []
    for account in list_liability_accounts(engine, household_id):
        if account.annual_interest_rate is None or account.minimum_payment_minor is None:
            continue
        balance = balances.get(account.id)
        if balance is None or balance.balance_minor >= 0:
            continue
        debts.append(
            DebtAccountRecord(
                account_id=account.id,
                name=account.name,
                currency=account.currency,
                balance_owed_minor=abs(balance.balance_minor),
                annual_interest_rate=account.annual_interest_rate,
                minimum_payment_minor=account.minimum_payment_minor,
                account_type=account.account_type,
            )
        )
    return debts


def count_liabilities_without_terms(engine: Engine, household_id: str) -> int:
    """Liability accounts (with a balance owed) that lack debt terms and so can't be modeled."""
    balances = {b.account_id: b for b in list_account_balances(engine, household_id)}
    count = 0
    for account in list_liability_accounts(engine, household_id):
        balance = balances.get(account.id)
        if balance is None or balance.balance_minor >= 0:
            continue
        if account.annual_interest_rate is None or account.minimum_payment_minor is None:
            count += 1
    return count


def create_account(
    engine: Engine,
    household_id: str,
    name: str,
    account_type: str,
    currency: str,
    annual_interest_rate: float | None = None,
    minimum_payment_minor: int | None = None,
    maturity_date: date | None = None,
    next_payment_due_date: date | None = None,
) -> AccountRecord:
    account_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.accounts).values(
                id=account_id,
                household_id=household_id,
                name=name,
                type=account_type,
                currency=currency,
                annual_interest_rate=annual_interest_rate,
                minimum_payment_minor=minimum_payment_minor,
                maturity_date=maturity_date,
                next_payment_due_date=next_payment_due_date,
                created_at=now,
                updated_at=now,
            )
        )
    return AccountRecord(
        id=account_id,
        name=name,
        account_type=account_type,
        currency=currency,
        annual_interest_rate=annual_interest_rate,
        minimum_payment_minor=minimum_payment_minor,
        maturity_date=maturity_date,
        next_payment_due_date=next_payment_due_date,
    )


def update_account(
    engine: Engine,
    household_id: str,
    account_id: str,
    name: str | None = None,
    account_type: str | None = None,
    annual_interest_rate: float | None = None,
    minimum_payment_minor: int | None = None,
    maturity_date: date | None = None,
    next_payment_due_date: date | None = None,
    emergency_fund_percent: float | None = None,
    emergency_fund_minor: int | None = None,
    clear_emergency_fund: bool = False,
) -> bool:
    values: dict[str, Any] = {"updated_at": utcnow()}
    if name is not None:
        values["name"] = name
    if account_type is not None:
        values["type"] = account_type
    if annual_interest_rate is not None:
        values["annual_interest_rate"] = annual_interest_rate
    if minimum_payment_minor is not None:
        values["minimum_payment_minor"] = minimum_payment_minor
    if maturity_date is not None:
        values["maturity_date"] = maturity_date
    if next_payment_due_date is not None:
        values["next_payment_due_date"] = next_payment_due_date
    # M36: setting one designation clears the other (mutually exclusive by CHECK).
    if clear_emergency_fund:
        values["emergency_fund_percent"] = None
        values["emergency_fund_minor"] = None
    elif emergency_fund_percent is not None:
        values["emergency_fund_percent"] = emergency_fund_percent
        values["emergency_fund_minor"] = None
    elif emergency_fund_minor is not None:
        values["emergency_fund_minor"] = emergency_fund_minor
        values["emergency_fund_percent"] = None
    with engine.begin() as conn:
        result = conn.execute(
            update(models.accounts)
            .where(
                models.accounts.c.household_id == household_id, models.accounts.c.id == account_id
            )
            .values(**values)
        )
    return result.rowcount > 0


def account_in_use(engine: Engine, account_id: str) -> bool:
    with engine.connect() as conn:
        for table in (models.transactions, models.bills, models.imports):
            row = conn.execute(
                select(table.c.id).where(table.c.account_id == account_id).limit(1)
            ).first()
            if row is not None:
                return True
    return False


def delete_account(engine: Engine, household_id: str, account_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.account_balances).where(
                models.account_balances.c.account_id == account_id
            )
        )
        result = conn.execute(
            delete(models.accounts).where(
                models.accounts.c.household_id == household_id,
                models.accounts.c.id == account_id,
            )
        )
    return result.rowcount > 0


def get_latest_balance_minor(engine: Engine, account_id: str) -> int:
    """Latest recorded balance for an account, or 0 if none has been recorded yet."""
    latest = (
        select(models.account_balances.c.balance_minor)
        .where(models.account_balances.c.account_id == account_id)
        .order_by(models.account_balances.c.as_of.desc())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(latest).first()
    return int(row[0]) if row is not None else 0


def delete_account_balance(engine: Engine, household_id: str, balance_id: str) -> bool:
    """Undo of a recorded balance snapshot (M117): the prior snapshot becomes
    current again. Household-scoped through the owning account."""
    owned_accounts = select(models.accounts.c.id).where(
        models.accounts.c.household_id == household_id
    )
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.account_balances).where(
                models.account_balances.c.id == balance_id,
                models.account_balances.c.account_id.in_(owned_accounts),
            )
        )
    return result.rowcount > 0


def record_account_balance(
    engine: Engine, account_id: str, balance_minor: int, as_of: datetime | None = None
) -> str:
    balance_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.account_balances).values(
                id=balance_id,
                account_id=account_id,
                balance_minor=balance_minor,
                # A statement dates its balance by its closing date, so an old
                # statement can't clobber a newer balance (list_account_balances
                # keeps the latest by as_of). Defaults to now for live updates.
                as_of=as_of or now,
                created_at=now,
            )
        )
    return balance_id


# --- Transaction writes ------------------------------------------------------


def get_transaction(
    engine: Engine, household_id: str, transaction_id: str
) -> TransactionRecord | None:
    query = (
        select(
            models.transactions.c.id,
            models.transactions.c.account_id,
            models.transactions.c.occurred_at,
            models.transactions.c.amount_minor,
            models.transactions.c.currency,
            models.transactions.c.merchant,
            models.transaction_categories.c.name.label("category"),
            models.transactions.c.category_id,
            models.transactions.c.description,
            models.transactions.c.duplicate_state,
            models.transactions.c.external_id,
            models.transactions.c.note,
            models.transactions.c.attachment_path,
            models.transactions.c.attachment_content_type,
        )
        .select_from(models.transactions)
        .join(
            models.transaction_categories,
            models.transaction_categories.c.id == models.transactions.c.category_id,
            isouter=True,
        )
        .where(
            models.transactions.c.household_id == household_id,
            models.transactions.c.id == transaction_id,
        )
    )
    with engine.connect() as conn:
        row = conn.execute(query).first()
    if row is None:
        return None
    return TransactionRecord(
        id=row.id,
        account_id=row.account_id,
        occurred_at=row.occurred_at,
        amount_minor=row.amount_minor,
        currency=row.currency,
        merchant=row.merchant,
        category=row.category,
        category_id=row.category_id,
        description=row.description,
        duplicate_state=row.duplicate_state,
        external_id=row.external_id,
        note=row.note,
        attachment_path=row.attachment_path,
        attachment_content_type=row.attachment_content_type,
    )


def set_transaction_note(engine: Engine, household_id: str, transaction_id: str, note: str | None) -> bool:
    """M100: set/clear a transaction's free-text note. True if a row updated."""
    with engine.begin() as conn:
        result = conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.id == transaction_id,
            )
            .values(note=(note or None))
        )
    return result.rowcount > 0


def set_transaction_attachment(
    engine: Engine, household_id: str, transaction_id: str, path: str | None, content_type: str | None
) -> None:
    """M100: point a transaction at its stored attachment (or clear it with None)."""
    with engine.begin() as conn:
        conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.id == transaction_id,
            )
            .values(attachment_path=path, attachment_content_type=content_type)
        )


def update_transaction(
    engine: Engine,
    household_id: str,
    transaction_id: str,
    account_id: str | None = None,
    occurred_at: date | None = None,
    amount_minor: int | None = None,
    currency: str | None = None,
    merchant: str | None = None,
    description: str | None = None,
    category_id: str | None = None,
    clear_category: bool = False,
) -> bool:
    values: dict[str, Any] = {}
    if account_id is not None:
        values["account_id"] = account_id
    if occurred_at is not None:
        values["occurred_at"] = occurred_at
    if amount_minor is not None:
        values["amount_minor"] = amount_minor
    if currency is not None:
        values["currency"] = currency
    if merchant is not None:
        values["merchant"] = merchant
    if description is not None:
        values["description"] = description
    # M45: assign or clear the category (clear distinguishes from "unchanged").
    if clear_category:
        values["category_id"] = None
    elif category_id is not None:
        values["category_id"] = category_id
    if not values:
        return get_transaction(engine, household_id, transaction_id) is not None
    with engine.begin() as conn:
        result = conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.id == transaction_id,
            )
            .values(**values)
        )
    return result.rowcount > 0


def delete_transaction(engine: Engine, household_id: str, transaction_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.transactions).where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.id == transaction_id,
            )
        )
    return result.rowcount > 0


def restore_deleted_transaction(
    engine: Engine,
    household_id: str,
    *,
    transaction_id: str,
    account_id: str,
    occurred_at: date,
    amount_minor: int,
    currency: str,
    merchant: str | None,
    description: str | None,
    category_id: str | None,
    duplicate_state: str | None,
    external_id: str | None,
    note: str | None,
    attachment_path: str | None,
    attachment_content_type: str | None,
) -> None:
    """Re-insert a transaction that was deleted, for undo (M110). Reuses the
    original id and every preserved field — same aggregator id (so bank dedupe
    still recognises it), note, attachment, category and duplicate flag — so the
    row comes back exactly as it was. A no-op if a row with that id already
    exists (undo applied twice)."""
    if get_transaction(engine, household_id, transaction_id) is not None:
        return
    with engine.begin() as conn:
        conn.execute(
            insert(models.transactions).values(
                id=transaction_id,
                household_id=household_id,
                account_id=account_id,
                occurred_at=occurred_at,
                amount_minor=amount_minor,
                currency=currency,
                merchant=merchant,
                category_id=category_id,
                description=description,
                duplicate_state=duplicate_state,
                external_id=external_id,
                note=note,
                attachment_path=attachment_path,
                attachment_content_type=attachment_content_type,
                review_state="reviewed",
                created_at=utcnow(),
            )
        )


# --- Bill writes -------------------------------------------------------------


def set_transactions_category(
    engine: Engine, household_id: str, transaction_ids: list[str], category_id: str
) -> int:
    """Bulk-file a set of transactions under one category (M96). Returns the count
    updated. Used to propagate a bill's category to its matching transactions."""
    if not transaction_ids:
        return 0
    with engine.begin() as conn:
        result = conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.id.in_(transaction_ids),
            )
            .values(category_id=category_id)
        )
    return result.rowcount


# --- M97: duplicate review queue ---------------------------------------------

REVIEW_DUPLICATE_STATES = ("flagged", "disputed")
USER_DUPLICATE_STATES = ("dismissed", "disputed")


def flag_possible_duplicates(engine: Engine, household_id: str) -> int:
    """Flag exact-duplicate groups — same account AND content hash, 2+ rows — as
    'flagged' for the Review queue. Only touches rows still at NULL, so a user's
    'dismissed'/'disputed' decision is never re-flagged on the next sync. Also
    clears a now-stale 'flagged' whose group has fallen below two (e.g. after the
    user deleted the other leg) or that has since been categorized as a
    non-spending movement. Returns how many were newly flagged.

    Non-spending categories (Transfers/Income/Taxes) are excluded: those are the
    movements that *legitimately* repeat — RSU sell-to-cover lots journaled out as
    Taxes, identical-price share sales booked as Income, paired transfers — and
    they aren't charges the user could dispute."""
    from collections import defaultdict

    with engine.begin() as conn:
        rows = conn.execute(
            select(
                models.transactions.c.id,
                models.transactions.c.account_id,
                models.transactions.c.import_hash,
                models.transactions.c.duplicate_state,
                func.lower(models.transaction_categories.c.name).label("category"),
            )
            .select_from(models.transactions)
            .join(
                models.transaction_categories,
                models.transaction_categories.c.id == models.transactions.c.category_id,
                isouter=True,
            )
            .where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.import_hash.is_not(None),
                # A $0 line (e.g. an RSU vest lot) is not a charge to dispute.
                models.transactions.c.amount_minor != 0,
            )
        ).all()

        groups: dict[tuple[str, str], list] = defaultdict(list)
        for row in rows:
            groups[(row.account_id, row.import_hash)].append(row)

        to_flag: list[str] = []
        to_clear: list[str] = []
        for members in groups.values():
            is_duplicate = len(members) > 1
            for member in members:
                non_spending = (member.category or "") in NON_SPENDING_CATEGORY_NAMES
                flaggable = is_duplicate and not non_spending
                if flaggable and member.duplicate_state is None:
                    to_flag.append(member.id)
                elif not flaggable and member.duplicate_state == "flagged":
                    to_clear.append(member.id)

        if to_flag:
            conn.execute(
                update(models.transactions)
                .where(models.transactions.c.id.in_(to_flag))
                .values(duplicate_state="flagged")
            )
        if to_clear:
            conn.execute(
                update(models.transactions)
                .where(models.transactions.c.id.in_(to_clear))
                .values(duplicate_state=None)
            )
    return len(to_flag)


def set_transaction_duplicate_state(
    engine: Engine, household_id: str, transaction_id: str, state: str | None
) -> bool:
    """Set (or clear, with None) a transaction's duplicate_state. True if a row
    was updated."""
    with engine.begin() as conn:
        result = conn.execute(
            update(models.transactions)
            .where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.id == transaction_id,
            )
            .values(duplicate_state=state)
        )
    return result.rowcount > 0


def count_review_transactions(engine: Engine, household_id: str) -> int:
    """How many transactions are awaiting review (flagged or disputed) — drives
    the Review tab's badge."""
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count())
                .select_from(models.transactions)
                .where(
                    models.transactions.c.household_id == household_id,
                    models.transactions.c.duplicate_state.in_(REVIEW_DUPLICATE_STATES),
                )
            ).scalar_one()
        )


def _recurring_record_from_row(row: Any) -> RecurringRecord:
    # Shared by bills and income sources; only bills carry due-date/category cols.
    return RecurringRecord(
        id=row["id"],
        name=row["name"],
        amount_minor=row["amount_minor"],
        currency=row["currency"],
        frequency=row["frequency"],
        next_due_date=row.get("next_due_date"),
        category_id=row.get("category_id"),
    )


def get_bill(engine: Engine, household_id: str, bill_id: str) -> RecurringRecord | None:
    query = select(models.bills).where(
        models.bills.c.household_id == household_id, models.bills.c.id == bill_id
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _recurring_record_from_row(row) if row is not None else None


def create_bill(
    engine: Engine,
    household_id: str,
    name: str,
    amount_minor: int,
    currency: str,
    frequency: str,
    account_id: str | None = None,
    next_due_date: date | None = None,
    category_id: str | None = None,
) -> RecurringRecord:
    bill_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.bills).values(
                id=bill_id,
                household_id=household_id,
                account_id=account_id,
                name=name,
                amount_minor=amount_minor,
                currency=currency,
                frequency=frequency,
                next_due_date=next_due_date,
                category_id=category_id,
                created_at=now,
                updated_at=now,
            )
        )
    return RecurringRecord(
        id=bill_id,
        name=name,
        amount_minor=amount_minor,
        currency=currency,
        frequency=frequency,
        next_due_date=next_due_date,
        category_id=category_id,
    )




def update_bill(
    engine: Engine,
    household_id: str,
    bill_id: str,
    name: str | None = None,
    amount_minor: int | None = None,
    currency: str | None = None,
    frequency: str | None = None,
    next_due_date: date | None = None,
    category_id: str | None | Any = _UNSET,
) -> bool:
    values: dict[str, Any] = {"updated_at": utcnow()}
    if name is not None:
        values["name"] = name
    if amount_minor is not None:
        values["amount_minor"] = amount_minor
    if currency is not None:
        values["currency"] = currency
    if frequency is not None:
        values["frequency"] = frequency
    if next_due_date is not None:
        values["next_due_date"] = next_due_date
    # Sentinel so passing None explicitly CLEARS the category (vs. leaving it).
    if category_id is not _UNSET:
        values["category_id"] = category_id
    with engine.begin() as conn:
        result = conn.execute(
            update(models.bills)
            .where(models.bills.c.household_id == household_id, models.bills.c.id == bill_id)
            .values(**values)
        )
    return result.rowcount > 0


def delete_bill(engine: Engine, household_id: str, bill_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.bills).where(
                models.bills.c.household_id == household_id, models.bills.c.id == bill_id
            )
        )
    return result.rowcount > 0


# --- Income writes -----------------------------------------------------------


def get_income_source(engine: Engine, household_id: str, income_id: str) -> RecurringRecord | None:
    query = select(models.income_sources).where(
        models.income_sources.c.household_id == household_id,
        models.income_sources.c.id == income_id,
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return _recurring_record_from_row(row) if row is not None else None


def create_income_source(
    engine: Engine,
    household_id: str,
    name: str,
    amount_minor: int,
    currency: str,
    frequency: str,
) -> RecurringRecord:
    income_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.income_sources).values(
                id=income_id,
                household_id=household_id,
                name=name,
                amount_minor=amount_minor,
                currency=currency,
                frequency=frequency,
                created_at=now,
                updated_at=now,
            )
        )
    return RecurringRecord(
        id=income_id, name=name, amount_minor=amount_minor, currency=currency, frequency=frequency
    )


def update_income_source(
    engine: Engine,
    household_id: str,
    income_id: str,
    name: str | None = None,
    amount_minor: int | None = None,
    currency: str | None = None,
    frequency: str | None = None,
) -> bool:
    values: dict[str, Any] = {"updated_at": utcnow()}
    if name is not None:
        values["name"] = name
    if amount_minor is not None:
        values["amount_minor"] = amount_minor
    if currency is not None:
        values["currency"] = currency
    if frequency is not None:
        values["frequency"] = frequency
    with engine.begin() as conn:
        result = conn.execute(
            update(models.income_sources)
            .where(
                models.income_sources.c.household_id == household_id,
                models.income_sources.c.id == income_id,
            )
            .values(**values)
        )
    return result.rowcount > 0


def delete_income_source(engine: Engine, household_id: str, income_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.income_sources).where(
                models.income_sources.c.household_id == household_id,
                models.income_sources.c.id == income_id,
            )
        )
    return result.rowcount > 0


# --- Conversations (M10) -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    id: str
    household_id: str
    created_by_user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationMessageRecord:
    id: str
    conversation_id: str
    role: str
    content: str
    recommendation_id: str | None
    sequence: int
    created_at: datetime


def create_conversation(
    engine: Engine, household_id: str, created_by_user_id: str, title: str
) -> ConversationRecord:
    conversation_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.conversations).values(
                id=conversation_id,
                household_id=household_id,
                created_by_user_id=created_by_user_id,
                title=title,
                created_at=now,
                updated_at=now,
            )
        )
    return ConversationRecord(
        id=conversation_id,
        household_id=household_id,
        created_by_user_id=created_by_user_id,
        title=title,
        created_at=now,
        updated_at=now,
        message_count=0,
    )


def get_conversation(
    engine: Engine, household_id: str, conversation_id: str, user_id: str
) -> ConversationRecord | None:
    # ADR 0038: a conversation is private to the member who created it — scope by
    # created_by_user_id so one member can't read another's thread by id.
    query = select(models.conversations).where(
        models.conversations.c.household_id == household_id,
        models.conversations.c.id == conversation_id,
        models.conversations.c.created_by_user_id == user_id,
    )
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
        if row is None:
            return None
        count = conn.execute(
            select(func.count())
            .select_from(models.conversation_messages)
            .where(models.conversation_messages.c.conversation_id == conversation_id)
        ).scalar_one()
    return ConversationRecord(
        id=row["id"],
        household_id=row["household_id"],
        created_by_user_id=row["created_by_user_id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=int(count),
        summary=row["summary"],
    )


def list_all_conversation_ids(engine: Engine, household_id: str) -> list[str]:
    """Every conversation id in the household, regardless of who created it — for
    the internal memory backfill only. Extracted memories are shared household
    financial context; the user-facing listing stays per-user (ADR 0038)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(models.conversations.c.id).where(
                models.conversations.c.household_id == household_id
            )
        ).all()
    return [row[0] for row in rows]


def list_conversations(engine: Engine, household_id: str, user_id: str) -> list[ConversationRecord]:
    message_counts = (
        select(
            models.conversation_messages.c.conversation_id,
            func.count().label("message_count"),
        )
        .group_by(models.conversation_messages.c.conversation_id)
        .subquery()
    )
    query = (
        select(
            models.conversations.c.id,
            models.conversations.c.household_id,
            models.conversations.c.created_by_user_id,
            models.conversations.c.title,
            models.conversations.c.created_at,
            models.conversations.c.updated_at,
            func.coalesce(message_counts.c.message_count, 0).label("message_count"),
        )
        .select_from(models.conversations)
        .join(
            message_counts,
            message_counts.c.conversation_id == models.conversations.c.id,
            isouter=True,
        )
        .where(
            models.conversations.c.household_id == household_id,
            models.conversations.c.created_by_user_id == user_id,  # ADR 0038: private per user
        )
        .order_by(models.conversations.c.updated_at.desc())
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        ConversationRecord(
            id=row["id"],
            household_id=row["household_id"],
            created_by_user_id=row["created_by_user_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=int(row["message_count"]),
        )
        for row in rows
    ]


def list_conversation_messages(
    engine: Engine, conversation_id: str
) -> list[ConversationMessageRecord]:
    query = (
        select(models.conversation_messages)
        .where(models.conversation_messages.c.conversation_id == conversation_id)
        .order_by(models.conversation_messages.c.sequence)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        ConversationMessageRecord(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            recommendation_id=row["recommendation_id"],
            sequence=row["sequence"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def append_conversation_turn(
    engine: Engine,
    conversation_id: str,
    user_content: str,
    assistant_content: str,
    recommendation_id: str,
) -> None:
    """Append a user message and its assistant answer as one atomic turn."""
    now = utcnow()
    with engine.begin() as conn:
        next_sequence = conn.execute(
            select(func.coalesce(func.max(models.conversation_messages.c.sequence), 0)).where(
                models.conversation_messages.c.conversation_id == conversation_id
            )
        ).scalar_one()
        conn.execute(
            insert(models.conversation_messages).values(
                id=new_id(),
                conversation_id=conversation_id,
                role="user",
                content=user_content,
                recommendation_id=None,
                sequence=next_sequence + 1,
                created_at=now,
            )
        )
        conn.execute(
            insert(models.conversation_messages).values(
                id=new_id(),
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                recommendation_id=recommendation_id,
                sequence=next_sequence + 2,
                created_at=now,
            )
        )
        conn.execute(
            update(models.conversations)
            .where(models.conversations.c.id == conversation_id)
            .values(updated_at=now)
        )


def delete_conversation(
    engine: Engine, household_id: str, conversation_id: str, user_id: str
) -> bool:
    with engine.begin() as conn:
        owned = conn.execute(
            select(models.conversations.c.id).where(
                models.conversations.c.household_id == household_id,
                models.conversations.c.id == conversation_id,
                models.conversations.c.created_by_user_id == user_id,  # ADR 0038
            )
        ).first()
        if owned is None:
            return False
        conn.execute(
            delete(models.conversation_messages).where(
                models.conversation_messages.c.conversation_id == conversation_id
            )
        )
        conn.execute(
            delete(models.conversations).where(models.conversations.c.id == conversation_id)
        )
    return True


def set_conversation_summary(engine: Engine, conversation_id: str, summary: str) -> None:
    """M57 (ADR 0016): store the rolling summary of turns older than the history window."""
    with engine.begin() as conn:
        conn.execute(
            update(models.conversations)
            .where(models.conversations.c.id == conversation_id)
            .values(summary=summary)
        )


# --- M58: bill suggestions from transactions ----------------------------------


def list_bill_detection_transactions(
    engine: Engine, household_id: str, *, since: date
) -> list[tuple[date, int, str, str | None, str | None]]:
    """Outflow rows from checking/credit-card accounts for recurring detection.

    Returns (occurred_at, amount_minor, currency, merchant, description).
    """
    query = (
        select(
            models.transactions.c.occurred_at,
            models.transactions.c.amount_minor,
            models.transactions.c.currency,
            models.transactions.c.merchant,
            models.transactions.c.description,
        )
        .select_from(
            models.transactions.join(
                models.accounts, models.transactions.c.account_id == models.accounts.c.id
            )
        )
        .where(
            models.transactions.c.household_id == household_id,
            models.transactions.c.amount_minor < 0,
            models.transactions.c.occurred_at >= since,
            models.accounts.c.type.in_(("checking", "credit_card")),
        )
        .order_by(models.transactions.c.occurred_at)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return [tuple(row) for row in rows]


def list_bill_suggestion_dismissals(engine: Engine, household_id: str) -> set[str]:
    query = select(models.bill_suggestion_dismissals.c.merchant_key).where(
        models.bill_suggestion_dismissals.c.household_id == household_id
    )
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(query).all()}


def remove_bill_suggestion_dismissal(engine: Engine, household_id: str, merchant_key: str) -> None:
    """Undo of a dismissal (M117): the suggestion reappears on the next fetch."""
    with engine.begin() as conn:
        conn.execute(
            delete(models.bill_suggestion_dismissals).where(
                models.bill_suggestion_dismissals.c.household_id == household_id,
                models.bill_suggestion_dismissals.c.merchant_key == merchant_key,
            )
        )


def add_bill_suggestion_dismissal(engine: Engine, household_id: str, merchant_key: str) -> None:
    """Idempotent: dismissing an already-dismissed merchant is a no-op."""
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.bill_suggestion_dismissals.c.id).where(
                models.bill_suggestion_dismissals.c.household_id == household_id,
                models.bill_suggestion_dismissals.c.merchant_key == merchant_key,
            )
        ).first()
        if existing is not None:
            return
        conn.execute(
            insert(models.bill_suggestion_dismissals).values(
                id=new_id(),
                household_id=household_id,
                merchant_key=merchant_key,
                created_at=utcnow(),
            )
        )


# --- M73: compensation profiles ------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncomeProfileRecord:
    id: str
    household_id: str
    label: str
    base_salary_minor: int
    rsu_annual_minor: int
    rsu_frequency: str | None
    rsu_next_vest_date: date | None
    bonus_percent: float
    bonus_month: int | None
    w2_year: int | None
    w2_wages_minor: int | None
    w2_withheld_minor: int | None


def list_income_profiles(engine: Engine, household_id: str) -> list[IncomeProfileRecord]:
    query = (
        select(models.income_profiles)
        .where(models.income_profiles.c.household_id == household_id)
        .order_by(models.income_profiles.c.created_at)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        IncomeProfileRecord(
            id=row["id"],
            household_id=row["household_id"],
            label=row["label"],
            base_salary_minor=row["base_salary_minor"],
            rsu_annual_minor=row["rsu_annual_minor"],
            rsu_frequency=row["rsu_frequency"],
            rsu_next_vest_date=row["rsu_next_vest_date"],
            bonus_percent=row["bonus_percent"],
            bonus_month=row["bonus_month"],
            w2_year=row["w2_year"],
            w2_wages_minor=row["w2_wages_minor"],
            w2_withheld_minor=row["w2_withheld_minor"],
        )
        for row in rows
    ]


def create_income_profile(
    engine: Engine,
    household_id: str,
    *,
    label: str,
    base_salary_minor: int = 0,
    rsu_annual_minor: int = 0,
    rsu_frequency: str | None = None,
    rsu_next_vest_date: date | None = None,
    bonus_percent: float = 0.0,
    bonus_month: int | None = None,
    w2_year: int | None = None,
    w2_wages_minor: int | None = None,
    w2_withheld_minor: int | None = None,
) -> str:
    profile_id = new_id()
    now = utcnow()
    with engine.begin() as conn:
        conn.execute(
            insert(models.income_profiles).values(
                id=profile_id,
                household_id=household_id,
                label=label,
                base_salary_minor=base_salary_minor,
                rsu_annual_minor=rsu_annual_minor,
                rsu_frequency=rsu_frequency,
                rsu_next_vest_date=rsu_next_vest_date,
                bonus_percent=bonus_percent,
                bonus_month=bonus_month,
                w2_year=w2_year,
                w2_wages_minor=w2_wages_minor,
                w2_withheld_minor=w2_withheld_minor,
                created_at=now,
                updated_at=now,
            )
        )
    return profile_id


def delete_income_profile(engine: Engine, household_id: str, profile_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.income_profiles).where(
                models.income_profiles.c.household_id == household_id,
                models.income_profiles.c.id == profile_id,
            )
        )
    return result.rowcount > 0


# --- M61: income analysis ------------------------------------------------------


def list_income_detection_transactions(
    engine: Engine, household_id: str, *, since: date
) -> list[tuple[str, date, int, str, str | None, str | None, str, str | None]]:
    """Inflow rows from checking accounts for recurring-income detection.

    Returns (id, occurred_at, amount_minor, currency, merchant, description,
    account_name, institution).
    """
    query = (
        select(
            models.transactions.c.id,
            models.transactions.c.occurred_at,
            models.transactions.c.amount_minor,
            models.transactions.c.currency,
            models.transactions.c.merchant,
            models.transactions.c.description,
            models.accounts.c.name,
            models.accounts.c.institution,
        )
        .select_from(
            models.transactions.join(
                models.accounts, models.transactions.c.account_id == models.accounts.c.id
            )
        )
        .where(
            models.transactions.c.household_id == household_id,
            models.transactions.c.amount_minor > 0,
            models.transactions.c.occurred_at >= since,
            models.accounts.c.type == "checking",
        )
        .order_by(models.transactions.c.occurred_at)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return [tuple(row) for row in rows]


def list_income_categorized_transactions(
    engine: Engine, household_id: str, *, since: date
) -> list[tuple[str, date, int, str, str | None, str | None, str, str | None]]:
    """Inflow rows the household filed under the Income category — from ANY asset
    account type, so RSU/ESPP deposits that land in a brokerage are seen too
    (ADR 0054). Liability accounts are excluded: a positive posting on a loan or
    lease is a debt PAYMENT credit, never income (ADR 0055). Same tuple shape as
    list_income_detection_transactions."""
    income_ids = select(models.transaction_categories.c.id).where(
        (models.transaction_categories.c.household_id == household_id)
        & (func.lower(models.transaction_categories.c.name).in_(INCOME_CATEGORY_NAMES))
    )
    query = (
        select(
            models.transactions.c.id,
            models.transactions.c.occurred_at,
            models.transactions.c.amount_minor,
            models.transactions.c.currency,
            models.transactions.c.merchant,
            models.transactions.c.description,
            models.accounts.c.name,
            models.accounts.c.institution,
        )
        .select_from(
            models.transactions.join(
                models.accounts, models.transactions.c.account_id == models.accounts.c.id
            )
        )
        .where(
            models.transactions.c.household_id == household_id,
            models.transactions.c.amount_minor > 0,
            models.transactions.c.occurred_at >= since,
            models.transactions.c.category_id.in_(income_ids),
            models.accounts.c.type.notin_(tuple(LIABILITY_ACCOUNT_TYPES)),
        )
        .order_by(models.transactions.c.occurred_at)
    )
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(query).all()]


def list_household_outflows(
    engine: Engine, household_id: str, *, since: date
) -> list[tuple[date, int]]:
    """(occurred_at, positive amount) of every outflow across ALL accounts.

    Used to unmask internal transfers (M63): a checking inflow whose amount
    left a sibling account around the same time is money movement, not income.
    """
    query = select(
        models.transactions.c.occurred_at,
        -models.transactions.c.amount_minor,
    ).where(
        models.transactions.c.household_id == household_id,
        models.transactions.c.amount_minor < 0,
        models.transactions.c.occurred_at >= since,
    )
    with engine.connect() as conn:
        return [(row[0], int(row[1])) for row in conn.execute(query).all()]


def list_transactions_for_indexing(
    engine: Engine, household_id: str, *, since: date
) -> list[tuple[str, date, int, str, str | None, str | None, str]]:
    """All of a household's transactions in the window, for vector indexing (M69).

    Returns (id, occurred_at, amount_minor, currency, merchant, description,
    account_name).
    """
    query = (
        select(
            models.transactions.c.id,
            models.transactions.c.occurred_at,
            models.transactions.c.amount_minor,
            models.transactions.c.currency,
            models.transactions.c.merchant,
            models.transactions.c.description,
            models.accounts.c.name,
        )
        .select_from(
            models.transactions.join(
                models.accounts, models.transactions.c.account_id == models.accounts.c.id
            )
        )
        .where(
            models.transactions.c.household_id == household_id,
            models.transactions.c.occurred_at >= since,
        )
        .order_by(models.transactions.c.occurred_at)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return [tuple(row) for row in rows]


def list_income_overrides(engine: Engine, household_id: str) -> dict[str, str]:
    """transaction_id -> verdict ("include" | "exclude")."""
    query = select(
        models.income_transaction_overrides.c.transaction_id,
        models.income_transaction_overrides.c.verdict,
    ).where(models.income_transaction_overrides.c.household_id == household_id)
    with engine.connect() as conn:
        return {row[0]: row[1] for row in conn.execute(query).all()}


def set_income_override(
    engine: Engine, household_id: str, transaction_id: str, verdict: str
) -> bool:
    """Upsert an include/exclude verdict; "clear" removes it.

    Returns False when the transaction does not belong to the household.
    """
    with engine.begin() as conn:
        owned = conn.execute(
            select(models.transactions.c.id).where(
                models.transactions.c.household_id == household_id,
                models.transactions.c.id == transaction_id,
            )
        ).first()
        if owned is None:
            return False
        existing = conn.execute(
            select(models.income_transaction_overrides.c.id).where(
                models.income_transaction_overrides.c.household_id == household_id,
                models.income_transaction_overrides.c.transaction_id == transaction_id,
            )
        ).first()
        if verdict == "clear":
            if existing is not None:
                conn.execute(
                    delete(models.income_transaction_overrides).where(
                        models.income_transaction_overrides.c.id == existing.id
                    )
                )
            return True
        if existing is not None:
            conn.execute(
                update(models.income_transaction_overrides)
                .where(models.income_transaction_overrides.c.id == existing.id)
                .values(verdict=verdict)
            )
        else:
            conn.execute(
                insert(models.income_transaction_overrides).values(
                    id=new_id(),
                    household_id=household_id,
                    transaction_id=transaction_id,
                    verdict=verdict,
                    created_at=utcnow(),
                )
            )
    return True


# --- M57: household memory (ADR 0016) ----------------------------------------

# Internal marker key recording that the one-time backfill ran; never listed.
MEMORY_BACKFILL_MARKER_KEY = "_backfill_done"

MEMORY_VALUE_MAX_LENGTH = 500


@dataclass(frozen=True, slots=True)
class HouseholdMemoryRecord:
    id: str
    household_id: str
    key: str
    value: str
    source: str  # "chat" | "manual" | "system"
    source_conversation_id: str | None
    created_at: datetime
    updated_at: datetime


def _memory_from_row(row: Any) -> HouseholdMemoryRecord:
    return HouseholdMemoryRecord(
        id=row["id"],
        household_id=row["household_id"],
        key=row["key"],
        value=row["value"],
        source=row["source"],
        source_conversation_id=row["source_conversation_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_household_memories(engine: Engine, household_id: str) -> list[HouseholdMemoryRecord]:
    """All remembered facts, oldest first. Internal marker rows are excluded."""
    query = (
        select(models.household_memories)
        .where(
            models.household_memories.c.household_id == household_id,
            models.household_memories.c.source != "system",
        )
        .order_by(models.household_memories.c.created_at)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_memory_from_row(row) for row in rows]


def upsert_household_memory(
    engine: Engine,
    household_id: str,
    key: str,
    value: str,
    *,
    source: str = "chat",
    source_conversation_id: str | None = None,
) -> HouseholdMemoryRecord:
    """Insert a fact, or update the existing fact with the same key.

    Stable keys are the dedupe mechanism: "we eat out 5 times a week now"
    updates eating_out_frequency instead of piling up contradictions.
    """
    value = value[:MEMORY_VALUE_MAX_LENGTH]
    now = utcnow()
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.household_memories.c.id).where(
                models.household_memories.c.household_id == household_id,
                models.household_memories.c.key == key,
            )
        ).first()
        if existing is not None:
            conn.execute(
                update(models.household_memories)
                .where(models.household_memories.c.id == existing.id)
                .values(
                    value=value,
                    source=source,
                    source_conversation_id=source_conversation_id,
                    updated_at=now,
                )
            )
            memory_id = existing.id
        else:
            memory_id = new_id()
            conn.execute(
                insert(models.household_memories).values(
                    id=memory_id,
                    household_id=household_id,
                    key=key,
                    value=value,
                    source=source,
                    source_conversation_id=source_conversation_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        row = (
            conn.execute(
                select(models.household_memories).where(
                    models.household_memories.c.id == memory_id
                )
            )
            .mappings()
            .one()
        )
    return _memory_from_row(row)


# --- ADR 0040: idle-time study of the transaction history ---------------------


@dataclass(frozen=True, slots=True)
class StudyMonthRecord:
    household_id: str
    month: str  # YYYY-MM
    digest_hash: str
    insight_count: int
    model: str | None
    studied_at: datetime


def list_study_months(engine: Engine, household_id: str) -> list[StudyMonthRecord]:
    query = (
        select(models.study_months)
        .where(models.study_months.c.household_id == household_id)
        .order_by(models.study_months.c.month)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        StudyMonthRecord(
            household_id=row["household_id"],
            month=row["month"],
            digest_hash=row["digest_hash"],
            insight_count=row["insight_count"],
            model=row["model"],
            studied_at=row["studied_at"],
        )
        for row in rows
    ]


def upsert_study_month(
    engine: Engine,
    household_id: str,
    month: str,
    *,
    digest_hash: str,
    insight_count: int,
    model: str | None,
) -> None:
    now = utcnow()
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.study_months.c.id).where(
                models.study_months.c.household_id == household_id,
                models.study_months.c.month == month,
            )
        ).first()
        if existing is not None:
            conn.execute(
                update(models.study_months)
                .where(models.study_months.c.id == existing.id)
                .values(
                    digest_hash=digest_hash,
                    insight_count=insight_count,
                    model=model,
                    studied_at=now,
                )
            )
        else:
            conn.execute(
                insert(models.study_months).values(
                    id=new_id(),
                    household_id=household_id,
                    month=month,
                    digest_hash=digest_hash,
                    insight_count=insight_count,
                    model=model,
                    studied_at=now,
                )
            )


def last_chat_message_at(engine: Engine, household_id: str) -> datetime | None:
    """When anyone in the household last talked to the advisor — the study job's
    idleness signal (a recent chat means the runtime is busy serving a human)."""
    query = select(func.max(models.conversation_messages.c.created_at)).where(
        models.conversation_messages.c.conversation_id.in_(
            select(models.conversations.c.id).where(
                models.conversations.c.household_id == household_id
            )
        )
    )
    with engine.connect() as conn:
        return conn.execute(query).scalar_one_or_none()


def list_study_insights(engine: Engine, household_id: str) -> list[HouseholdMemoryRecord]:
    """What the study job has learned, freshest first."""
    query = (
        select(models.household_memories)
        .where(
            models.household_memories.c.household_id == household_id,
            models.household_memories.c.source == "study",
        )
        .order_by(models.household_memories.c.updated_at.desc())
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [_memory_from_row(row) for row in rows]


# --- ADR 0044: advisor answer feedback --------------------------------------


@dataclass(frozen=True, slots=True)
class AdvisorFeedbackRecord:
    id: str
    household_id: str
    recommendation_id: str
    rating: str  # "up" | "down"
    note: str | None
    answer: str  # the rated advisor answer, joined in for the review pass


def recommendation_belongs_to_household(
    engine: Engine, household_id: str, recommendation_id: str
) -> bool:
    query = select(models.recommendations.c.id).where(
        models.recommendations.c.id == recommendation_id,
        models.recommendations.c.household_id == household_id,
    )
    with engine.connect() as conn:
        return conn.execute(query).first() is not None


def upsert_advisor_feedback(
    engine: Engine,
    household_id: str,
    recommendation_id: str,
    user_id: str,
    rating: str,
    note: str | None,
) -> None:
    """Record a member's rating of an advisor answer, or update their existing
    one. Re-rating resets `reviewed` so the study job takes another look."""
    now = utcnow()
    with engine.begin() as conn:
        existing = conn.execute(
            select(models.advisor_feedback.c.id).where(
                models.advisor_feedback.c.recommendation_id == recommendation_id,
                models.advisor_feedback.c.created_by_user_id == user_id,
            )
        ).first()
        if existing is not None:
            conn.execute(
                update(models.advisor_feedback)
                .where(models.advisor_feedback.c.id == existing.id)
                .values(rating=rating, note=note, reviewed=False, updated_at=now)
            )
        else:
            conn.execute(
                insert(models.advisor_feedback).values(
                    id=new_id(),
                    household_id=household_id,
                    recommendation_id=recommendation_id,
                    created_by_user_id=user_id,
                    rating=rating,
                    note=note,
                    reviewed=False,
                    created_at=now,
                    updated_at=now,
                )
            )


def list_unreviewed_feedback(
    engine: Engine, household_id: str, limit: int = 20
) -> list[AdvisorFeedbackRecord]:
    """Feedback the study job hasn't distilled a lesson from yet, with the rated
    answer joined in. Oldest first, so the queue drains in order."""
    fb = models.advisor_feedback
    query = (
        select(
            fb.c.id,
            fb.c.household_id,
            fb.c.recommendation_id,
            fb.c.rating,
            fb.c.note,
            models.recommendations.c.answer,
        )
        .join(models.recommendations, models.recommendations.c.id == fb.c.recommendation_id)
        .where(fb.c.household_id == household_id, fb.c.reviewed.is_(False))
        .order_by(fb.c.created_at)
        .limit(limit)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [
        AdvisorFeedbackRecord(
            id=row["id"],
            household_id=row["household_id"],
            recommendation_id=row["recommendation_id"],
            rating=row["rating"],
            note=row["note"],
            answer=row["answer"],
        )
        for row in rows
    ]


def mark_feedback_reviewed(engine: Engine, feedback_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(models.advisor_feedback)
            .where(models.advisor_feedback.c.id == feedback_id)
            .values(reviewed=True, updated_at=utcnow())
        )


def delete_household_memory(engine: Engine, household_id: str, memory_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            delete(models.household_memories).where(
                models.household_memories.c.household_id == household_id,
                models.household_memories.c.id == memory_id,
                models.household_memories.c.source != "system",
            )
        )
    return result.rowcount > 0


def memory_backfill_done(engine: Engine, household_id: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            select(models.household_memories.c.id).where(
                models.household_memories.c.household_id == household_id,
                models.household_memories.c.key == MEMORY_BACKFILL_MARKER_KEY,
            )
        ).first()
    return row is not None


def mark_memory_backfill_done(engine: Engine, household_id: str) -> None:
    upsert_household_memory(
        engine, household_id, MEMORY_BACKFILL_MARKER_KEY, "done", source="system"
    )


# --- M27: institution connections + dedupe (ADR 0015) -------------------------


@dataclass(frozen=True, slots=True)
class InstitutionConnectionRecord:
    id: str
    household_id: str
    provider: str
    display_name: str
    access_url_encrypted: str
    status: str
    last_synced_at: datetime | None
    last_sync_error: str | None
    created_at: datetime


def _connection_from_row(row: Any) -> InstitutionConnectionRecord:
    return InstitutionConnectionRecord(
        id=row["id"],
        household_id=row["household_id"],
        provider=row["provider"],
        display_name=row["display_name"],
        access_url_encrypted=row["access_url_encrypted"],
        status=row["status"],
        last_synced_at=_as_aware(row["last_synced_at"]) if row["last_synced_at"] else None,
        last_sync_error=row["last_sync_error"],
        created_at=_as_aware(row["created_at"]),
    )


def create_institution_connection(
    engine: Engine,
    household_id: str,
    provider: str,
    display_name: str,
    access_url_encrypted: str,
) -> InstitutionConnectionRecord:
    connection_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.institution_connections).values(
                id=connection_id,
                household_id=household_id,
                provider=provider,
                display_name=display_name,
                access_url_encrypted=access_url_encrypted,
                status="active",
                created_at=utcnow(),
            )
        )
    record = get_institution_connection(engine, household_id, connection_id)
    assert record is not None
    return record


def get_institution_connection(
    engine: Engine, household_id: str, connection_id: str
) -> InstitutionConnectionRecord | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(models.institution_connections).where(
                    models.institution_connections.c.id == connection_id,
                    models.institution_connections.c.household_id == household_id,
                )
            )
            .mappings()
            .first()
        )
    return _connection_from_row(row) if row else None


def list_institution_connections(
    engine: Engine, household_id: str
) -> list[InstitutionConnectionRecord]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(models.institution_connections)
                .where(models.institution_connections.c.household_id == household_id)
                .order_by(models.institution_connections.c.created_at)
            )
            .mappings()
            .all()
        )
    return [_connection_from_row(row) for row in rows]


def list_all_institution_connections(engine: Engine) -> list[InstitutionConnectionRecord]:
    """Every active connection across households — for the scheduled sync job."""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(models.institution_connections).where(
                    models.institution_connections.c.status == "active"
                )
            )
            .mappings()
            .all()
        )
    return [_connection_from_row(row) for row in rows]


def delete_institution_connection(
    engine: Engine, household_id: str, connection_id: str
) -> bool:
    with engine.begin() as conn:
        conn.execute(
            delete(models.connection_accounts).where(
                models.connection_accounts.c.connection_id == connection_id
            )
        )
        result = conn.execute(
            delete(models.institution_connections).where(
                models.institution_connections.c.id == connection_id,
                models.institution_connections.c.household_id == household_id,
            )
        )
    return result.rowcount > 0


def record_connection_sync(engine: Engine, connection_id: str, error: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(models.institution_connections)
            .where(models.institution_connections.c.id == connection_id)
            .values(last_synced_at=utcnow(), last_sync_error=error)
        )


def get_or_create_connection_account(
    engine: Engine,
    household_id: str,
    connection_id: str,
    external_account_id: str,
    name: str,
    currency: str,
    account_type: str = "checking",
    institution: str | None = None,
) -> str:
    """The local account mapped to a provider account, auto-created on first sight.

    account_type only applies at creation; an existing mapping is returned as-is
    so manual retyping from the Accounts page is never overwritten by a sync. The
    institution, in contrast, is the provider's own fact and is refreshed every
    sync (including a backfill of accounts linked before it was captured).
    """
    with engine.connect() as conn:
        row = conn.execute(
            select(models.connection_accounts.c.account_id).where(
                models.connection_accounts.c.connection_id == connection_id,
                models.connection_accounts.c.external_account_id == external_account_id,
            )
        ).first()
    if row is not None:
        account_id = row[0]
        if institution:
            with engine.begin() as conn:
                conn.execute(
                    update(models.accounts)
                    .where(models.accounts.c.id == account_id)
                    .values(institution=institution)
                )
        return account_id

    account = create_account(engine, household_id, name, account_type, currency)
    with engine.begin() as conn:
        if institution:
            conn.execute(
                update(models.accounts)
                .where(models.accounts.c.id == account.id)
                .values(institution=institution)
            )
        conn.execute(
            insert(models.connection_accounts).values(
                id=new_id(),
                connection_id=connection_id,
                external_account_id=external_account_id,
                account_id=account.id,
                created_at=utcnow(),
            )
        )
    return account.id


def create_transaction_deduped(
    engine: Engine,
    household_id: str,
    account_id: str,
    occurred_at: date,
    amount_minor: int,
    currency: str,
    merchant: str | None,
    description: str | None,
    import_source: str | None,
    external_id: str | None = None,
    import_id: str | None = None,
    review_state: str = "reviewed",
) -> bool:
    """Insert a transaction unless it is a duplicate (ADR 0015). True if inserted.

    Provider path: skip when (account_id, external_id) already exists — hard
    idempotency, backed by the unique index. Fallback path (no external id):
    skip when the content hash matches an existing row in the account.
    """
    from family_cfo_api.banksync import compute_import_hash

    import_hash = compute_import_hash(account_id, occurred_at, amount_minor, merchant)
    with engine.begin() as conn:
        if external_id is not None:
            existing = conn.execute(
                select(models.transactions.c.id).where(
                    models.transactions.c.account_id == account_id,
                    models.transactions.c.external_id == external_id,
                )
            ).first()
            if existing is not None:
                return False
        else:
            existing = conn.execute(
                select(models.transactions.c.id).where(
                    models.transactions.c.account_id == account_id,
                    models.transactions.c.import_hash == import_hash,
                )
            ).first()
            if existing is not None:
                return False
        conn.execute(
            insert(models.transactions).values(
                id=new_id(),
                household_id=household_id,
                account_id=account_id,
                occurred_at=occurred_at,
                amount_minor=amount_minor,
                currency=currency,
                merchant=merchant,
                category_id=None,
                description=description,
                import_source=import_source,
                import_id=import_id,
                possible_duplicate=False,
                review_state=review_state,
                external_id=external_id,
                import_hash=import_hash,
                created_at=utcnow(),
            )
        )
    return True


def any_household_exists(engine: Engine) -> bool:
    """M32 single-tenant lockout: is this server already claimed by a family?"""
    with engine.connect() as conn:
        return conn.execute(select(models.households.c.id).limit(1)).first() is not None


@dataclass(frozen=True, slots=True)
class AccountConnectionInfo:
    institution: str
    last_synced_at: datetime | None


def account_connection_map(engine: Engine, household_id: str) -> dict[str, AccountConnectionInfo]:
    """account_id -> linked institution name + last sync (M33 accounts page)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                models.connection_accounts.c.account_id,
                models.institution_connections.c.display_name,
                models.institution_connections.c.last_synced_at,
            )
            .select_from(
                models.connection_accounts.join(
                    models.institution_connections,
                    models.connection_accounts.c.connection_id
                    == models.institution_connections.c.id,
                )
            )
            .where(models.institution_connections.c.household_id == household_id)
        ).all()
    return {
        row[0]: AccountConnectionInfo(
            institution=row[1],
            last_synced_at=_as_aware(row[2]) if row[2] else None,
        )
        for row in rows
    }
