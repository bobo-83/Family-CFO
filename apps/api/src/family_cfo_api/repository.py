from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

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
            select(models.household_memberships.c.role).where(
                models.household_memberships.c.household_id == session_row["household_id"],
                models.household_memberships.c.user_id == session_row["user_id"],
            )
        ).first()

    if role_row is None:
        return None

    return SessionContext(
        user_id=session_row["user_id"],
        household_id=session_row["household_id"],
        role=role_row[0],
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
        device_id=device_id, access_token=access_token, expires_at=expires_at
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


# --- Household and accounts ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class HouseholdRecord:
    id: str
    display_name: str
    base_currency: str


@dataclass(frozen=True, slots=True)
class AccountBalanceRecord:
    account_id: str
    name: str
    account_type: str
    currency: str
    balance_minor: int


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
        id=row["id"], display_name=row["display_name"], base_currency=row["base_currency"]
    )


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


@dataclass(frozen=True, slots=True)
class RecurringRecord:
    id: str
    name: str
    amount_minor: int
    currency: str
    frequency: str


def list_transactions(
    engine: Engine, household_id: str, limit: int = 200
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
        .where(models.transactions.c.household_id == household_id)
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
            description=row.description,
        )
        for row in rows
    ]


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
                current_minor=0,
                currency=currency,
                target_date=target_date,
                priority=priority,
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
    )


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
                category_id=None,
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
) -> None:
    values: dict[str, Any] = {"status": status}
    if storage_path is not None:
        values["storage_path"] = storage_path
    if size_bytes is not None:
        values["size_bytes"] = size_bytes
    if error_message is not None:
        values["error_message"] = error_message
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
