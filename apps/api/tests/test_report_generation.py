from datetime import date

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, models, report_generation, repository
from family_cfo_api.explanation import DeterministicExplanationAdapter
from family_cfo_financial_engine import Money

# A reference date far from any real "today" the test suite might run on, so the
# demo fixtures' own today-dated seed transactions never leak into these periods.
_REFERENCE_DATE = date(2030, 3, 8)


def test_compute_report_period_weekly_covers_seven_days_ending_before_reference() -> None:
    period = report_generation.compute_report_period("weekly", _REFERENCE_DATE)

    assert period.start == date(2030, 3, 1)
    assert period.end_exclusive == date(2030, 3, 8)


def test_compute_report_period_monthly_covers_prior_calendar_month() -> None:
    period = report_generation.compute_report_period("monthly", date(2030, 3, 15))

    assert period.start == date(2030, 2, 1)
    assert period.end_exclusive == date(2030, 3, 1)


def test_compute_report_period_monthly_handles_january_year_rollover() -> None:
    period = report_generation.compute_report_period("monthly", date(2030, 1, 15))

    assert period.start == date(2029, 12, 1)
    assert period.end_exclusive == date(2030, 1, 1)


def _seed_report_transactions(engine: Engine) -> None:
    travel_category_id = repository.new_id()
    with engine.begin() as conn:
        conn.execute(
            insert(models.transaction_categories).values(
                id=travel_category_id,
                household_id=fixtures.DEMO_HOUSEHOLD_ID,
                name="Travel",
                parent_category_id=None,
                created_at=repository.utcnow(),
            )
        )
        conn.execute(
            insert(models.transactions),
            [
                # Current week (2030-03-01 .. 2030-03-07): Groceries spend doubled vs last week.
                dict(
                    id=repository.new_id(),
                    household_id=fixtures.DEMO_HOUSEHOLD_ID,
                    account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
                    occurred_at=date(2030, 3, 3),
                    amount_minor=-20_000,
                    currency="USD",
                    merchant="Whole Foods",
                    category_id=fixtures.DEMO_GROCERIES_CATEGORY_ID,
                    description=None,
                    import_source=None,
                    review_state="reviewed",
                    created_at=repository.utcnow(),
                ),
                # New, previously-unseen category above the unusual-spending threshold.
                dict(
                    id=repository.new_id(),
                    household_id=fixtures.DEMO_HOUSEHOLD_ID,
                    account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
                    occurred_at=date(2030, 3, 2),
                    amount_minor=-30_000,
                    currency="USD",
                    merchant="Airline",
                    category_id=travel_category_id,
                    description=None,
                    import_source=None,
                    review_state="reviewed",
                    created_at=repository.utcnow(),
                ),
                # Previous week (2030-02-22 .. 2030-02-28): Groceries baseline.
                dict(
                    id=repository.new_id(),
                    household_id=fixtures.DEMO_HOUSEHOLD_ID,
                    account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
                    occurred_at=date(2030, 2, 25),
                    amount_minor=-10_000,
                    currency="USD",
                    merchant="Whole Foods",
                    category_id=fixtures.DEMO_GROCERIES_CATEGORY_ID,
                    description=None,
                    import_source=None,
                    review_state="reviewed",
                    created_at=repository.utcnow(),
                ),
            ],
        )


def test_generate_report_flags_risk_win_and_unusual_spending(demo_engine: Engine) -> None:
    _seed_report_transactions(demo_engine)

    record = report_generation.generate_report(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        "weekly",
        DeterministicExplanationAdapter(),
        reference_date=_REFERENCE_DATE,
    )

    assert record.report_type == "weekly"
    assert record.period_start == date(2030, 3, 1)
    assert record.period_end == date(2030, 3, 7)
    assert record.explanation_source == "deterministic_stub"

    risks = record.summary["risks"]
    assert any(
        "Groceries" in risk and "USD 100.00" in risk and "USD 200.00" in risk for risk in risks
    )

    unusual = record.summary["unusual_spending"]
    assert any("Travel" in entry and "USD 300.00" in entry for entry in unusual)

    monthly_income = Money(600_000, "USD")  # Salary, from demo fixtures
    monthly_bills = Money(208_000, "USD")  # Mortgage + Internet, from demo fixtures
    period_income = monthly_income.scale(7, 30)
    period_bills = monthly_bills.scale(7, 30)
    expected_remaining = period_income - period_bills - Money(50_000, "USD")

    assert record.summary["net_cash_flow"] == expected_remaining.to_dict()
    if expected_remaining.is_negative():
        assert any("exceeded" in risk for risk in risks)
    else:
        assert any("remaining" in win for win in record.summary["wins"])

    assert len(record.summary["calculation_refs"]) == 2
    assert len(record.summary["goal_progress"]) == 1
    goal_summary = record.summary["goal_progress"][0]
    assert goal_summary["name"] == "Emergency fund"
    assert goal_summary["calculation_ref"].startswith("financial_calculations:")


def test_generate_report_is_idempotent_for_same_period(demo_engine: Engine) -> None:
    _seed_report_transactions(demo_engine)

    first = report_generation.generate_report(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        "weekly",
        DeterministicExplanationAdapter(),
        reference_date=_REFERENCE_DATE,
    )
    second = report_generation.generate_report(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        "weekly",
        DeterministicExplanationAdapter(),
        reference_date=_REFERENCE_DATE,
    )

    assert first.id == second.id
    assert len(repository.list_reports(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)) == 1


def test_generate_report_unknown_household_raises(demo_engine: Engine) -> None:
    try:
        report_generation.generate_report(
            demo_engine, "not-a-household", "weekly", DeterministicExplanationAdapter()
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_run_scheduled_reports_once_skips_already_generated_period(demo_engine: Engine) -> None:
    _seed_report_transactions(demo_engine)

    first_count = report_generation.run_scheduled_reports_once(
        demo_engine, "weekly", reference_date=_REFERENCE_DATE
    )
    second_count = report_generation.run_scheduled_reports_once(
        demo_engine, "weekly", reference_date=_REFERENCE_DATE
    )

    assert first_count == 1
    assert second_count == 0
    assert len(repository.list_reports(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)) == 1
