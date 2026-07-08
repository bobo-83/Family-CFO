from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select
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


def test_pairing_session_confirm_creates_device_backed_session(demo_engine: Engine) -> None:
    pairing_session_id = repository.new_id()
    token = security.generate_access_token()
    expires_at = repository.utcnow() + timedelta(hours=1)
    repository.create_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        created_by_user_id=fixtures.DEMO_USER_ID,
        qr_payload="{}",
        expires_at=expires_at,
    )

    credential = repository.confirm_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        device_name="Alex's iPhone",
        device_public_key="public-key",
        access_token=token,
        token_hash=security.hash_token(token),
        expires_at=expires_at,
    )

    assert credential is not None
    assert credential.access_token == token
    session = repository.get_session_context(demo_engine, security.hash_token(token))
    assert session is not None
    assert session.household_id == fixtures.DEMO_HOUSEHOLD_ID

    devices = repository.list_paired_devices(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert [device.name for device in devices] == ["Alex's iPhone"]


def test_pairing_session_cannot_be_confirmed_twice(demo_engine: Engine) -> None:
    pairing_session_id = repository.new_id()
    expires_at = repository.utcnow() + timedelta(hours=1)
    repository.create_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        created_by_user_id=fixtures.DEMO_USER_ID,
        qr_payload="{}",
        expires_at=expires_at,
    )
    first_token = security.generate_access_token()
    second_token = security.generate_access_token()

    first = repository.confirm_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        device_name="First iPhone",
        device_public_key="first-public-key",
        access_token=first_token,
        token_hash=security.hash_token(first_token),
        expires_at=expires_at,
    )
    second = repository.confirm_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        device_name="Second iPhone",
        device_public_key="second-public-key",
        access_token=second_token,
        token_hash=security.hash_token(second_token),
        expires_at=expires_at,
    )

    assert first is not None
    assert second is None


def test_expired_pairing_session_cannot_be_confirmed(demo_engine: Engine) -> None:
    pairing_session_id = repository.new_id()
    expires_at = repository.utcnow() - timedelta(minutes=1)
    token = security.generate_access_token()
    repository.create_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        created_by_user_id=fixtures.DEMO_USER_ID,
        qr_payload="{}",
        expires_at=expires_at,
    )

    credential = repository.confirm_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        device_name="Expired iPhone",
        device_public_key="public-key",
        access_token=token,
        token_hash=security.hash_token(token),
        expires_at=repository.utcnow() + timedelta(hours=1),
    )

    assert credential is None


def test_revoke_paired_device_revokes_device_sessions(demo_engine: Engine) -> None:
    pairing_session_id = repository.new_id()
    token = security.generate_access_token()
    expires_at = repository.utcnow() + timedelta(hours=1)
    repository.create_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        created_by_user_id=fixtures.DEMO_USER_ID,
        qr_payload="{}",
        expires_at=expires_at,
    )
    credential = repository.confirm_pairing_session(
        demo_engine,
        pairing_session_id=pairing_session_id,
        device_name="Revoked iPhone",
        device_public_key="public-key",
        access_token=token,
        token_hash=security.hash_token(token),
        expires_at=expires_at,
    )
    assert credential is not None

    revoked = repository.revoke_paired_device(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        credential.device_id,
    )

    assert revoked is True
    assert repository.get_session_context(demo_engine, security.hash_token(token)) is None


def test_create_scenario_and_recommendation_round_trip(demo_engine: Engine) -> None:
    scenario_id = repository.create_scenario(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        created_by_user_id=fixtures.DEMO_USER_ID,
        name="Purchase: a new laptop",
        description=None,
        input_json={"item": "a new laptop", "price": {"amount_minor": 150_000, "currency": "USD"}},
    )

    recommendation_id = repository.create_recommendation(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        scenario_id=scenario_id,
        answer="Buying a new laptop would leave your net worth at USD 1,000.00.",
        assumptions=["The purchase is paid in cash."],
        impacts=[{"area": "net_worth", "summary": "...", "amount": None}],
        tradeoffs=["Paying in cash avoids interest."],
        alternatives=["Delay the purchase."],
        confidence=0.75,
        calculation_refs=["financial_calculations:abc123"],
        warnings=[],
        explanation_source="deterministic_stub",
    )

    with demo_engine.connect() as conn:
        scenario_row = (
            conn.execute(select(models.scenarios).where(models.scenarios.c.id == scenario_id))
            .mappings()
            .first()
        )
        recommendation_row = (
            conn.execute(
                select(models.recommendations).where(
                    models.recommendations.c.id == recommendation_id
                )
            )
            .mappings()
            .first()
        )

    assert scenario_row is not None
    assert scenario_row["name"] == "Purchase: a new laptop"
    assert recommendation_row is not None
    assert recommendation_row["scenario_id"] == scenario_id
    assert recommendation_row["confidence"] == 0.75
