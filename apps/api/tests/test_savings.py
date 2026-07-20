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
    # Steady monthly spend → recurring habits, ranked largest-first.
    ranked = [(c.name, c.monthly_avg.amount_minor) for c in report.recurring_ranked]
    assert ranked[0] == ("Shopping", 60_000)
    assert ("Dining", 30_000) in ranked
    # Subscriptions found, and the duplicate-streaming waste flag raised.
    merchants = {s.merchant for s in report.subscriptions}
    assert "Netflix" in merchants and "Spotify" in merchants
    assert any("streaming" in w.lower() for w in report.possible_waste)
    # The open goal is offered to tie trims to.
    assert ("Emergency Fund", 800_000) in [(n, g.amount_minor) for n, g in report.goals]


def test_subscriptions_exclude_debt_and_bill_payments(demo_engine: Engine) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 7, 15)
    checking = repository.create_account(demo_engine, hh, "Chk", "checking", "USD")
    # A student-loan payment whose charge merchant differs from the account name.
    repository.create_account(
        demo_engine, hh, "U.S. Department of Education", "student_loan", "USD",
        annual_interest_rate=0.02, minimum_payment_minor=7_800,
    )
    for m in (4, 5, 6):
        _txn(demo_engine, checking.id, date(2026, m, 15), -7_800, "Department of Education")
        _txn(demo_engine, checking.id, date(2026, m, 16), -1_599, "Netflix")  # real subscription

    report = savings.find_savings(demo_engine, hh, "USD", today=today)
    merchants = {s.merchant for s in report.subscriptions}
    assert "Netflix" in merchants
    # Matched to the "U.S. Department of Education" account despite the shorter name.
    assert "Department of Education" not in merchants


def test_creep_ignores_one_off_spikes_without_a_baseline(demo_engine: Engine) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 7, 15)  # last complete month = June
    checking = repository.create_account(demo_engine, hh, "Chk2", "checking", "USD")
    travel = repository.create_category(demo_engine, hh, "Travel")
    # A single big June trip, nothing before it — a spike, not a creeping habit.
    _txn(demo_engine, checking.id, date(2026, 6, 8), -500_000, "Airline", travel.id)

    report = savings.find_savings(demo_engine, hh, "USD", today=today)
    assert not any("Travel" in w for w in report.possible_waste)


def test_creep_ignores_huge_one_off_spikes_even_with_a_baseline(demo_engine: Engine) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 7, 15)
    checking = repository.create_account(demo_engine, hh, "Chk3", "checking", "USD")
    reno = repository.create_category(demo_engine, hh, "Home Improvements")
    # A steady ~$300/mo baseline, then a $20k renovation in June — a spike (66x),
    # not a creeping habit.
    for m in (3, 4, 5):
        _txn(demo_engine, checking.id, date(2026, m, 5), -30_000, "Hardware", reno.id)
    _txn(demo_engine, checking.id, date(2026, 6, 5), -2_000_000, "Contractor", reno.id)

    report = savings.find_savings(demo_engine, hh, "USD", today=today)
    assert not any("Home Improvements" in w for w in report.possible_waste)


def test_recurring_and_one_off_are_separated(demo_engine: Engine) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 7, 15)
    checking = repository.create_account(demo_engine, hh, "Chk4", "checking", "USD")
    dining = repository.create_category(demo_engine, hh, "Dining")
    reno = repository.create_category(demo_engine, hh, "Home Improvements")
    # Dining every month → recurring habit. A single June renovation → one-off.
    for m in (4, 5, 6):
        _txn(demo_engine, checking.id, date(2026, m, 10), -30_000, "Bistro", dining.id)
    _txn(demo_engine, checking.id, date(2026, 6, 20), -2_000_000, "Contractor", reno.id)

    report = savings.find_savings(demo_engine, hh, "USD", today=today)
    recurring = {c.name for c in report.recurring_ranked}
    one_off = {o.name for o in report.one_off}
    assert "Dining" in recurring and "Home Improvements" not in recurring
    assert "Home Improvements" in one_off  # already spent, not a monthly habit


def test_valued_activity_is_never_flagged_as_waste(demo_engine: Engine) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 7, 15)
    checking = repository.create_account(demo_engine, hh, "Chk5", "checking", "USD")
    tennis = repository.create_category(demo_engine, hh, "Tennis")
    # A real baseline that crept up in June — but tennis is a valued activity.
    for m in (3, 4, 5):
        _txn(demo_engine, checking.id, date(2026, m, 5), -30_000, "Club", tennis.id)
    _txn(demo_engine, checking.id, date(2026, 6, 5), -50_000, "Club", tennis.id)
    repository.upsert_household_memory(
        demo_engine, hh, "recreational_spending", "Tennis at the club is a weekly ritual.",
        source="study",
    )

    report = savings.find_savings(demo_engine, hh, "USD", today=today)
    assert not any("Tennis" in w for w in report.possible_waste)


def test_find_savings_tool_is_advertised_and_shaped(demo_engine: Engine) -> None:
    from family_cfo_api import ai_tools

    assert "find_savings" in {t.name for t in ai_tools.build_tools()}
    result = ai_tools.build_executor(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")(
        "find_savings", {}
    )
    for key in (
        "essential_monthly", "discretionary_monthly", "recurring_discretionary",
        "one_off_purchases", "subscriptions", "possible_waste", "valued_activities", "goals",
    ):
        assert key in result


def test_grounding_rules_teach_waste_first_savings() -> None:
    from family_cfo_api import ai_tools

    prompt = ai_tools.build_system_prompt().lower()
    assert "find_savings" in prompt
    assert "waste first" in prompt
    assert "valued_activities" in prompt
