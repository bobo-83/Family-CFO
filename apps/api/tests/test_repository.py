from datetime import UTC, datetime, timedelta

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, models, repository, security


def test_get_user_by_email_returns_seeded_demo_user(demo_engine: Engine) -> None:
    user = repository.get_user_by_email(demo_engine, fixtures.DEMO_USER_EMAIL)

    assert user is not None
    assert user.id == fixtures.DEMO_USER_ID
    assert security.verify_password(fixtures.DEMO_USER_PASSWORD, user.password_hash)


def test_get_user_by_email_returns_none_for_unknown_email(demo_engine: Engine) -> None:
    assert repository.get_user_by_email(demo_engine, "nobody@example.com") is None


def test_get_household_returns_seeded_household(demo_engine: Engine) -> None:
    household = repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)

    assert household is not None
    assert household.base_currency == "USD"


def test_list_account_balances_returns_latest_balance_per_account(demo_engine: Engine) -> None:
    balances = repository.list_account_balances(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)

    assert {b.account_id for b in balances} == {
        fixtures.DEMO_CHECKING_ACCOUNT_ID,
        fixtures.DEMO_SAVINGS_ACCOUNT_ID,
        fixtures.DEMO_MORTGAGE_ACCOUNT_ID,
    }
    checking = next(b for b in balances if b.account_id == fixtures.DEMO_CHECKING_ACCOUNT_ID)
    assert checking.balance_minor == 500_000


def test_list_account_balances_uses_most_recent_as_of(demo_engine: Engine) -> None:
    now = datetime.now(UTC)
    with demo_engine.begin() as conn:
        conn.execute(
            insert(models.account_balances).values(
                id=repository.new_id(),
                account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
                balance_minor=999_000,
                as_of=now + timedelta(days=1),
                created_at=now,
            )
        )

    balances = repository.list_account_balances(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    checking = next(b for b in balances if b.account_id == fixtures.DEMO_CHECKING_ACCOUNT_ID)
    assert checking.balance_minor == 999_000


def test_list_transactions_returns_seeded_transactions(demo_engine: Engine) -> None:
    transactions = repository.list_transactions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)

    assert len(transactions) == 2
    assert all(t.category == "Groceries" for t in transactions)


def test_list_bills_and_income(demo_engine: Engine) -> None:
    bills = repository.list_bills(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    income = repository.list_income_sources(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)

    assert {b.name for b in bills} == {"Mortgage payment", "Internet"}
    assert {i.name for i in income} == {"Salary"}


def test_list_and_create_goals(demo_engine: Engine) -> None:
    goals = repository.list_goals(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert len(goals) == 1

    created = repository.create_goal(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        name="Vacation",
        goal_type="vacation",
        target_minor=500_000,
        currency="USD",
        target_date=None,
        priority=3,
    )

    assert created.current_minor == 0
    assert created.name == "Vacation"

    goals_after = repository.list_goals(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert len(goals_after) == 2


def test_auth_session_round_trip(demo_engine: Engine) -> None:
    token = security.generate_access_token()
    expires_at = repository.utcnow() + timedelta(hours=1)
    repository.create_auth_session(
        demo_engine,
        fixtures.DEMO_USER_ID,
        fixtures.DEMO_HOUSEHOLD_ID,
        security.hash_token(token),
        expires_at,
    )

    session = repository.get_session_context(demo_engine, security.hash_token(token))

    assert session is not None
    assert session.user_id == fixtures.DEMO_USER_ID
    assert session.household_id == fixtures.DEMO_HOUSEHOLD_ID
    assert session.role == "owner"


def test_auth_session_rejects_expired_token(demo_engine: Engine) -> None:
    token = security.generate_access_token()
    expired_at = repository.utcnow() - timedelta(hours=1)
    repository.create_auth_session(
        demo_engine,
        fixtures.DEMO_USER_ID,
        fixtures.DEMO_HOUSEHOLD_ID,
        security.hash_token(token),
        expired_at,
    )

    assert repository.get_session_context(demo_engine, security.hash_token(token)) is None


def test_auth_session_rejects_unknown_token(demo_engine: Engine) -> None:
    assert repository.get_session_context(demo_engine, security.hash_token("nope")) is None
