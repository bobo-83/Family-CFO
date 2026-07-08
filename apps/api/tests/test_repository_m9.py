from datetime import date

from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository, security


def test_create_household_with_owner_creates_working_membership(demo_engine: Engine) -> None:
    result = repository.create_household_with_owner(
        demo_engine,
        display_name="New Family",
        base_currency="USD",
        owner_email="new-owner@example.com",
        owner_password_hash=security.hash_password("password-123"),
        owner_display_name="New Owner",
    )

    assert result.role == "owner"
    assert (
        repository.get_membership_role(demo_engine, result.household_id, result.user_id) == "owner"
    )
    assert repository.count_household_owners(demo_engine, result.household_id) == 1


def test_member_lifecycle_and_last_owner_count(demo_engine: Engine) -> None:
    member = repository.create_member(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        email="adult@example.com",
        password_hash=security.hash_password("password-123"),
        display_name="An Adult",
        role="adult",
    )
    assert any(
        m.user_id == member.user_id
        for m in repository.list_members(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    )

    assert repository.update_member_role(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id, "viewer"
    )
    assert (
        repository.get_member(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id).role
        == "viewer"
    )

    # The seeded demo household has exactly one owner.
    assert repository.count_household_owners(demo_engine, fixtures.DEMO_HOUSEHOLD_ID) == 1

    assert repository.delete_member(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id)
    assert repository.get_member(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id) is None


def test_delete_member_revokes_their_sessions(demo_engine: Engine) -> None:
    member = repository.create_member(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        email="revoke-me@example.com",
        password_hash=security.hash_password("password-123"),
        display_name="Revoke Me",
        role="adult",
    )
    token = security.generate_access_token()
    repository.create_auth_session(
        demo_engine,
        member.user_id,
        fixtures.DEMO_HOUSEHOLD_ID,
        security.hash_token(token),
        repository.utcnow().replace(year=2999),
    )
    assert repository.get_session_context(demo_engine, security.hash_token(token)) is not None

    repository.delete_member(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id)

    assert repository.get_session_context(demo_engine, security.hash_token(token)) is None


def test_account_write_and_balance_append(demo_engine: Engine) -> None:
    account = repository.create_account(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        name="Brokerage",
        account_type="brokerage",
        currency="USD",
    )
    assert repository.get_latest_balance_minor(demo_engine, account.id) == 0

    repository.record_account_balance(demo_engine, account.id, 250_000)
    assert repository.get_latest_balance_minor(demo_engine, account.id) == 250_000

    repository.record_account_balance(demo_engine, account.id, 300_000)
    assert repository.get_latest_balance_minor(demo_engine, account.id) == 300_000

    assert repository.update_account(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, account.id, name="Renamed"
    )
    assert (
        repository.get_account(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, account.id).name
        == "Renamed"
    )


def test_account_in_use_blocks_when_referenced(demo_engine: Engine) -> None:
    account = repository.create_account(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        name="Scratch",
        account_type="checking",
        currency="USD",
    )
    assert repository.account_in_use(demo_engine, account.id) is False

    repository.create_transaction(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=account.id,
        occurred_at=date(2026, 1, 1),
        amount_minor=-1000,
        currency="USD",
        merchant="Test",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    assert repository.account_in_use(demo_engine, account.id) is True


def test_transaction_bill_income_write_round_trips(demo_engine: Engine) -> None:
    transaction_id = repository.create_transaction(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        occurred_at=date(2026, 2, 2),
        amount_minor=-4200,
        currency="USD",
        merchant="Manual Entry",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    assert repository.update_transaction(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, transaction_id, merchant="Edited"
    )
    assert (
        repository.get_transaction(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, transaction_id).merchant
        == "Edited"
    )
    assert repository.delete_transaction(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, transaction_id)
    assert (
        repository.get_transaction(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, transaction_id) is None
    )

    bill = repository.create_bill(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        name="Gym",
        amount_minor=5000,
        currency="USD",
        frequency="monthly",
    )
    assert repository.update_bill(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, bill.id, amount_minor=6000
    )
    assert (
        repository.get_bill(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, bill.id).amount_minor == 6000
    )
    assert repository.delete_bill(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, bill.id)

    income = repository.create_income_source(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        name="Freelance",
        amount_minor=100_000,
        currency="USD",
        frequency="monthly",
    )
    assert repository.update_income_source(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, income.id, name="Consulting"
    )
    assert (
        repository.get_income_source(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, income.id).name
        == "Consulting"
    )
    assert repository.delete_income_source(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, income.id)


def test_audit_events_recorded_and_listed(demo_engine: Engine) -> None:
    repository.record_audit_event(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        actor_user_id=fixtures.DEMO_USER_ID,
        action="account.created",
        entity_type="account",
        entity_id="acct-1",
        summary="Created account 'Test'",
    )
    events = repository.list_audit_events(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert len(events) == 1
    assert events[0].action == "account.created"
    assert events[0].entity_type == "account"
