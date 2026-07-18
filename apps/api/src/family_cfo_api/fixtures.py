"""Synthetic demo household fixtures for local development and tests.

Never seed real financial data here; every value is invented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from family_cfo_api import models, repository, security
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
        preset_role_ids = repository._seed_preset_roles(conn, DEMO_HOUSEHOLD_ID, now)
        conn.execute(
            insert(models.household_memberships).values(
                id=DEMO_MEMBERSHIP_ID,
                household_id=DEMO_HOUSEHOLD_ID,
                user_id=DEMO_USER_ID,
                role="owner",
                role_id=preset_role_ids["Admin"],
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
                role_id=preset_role_ids["Viewer"],
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


# --- M74: showcase data ---------------------------------------------------------

SHOWCASE_MARKER_ACCOUNT = "High-Yield Savings (showcase)"


def seed_showcase_data(engine: Engine) -> bool:  # noqa: PLR0915 - one linear script
    """Rich, additive demo data exercising every product scenario (M74).

    Idempotent: no-op (returns False) when the showcase marker account already
    exists. Layers ON TOP of ``seed_demo_household`` — the minimal fixture the
    test suite depends on stays untouched.
    """
    from family_cfo_api import repository

    hh = DEMO_HOUSEHOLD_ID
    names = [b.name for b in repository.list_account_balances(engine, hh)]
    if SHOWCASE_MARKER_ACCOUNT in names:
        return False

    today = date.today()

    def txn(account_id: str, days_ago: int, amount_minor: int, merchant: str,
            description: str | None = None, category_id: str | None = None) -> None:
        repository.create_transaction(
            engine, household_id=hh, account_id=account_id,
            occurred_at=today - timedelta(days=days_ago),
            amount_minor=amount_minor, currency="USD", merchant=merchant,
            description=description, import_source=None, import_id=None,
            review_state="reviewed", category_id=category_id,
        )

    # --- Accounts across the spectrum (M33/M35 grouping) ---
    checking = repository.create_account(engine, hh, "Rewards Checking (showcase)", "checking", "USD")
    savings = repository.create_account(engine, hh, SHOWCASE_MARKER_ACCOUNT, "savings", "USD")
    brokerage = repository.create_account(engine, hh, "Brokerage (showcase)", "brokerage", "USD")
    k401 = repository.create_account(engine, hh, "401k (showcase)", "retirement", "USD")
    plan529 = repository.create_account(engine, hh, "College 529 (showcase)", "529", "USD")
    card = repository.create_account(
        engine, hh, "Sapphire Card (showcase)", "credit_card", "USD",
        annual_interest_rate=0.239, minimum_payment_minor=15_000,
    )
    mortgage = repository.create_account(
        engine, hh, "Mortgage (showcase)", "mortgage", "USD",
        annual_interest_rate=0.052, minimum_payment_minor=385_000,
    )
    for account, balance in (
        (checking, 2_450_000), (savings, 6_500_000), (brokerage, 21_500_000),
        (k401, 61_000_000), (plan529, 5_400_000), (card, -412_500),
    ):
        repository.record_account_balance(engine, account.id, balance)
    repository.record_account_balance(engine, mortgage.id, -52_000_000)

    # Emergency fund designation (M36): the savings account IS the fund.
    repository.update_account(engine, hh, savings.id, emergency_fund_percent=100.0)

    # --- Categories + budgets in all three states (M45/M46) ---
    groceries = repository.create_category(engine, hh, "Groceries (showcase)")
    dining = repository.create_category(engine, hh, "Dining (showcase)")
    gas = repository.create_category(engine, hh, "Gas (showcase)")
    repository.create_budget(engine, hh, category_id=groceries.id, limit_minor=80_000, currency="USD")
    repository.create_budget(engine, hh, category_id=dining.id, limit_minor=30_000, currency="USD")
    repository.create_budget(engine, hh, category_id=gas.id, limit_minor=25_000, currency="USD")

    # --- ~7 months of activity ---
    # Biweekly paycheck hidden under a generic transfer label (M65 clustering),
    # plus big one-offs sharing it.
    for i in range(14):
        txn(checking.id, 8 + 14 * i, 421_137 + i, "Online Transfer",
            "Online Transfer / Payment: Credit")
    txn(checking.id, 45, 2_312_400, "Online Transfer", "Online Transfer / Payment: Credit")
    # Quarterly RSU net proceeds landing in checking (M73 corroboration).
    txn(checking.id, 20, 2_874_060, "MORGAN STANLEY ACH", "RSU net proceeds Q2")
    txn(checking.id, 111, 2_641_220, "MORGAN STANLEY ACH", "RSU net proceeds Q1")
    # Matched checking->savings transfer pairs (M63 suppression: money movement).
    for days, amount in ((12, 500_000), (40, 500_000), (70, 750_000)):
        txn(checking.id, days, -amount, "Internal Transfer", "Internal Transfer Debit: Savings")
        txn(savings.id, days, amount, "Internal Transfer",
            "Internal Transfer Credit: Checking -0603")

    # Recurring charges. Two become EXISTING bills (one stale -> M59 drift);
    # three stay undetected -> M58 suggestions.
    for month in range(6):
        txn(card.id, 4 + 30 * month, -1_549, "NETFLIX.COM *4029")            # bill @ stale 12.99
        txn(checking.id, 9 + 30 * month, -8_000, "COMCAST INTERNET")          # bill, current
        txn(checking.id, 14 + 30 * month, -12_000 - month * 350, "PG&E UTILITY")  # suggestion
        txn(card.id, 2 + 30 * month, -14_800, "Goldfish Swim School")          # suggestion
        txn(card.id, 17 + 30 * month, -1_099, "SPOTIFY USA")                    # suggestion
    repository.create_bill(engine, hh, "NETFLIX.COM", 1_299, "USD", "monthly",
                           next_due_date=today + timedelta(days=9))
    repository.create_bill(engine, hh, "COMCAST INTERNET", 8_000, "USD", "monthly",
                           next_due_date=today + timedelta(days=4))

    # Categorized spending, this month AND last month (M42 insights, M46 states):
    # groceries "warning" (~86%), dining "over", gas "under".
    for days, amount in ((3, -21_400), (8, -18_300), (15, -16_900), (22, -12_100)):
        txn(card.id, days, amount, "WHOLE FOODS MKT", category_id=groceries.id)
        txn(card.id, days + 30, amount - 2_000, "WHOLE FOODS MKT", category_id=groceries.id)
    for days, amount in ((5, -8_200), (11, -9_900), (19, -8_400), (25, -7_300)):
        txn(card.id, days, amount, "LOCAL BISTRO", category_id=dining.id)
        txn(card.id, days + 30, amount + 1_500, "LOCAL BISTRO", category_id=dining.id)
    for days in (6, 20):
        txn(card.id, days, -6_200, "SHELL OIL", category_id=gas.id)
        txn(card.id, days + 30, -6_800, "SHELL OIL", category_id=gas.id)

    # --- Goals with progress (M41) ---
    vacation = repository.create_goal(engine, hh, "Japan trip 2027", "vacation",
                                      1_200_000, "USD", date(today.year + 1, 6, 1), 1)
    with engine.begin() as conn:
        conn.execute(
            models.goals.update()
            .where(models.goals.c.id == vacation.id)
            .values(current_minor=780_000)
        )

    # --- Income source (cash-flow card) ---
    repository.create_income_source(engine, hh, "Salary (take-home)", 842_274, "USD", "biweekly")

    # --- Compensation profile (M73): quarterly RSUs + 25% bonus + W2 actuals ---
    repository.create_income_profile(
        engine, hh, label="Alex (showcase)",
        base_salary_minor=20_000_000, rsu_annual_minor=16_000_000,
        rsu_frequency="quarterly", rsu_next_vest_date=today + timedelta(days=32),
        bonus_percent=25.0, bonus_month=12,
        w2_year=today.year - 1, w2_wages_minor=38_541_260, w2_withheld_minor=7_890_315,
    )

    # --- Tax settings (M65) + memories (M57) ---
    repository.update_tax_settings(engine, hh, tax_filing_status="married_joint",
                                   income_treated_as_net=False, state="CA")
    repository.upsert_household_memory(engine, hh, "home_city", "We live in San Jose, CA.")
    repository.upsert_household_memory(engine, hh, "kids_count",
                                       "We have two kids (ages 4 and 7).")
    repository.upsert_household_memory(engine, hh, "daycare_cost",
                                       "Daycare costs about $1,250.00 a month.")

    # --- Net-worth history (M40): a rising weekly series ---
    base_net_worth = 60_000_000
    with engine.begin() as conn:
        for week in range(10, 0, -1):
            conn.execute(
                insert(models.net_worth_snapshots).values(
                    id=new_id(),
                    household_id=hh,
                    as_of=today - timedelta(days=7 * week),
                    net_worth_minor=base_net_worth + (10 - week) * 350_000,
                    currency="USD",
                    created_at=datetime.now(UTC),
                )
            )
    return True
