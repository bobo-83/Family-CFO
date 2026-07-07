"""Synthetic demo household fixtures for local development and tests.

Never seed real financial data here; every value is invented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from family_cfo_api import models, security
from family_cfo_api.db import metadata
from family_cfo_api.repository import new_id

DEMO_HOUSEHOLD_ID = "11111111-1111-1111-1111-111111111111"
DEMO_USER_ID = "22222222-2222-2222-2222-222222222222"
DEMO_MEMBERSHIP_ID = "22222222-2222-2222-2222-333333333333"
DEMO_USER_EMAIL = "demo@family-cfo.local"
DEMO_USER_PASSWORD = "demo-password-123"
DEMO_VIEWER_USER_ID = "99999999-9999-9999-9999-999999999999"
DEMO_VIEWER_MEMBERSHIP_ID = "99999999-9999-9999-9999-888888888888"
DEMO_VIEWER_EMAIL = "viewer@family-cfo.local"
DEMO_VIEWER_PASSWORD = "viewer-password-123"
DEMO_CHECKING_ACCOUNT_ID = "33333333-3333-3333-3333-333333333333"
DEMO_SAVINGS_ACCOUNT_ID = "44444444-4444-4444-4444-444444444444"
DEMO_MORTGAGE_ACCOUNT_ID = "55555555-5555-5555-5555-555555555555"
DEMO_GROCERIES_CATEGORY_ID = "66666666-6666-6666-6666-666666666666"


def create_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def seed_demo_household(engine: Engine) -> None:
    now = datetime.now(UTC)
    today = date.today()

    with engine.begin() as conn:
        conn.execute(
            insert(models.households).values(
                id=DEMO_HOUSEHOLD_ID,
                display_name="The Demo Family",
                base_currency="USD",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(models.users).values(
                id=DEMO_USER_ID,
                email=DEMO_USER_EMAIL,
                password_hash=security.hash_password(DEMO_USER_PASSWORD),
                display_name="Demo Owner",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(models.household_memberships).values(
                id=DEMO_MEMBERSHIP_ID,
                household_id=DEMO_HOUSEHOLD_ID,
                user_id=DEMO_USER_ID,
                role="owner",
                created_at=now,
            )
        )
        conn.execute(
            insert(models.users).values(
                id=DEMO_VIEWER_USER_ID,
                email=DEMO_VIEWER_EMAIL,
                password_hash=security.hash_password(DEMO_VIEWER_PASSWORD),
                display_name="Demo Viewer",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(models.household_memberships).values(
                id=DEMO_VIEWER_MEMBERSHIP_ID,
                household_id=DEMO_HOUSEHOLD_ID,
                user_id=DEMO_VIEWER_USER_ID,
                role="viewer",
                created_at=now,
            )
        )
        conn.execute(
            insert(models.accounts),
            [
                dict(
                    id=DEMO_CHECKING_ACCOUNT_ID,
                    household_id=DEMO_HOUSEHOLD_ID,
                    name="Checking",
                    type="checking",
                    currency="USD",
                    created_at=now,
                    updated_at=now,
                ),
                dict(
                    id=DEMO_SAVINGS_ACCOUNT_ID,
                    household_id=DEMO_HOUSEHOLD_ID,
                    name="Savings",
                    type="savings",
                    currency="USD",
                    created_at=now,
                    updated_at=now,
                ),
                dict(
                    id=DEMO_MORTGAGE_ACCOUNT_ID,
                    household_id=DEMO_HOUSEHOLD_ID,
                    name="Mortgage",
                    type="mortgage",
                    currency="USD",
                    created_at=now,
                    updated_at=now,
                ),
            ],
        )
        conn.execute(
            insert(models.account_balances),
            [
                dict(
                    id=new_id(),
                    account_id=DEMO_CHECKING_ACCOUNT_ID,
                    balance_minor=500_000,
                    as_of=now,
                    created_at=now,
                ),
                dict(
                    id=new_id(),
                    account_id=DEMO_SAVINGS_ACCOUNT_ID,
                    balance_minor=1_500_000,
                    as_of=now,
                    created_at=now,
                ),
                dict(
                    id=new_id(),
                    account_id=DEMO_MORTGAGE_ACCOUNT_ID,
                    balance_minor=-300_000_000,
                    as_of=now,
                    created_at=now,
                ),
            ],
        )
        conn.execute(
            insert(models.transaction_categories).values(
                id=DEMO_GROCERIES_CATEGORY_ID,
                household_id=DEMO_HOUSEHOLD_ID,
                name="Groceries",
                parent_category_id=None,
                created_at=now,
            )
        )
        conn.execute(
            insert(models.transactions),
            [
                dict(
                    id=new_id(),
                    household_id=DEMO_HOUSEHOLD_ID,
                    account_id=DEMO_CHECKING_ACCOUNT_ID,
                    occurred_at=today,
                    amount_minor=-12_000,
                    currency="USD",
                    merchant="Whole Foods",
                    category_id=DEMO_GROCERIES_CATEGORY_ID,
                    description=None,
                    import_source=None,
                    review_state="reviewed",
                    created_at=now,
                ),
                dict(
                    id=new_id(),
                    household_id=DEMO_HOUSEHOLD_ID,
                    account_id=DEMO_CHECKING_ACCOUNT_ID,
                    occurred_at=today - timedelta(days=3),
                    amount_minor=-5_500,
                    currency="USD",
                    merchant="Trader Joe's",
                    category_id=DEMO_GROCERIES_CATEGORY_ID,
                    description=None,
                    import_source=None,
                    review_state="reviewed",
                    created_at=now,
                ),
            ],
        )
        conn.execute(
            insert(models.bills),
            [
                dict(
                    id=new_id(),
                    household_id=DEMO_HOUSEHOLD_ID,
                    account_id=DEMO_MORTGAGE_ACCOUNT_ID,
                    name="Mortgage payment",
                    amount_minor=200_000,
                    currency="USD",
                    frequency="monthly",
                    next_due_date=today + timedelta(days=15),
                    category_id=None,
                    created_at=now,
                    updated_at=now,
                ),
                dict(
                    id=new_id(),
                    household_id=DEMO_HOUSEHOLD_ID,
                    account_id=None,
                    name="Internet",
                    amount_minor=8_000,
                    currency="USD",
                    frequency="monthly",
                    next_due_date=today + timedelta(days=10),
                    category_id=None,
                    created_at=now,
                    updated_at=now,
                ),
            ],
        )
        conn.execute(
            insert(models.income_sources).values(
                id=new_id(),
                household_id=DEMO_HOUSEHOLD_ID,
                name="Salary",
                amount_minor=600_000,
                currency="USD",
                frequency="monthly",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(models.goals).values(
                id=new_id(),
                household_id=DEMO_HOUSEHOLD_ID,
                name="Emergency fund",
                type="emergency_fund",
                target_minor=1_800_000,
                current_minor=1_500_000,
                currency="USD",
                target_date=None,
                priority=1,
                created_at=now,
                updated_at=now,
            )
        )
