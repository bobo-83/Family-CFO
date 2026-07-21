"""Synthetic demo household fixtures for local development and tests.

Never seed real financial data here; every value is invented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert, select
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

# Tables that hold the demo household's IDENTITY — kept across a data reset so
# the demo logins, roles, and paired devices keep working.
_RESET_PROTECTED_TABLES = frozenset(
    {
        "households",
        "users",
        "roles",
        "household_memberships",
        "pairing_sessions",
        "paired_devices",
        "auth_sessions",
    }
)


def reset_demo_data(engine: Engine) -> int:
    """Delete the demo household's DATA — accounts, transactions, bills, goals,
    profiles, memories, history — while keeping its identity (household, users,
    memberships, roles, sessions), so the showcase can be reseeded from scratch
    after a persona change. Returns the number of rows deleted."""
    hh = DEMO_HOUSEHOLD_ID
    deleted = 0
    with engine.begin() as conn:
        # Children before parents; a table without household_id (account_balances,
        # import_files, …) is purged through its FK to a household-scoped parent.
        for table in reversed(metadata.sorted_tables):
            if table.name in _RESET_PROTECTED_TABLES:
                continue
            if "household_id" in table.c:
                result = conn.execute(table.delete().where(table.c.household_id == hh))
                deleted += result.rowcount
                continue
            for column in table.c:
                fk = next(iter(column.foreign_keys), None)
                if fk is not None and "household_id" in fk.column.table.c:
                    parent = fk.column.table
                    ids = select(fk.column).where(parent.c.household_id == hh)
                    result = conn.execute(table.delete().where(column.in_(ids)))
                    deleted += result.rowcount
                    break
    return deleted


def seed_showcase_data(engine: Engine) -> bool:  # noqa: PLR0915 - one linear script
    """Rich, additive demo data exercising every product scenario (M74).

    The persona: a senior software engineer at Anthropic living in Austin, TX
    (single filer, no state income tax), with TWO FULL YEARS of history —
    biweekly Anthropic payroll (with a raise a year in), an Austin mortgage with
    escrowed property taxes, seasonal Austin Energy bills, H-E-B groceries,
    matched savings transfers, card payments, and net-worth history.

    Idempotent: no-op (returns False) when the showcase marker account already
    exists. Layers ON TOP of ``seed_demo_household`` — the minimal fixture the
    test suite depends on stays untouched. Run ``reset_demo_data`` first to
    rebuild from scratch.
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

    def month_of(days_ago: int) -> int:
        return (today - timedelta(days=days_ago)).month

    # --- Accounts across the spectrum (M33/M35 grouping) ---
    checking = repository.create_account(engine, hh, "Rewards Checking (showcase)", "checking", "USD")
    savings = repository.create_account(engine, hh, SHOWCASE_MARKER_ACCOUNT, "savings", "USD")
    brokerage = repository.create_account(engine, hh, "Brokerage (showcase)", "brokerage", "USD")
    k401 = repository.create_account(engine, hh, "401k (showcase)", "retirement", "USD")
    card = repository.create_account(
        engine, hh, "Sapphire Card (showcase)", "credit_card", "USD",
        annual_interest_rate=0.239, minimum_payment_minor=15_000,
    )
    mortgage = repository.create_account(
        engine, hh, "Mortgage (showcase)", "mortgage", "USD",
        annual_interest_rate=0.0625, minimum_payment_minor=388_000,
    )
    for account, balance in (
        (checking, 1_850_000), (savings, 4_800_000), (brokerage, 14_500_000),
        (k401, 28_500_000), (card, -228_700),
    ):
        repository.record_account_balance(engine, account.id, balance)
    repository.record_account_balance(engine, mortgage.id, -43_000_000)

    # Emergency fund designation (M36): the savings account IS the fund.
    repository.update_account(engine, hh, savings.id, emergency_fund_percent=100.0)

    # --- Categories + budgets in all three states (M45/M46) ---
    groceries = repository.create_category(engine, hh, "Groceries (showcase)")
    dining = repository.create_category(engine, hh, "Dining (showcase)")
    gas = repository.create_category(engine, hh, "Gas (showcase)")
    repository.create_budget(engine, hh, category_id=groceries.id, limit_minor=60_000, currency="USD")
    repository.create_budget(engine, hh, category_id=dining.id, limit_minor=40_000, currency="USD")
    repository.create_budget(engine, hh, category_id=gas.id, limit_minor=20_000, currency="USD")

    # --- TWO YEARS of activity -------------------------------------------------
    MONTHS = 24

    # Biweekly Anthropic payroll (M65 clustering); a ~4.7% raise a year in, so
    # the second year's paychecks are visibly larger than the first's.
    for i in range(53):
        days_ago = 5 + 14 * i
        net = 780_000 if days_ago <= 365 else 745_000
        txn(checking.id, days_ago, net + (i % 3), "ANTHROPIC PBC PAYROLL",
            "ANTHROPIC PBC DES:PAYROLL INDN:JORDAN CO ID:XXXXX PPD")

    # Monthly HYSA interest lands in savings (not income — savings, not checking).
    for m in range(MONTHS):
        txn(savings.id, 11 + 30 * m, 17_000 + (m % 5) * 180, "Interest Paid",
            "Interest Paid — 4.10% APY")

    # Mortgage on an Austin house: P&I + escrowed Travis County property taxes.
    for m in range(MONTHS):
        txn(checking.id, 1 + 30 * m, -388_000, "MR COOPER MORTGAGE",
            "MR COOPER DES:MTG PYMT (P&I + escrowed property tax)")

    # Austin utilities: Austin Energy runs hot in summer (A/C), Texas Gas in
    # winter; water and Google Fiber stay flat.
    summer, winter = {6, 7, 8, 9}, {11, 12, 1, 2}
    for m in range(MONTHS):
        days_energy = 9 + 30 * m
        energy = 22_400 if month_of(days_energy) in summer else 10_800
        txn(checking.id, days_energy, -energy, "AUSTIN ENERGY", "City of Austin electric")
        days_gas = 13 + 30 * m
        gas_amt = 10_600 if month_of(days_gas) in winter else 2_900
        txn(checking.id, days_gas, -gas_amt, "TEXAS GAS SERVICE")
        txn(checking.id, 16 + 30 * m, -8_500, "CITY OF AUSTIN WATER")   # suggestion
        txn(checking.id, 7 + 30 * m, -7_000, "GOOGLE FIBER")            # bill, current
    # Recurring card charges: Netflix EXISTS as a stale bill (12.99 -> drift at
    # 15.49); Spotify and the gym stay undetected -> M58 suggestions.
    for m in range(MONTHS):
        txn(card.id, 4 + 30 * m, -1_549, "NETFLIX.COM *4029")
        txn(card.id, 17 + 30 * m, -1_199, "SPOTIFY USA")                # suggestion
        txn(card.id, 2 + 30 * m, -4_500, "GOLDS GYM AUSTIN")            # suggestion
    repository.create_bill(engine, hh, "NETFLIX.COM", 1_299, "USD", "monthly",
                           next_due_date=today + timedelta(days=9))
    repository.create_bill(engine, hh, "GOOGLE FIBER", 7_000, "USD", "monthly",
                           next_due_date=today + timedelta(days=4))
    repository.create_bill(engine, hh, "MR COOPER MORTGAGE", 388_000, "USD", "monthly",
                           next_due_date=today + timedelta(days=12))

    # Matched checking->savings transfer pairs (M63 suppression: money movement).
    for m in range(MONTHS):
        days = 6 + 30 * m
        txn(checking.id, days, -150_000, "Internal Transfer",
            "Internal Transfer Debit: Savings")
        txn(savings.id, days, 150_000, "Internal Transfer",
            "Internal Transfer Credit: Checking -0603")
    # Monthly card payment — the matched pair across checking and the card.
    for m in range(MONTHS):
        days = 27 + 30 * m
        txn(checking.id, days, -230_000, "Card Payment", "Payment to Sapphire Card")
        txn(card.id, days, 230_000, "Payment Received", "Payment Received — Thank You")

    # Categorized spending across ALL 24 months (M42 insights, M46 states):
    # groceries "warning" (~88%), dining just "over", gas "under".
    for m in range(MONTHS):
        for offset, amount in ((3, -13_400), (10, -12_100), (17, -14_300), (24, -12_800)):
            txn(card.id, offset + 30 * m, amount, "H-E-B", category_id=groceries.id)
        for offset, amount, merchant in (
            (5, -8_900, "TORCHYS TACOS"),
            (12, -16_400, "UCHI AUSTIN"),
            (19, -6_500, "FRANKLIN BARBECUE"),
            (26, -9_800, "LOUNGE ON SOUTH CONGRESS"),
        ):
            txn(card.id, offset + 30 * m, amount, merchant, category_id=dining.id)
        for offset, amount, merchant in ((6, -6_400, "BUC-EE'S #22"), (20, -6_900, "SHELL OIL")):
            txn(card.id, offset + 30 * m, amount, merchant, category_id=gas.id)

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
    repository.create_income_source(engine, hh, "Anthropic salary (take-home)",
                                    780_000, "USD", "biweekly")

    # --- Compensation profile (M73): senior SWE at Anthropic. Base + equity
    # (private — it vests but can't be sold until a liquidity event) + W2 actuals.
    repository.create_income_profile(
        engine, hh, label="Jordan — Senior SWE, Anthropic (showcase)",
        base_salary_minor=33_000_000, rsu_annual_minor=10_000_000,
        rsu_frequency="quarterly", rsu_next_vest_date=today + timedelta(days=40),
        bonus_percent=0.0, bonus_month=None,
        w2_year=today.year - 1, w2_wages_minor=30_900_000, w2_withheld_minor=6_310_000,
    )

    # --- Tax settings (M65) + memories (M57): Austin, TX — no state income tax.
    repository.update_tax_settings(engine, hh, tax_filing_status="single",
                                   income_treated_as_net=False, state="TX")
    repository.upsert_household_memory(engine, hh, "home_city", "I live in Austin, TX.")
    repository.upsert_household_memory(
        engine, hh, "job",
        "I'm a senior software engineer at Anthropic (remote from Austin).")
    repository.upsert_household_memory(
        engine, hh, "equity",
        "My Anthropic equity is private — it vests quarterly but can't be sold "
        "until a liquidity event, so it isn't cash income.")
    repository.upsert_household_memory(
        engine, hh, "property_tax",
        "Travis County property taxes (~2%) are paid through the mortgage escrow.")

    # --- Net-worth history (M40): a rising MONTHLY series over the two years ---
    base_net_worth = 4_800_000
    with engine.begin() as conn:
        for month in range(MONTHS, 0, -1):
            conn.execute(
                insert(models.net_worth_snapshots).values(
                    id=new_id(),
                    household_id=hh,
                    as_of=today - timedelta(days=30 * month),
                    net_worth_minor=base_net_worth + (MONTHS - month) * 92_000,
                    currency="USD",
                    created_at=datetime.now(UTC),
                )
            )
    return True
