from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, insert, select
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
        row = conn.execute(
            select(models.users).where(models.users.c.email == email)
        ).mappings().first()

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
) -> str:
    session_id = new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.auth_sessions).values(
                id=session_id,
                user_id=user_id,
                household_id=household_id,
                token_hash=token_hash,
                created_at=utcnow(),
                expires_at=expires_at,
                revoked_at=None,
            )
        )
    return session_id


def get_session_context(engine: Engine, token_hash: str) -> SessionContext | None:
    with engine.connect() as conn:
        session_row = conn.execute(
            select(models.auth_sessions).where(models.auth_sessions.c.token_hash == token_hash)
        ).mappings().first()

        if session_row is None or session_row["revoked_at"] is not None:
            return None

        if _as_aware(session_row["expires_at"]) < utcnow():
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
        row = conn.execute(
            select(models.households).where(models.households.c.id == household_id)
        ).mappings().first()

    if row is None:
        return None

    return HouseholdRecord(id=row["id"], display_name=row["display_name"], base_currency=row["base_currency"])


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


def list_transactions(engine: Engine, household_id: str, limit: int = 200) -> list[TransactionRecord]:
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
        row = conn.execute(
            select(models.goals).where(models.goals.c.id == goal_id)
        ).mappings().first()

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
                created_at=utcnow(),
            )
        )
    return recommendation_id
