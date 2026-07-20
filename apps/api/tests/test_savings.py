"""ADR 0047: waste-first savings opportunities for the advisor."""

from datetime import date

from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository, savings


def test_classify_category_splits_needs_from_wants() -> None:
    assert savings.classify_category("Rent") == "essential"
    assert savings.classify_category("Groceries") == "essential"
    assert savings.classify_category("Health Insurance") == "essential"
    assert savings.classify_category("Dining Out") == "discretionary"
    assert savings.classify_category("Tennis") == "discretionary"
    assert savings.classify_category("Shopping") == "discretionary"


def _txn(engine, account_id, when, minor, merchant, category_id=None):
    repository.create_transaction(
        engine, household_id=fixtures.DEMO_HOUSEHOLD_ID, account_id=account_id,
        occurred_at=when, amount_minor=minor, currency="USD", merchant=merchant,
        description=merchant, import_source=None, import_id=None, review_state="reviewed",
        category_id=category_id,
    )


def test_find_savings_splits_ranks_and_flags_waste(demo_engine: Engine) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 7, 15)  # trailing complete months: Apr, May, Jun 2026
    checking = repository.create_account(demo_engine, hh, "Spend Checking", "checking", "USD")
    rent = repository.create_category(demo_engine, hh, "Rent")
    dining = repository.create_category(demo_engine, hh, "Dining")
    shopping = repository.create_category(demo_engine, hh, "Shopping")

    _txn(demo_engine, checking.id, date(2026, 5, 3), -200_000, "Landlord", rent.id)  # essential
    for m in (4, 5, 6):
        _txn(demo_engine, checking.id, date(2026, m, 10), -30_000, "Bistro", dining.id)
        _txn(demo_engine, checking.id, date(2026, m, 12), -60_000, "Mall", shopping.id)
        # Two streaming subscriptions, monthly (needs 3 occurrences to detect).
        _txn(demo_engine, checking.id, date(2026, m, 15), -1_599, "Netflix")
        _txn(demo_engine, checking.id, date(2026, m, 16), -1_099, "Spotify")

    repository.create_goal(
        demo_engine, hh, "Emergency Fund", "emergency_fund", 1_000_000, "USD",
        target_date=None, priority=1, current_minor=200_000,
    )

    report = savings.find_savings(demo_engine, hh, "USD", today=today)

    # Needs vs wants: rent is essential; dining + shopping are discretionary.
    assert report.essential_monthly.amount_minor == round(200_000 / 3)
    # Ranked largest-first: shopping ($600/3mo) above dining ($300/3mo).
    ranked = [(c.name, c.monthly_avg.amount_minor) for c in report.discretionary_ranked]
    assert ranked[0] == ("Shopping", 60_000)
    assert ("Dining", 30_000) in ranked
    # Subscriptions found, and the duplicate-streaming waste flag raised.
    merchants = {s.merchant for s in report.subscriptions}
    assert "Netflix" in merchants and "Spotify" in merchants
    assert any("streaming" in w.lower() for w in report.possible_waste)
    # The open goal is offered to tie trims to.
    assert ("Emergency Fund", 800_000) in [(n, g.amount_minor) for n, g in report.goals]


def test_find_savings_tool_is_advertised_and_shaped(demo_engine: Engine) -> None:
    from family_cfo_api import ai_tools

    assert "find_savings" in {t.name for t in ai_tools.build_tools()}
    result = ai_tools.build_executor(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")(
        "find_savings", {}
    )
    for key in (
        "essential_monthly", "discretionary_monthly", "discretionary_by_category",
        "subscriptions", "possible_waste", "valued_activities", "goals",
    ):
        assert key in result


def test_grounding_rules_teach_waste_first_savings() -> None:
    from family_cfo_api import ai_tools

    prompt = ai_tools.build_system_prompt().lower()
    assert "find_savings" in prompt
    assert "waste first" in prompt
    assert "valued_activities" in prompt
