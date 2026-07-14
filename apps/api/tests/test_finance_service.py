from sqlalchemy import select
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, fixtures, models
from family_cfo_financial_engine import Money


def test_compute_net_worth_sums_demo_accounts(demo_engine: Engine) -> None:
    result = finance_service.compute_net_worth(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")

    # checking 500_000 + savings 1_500_000 - mortgage 300_000_000
    assert result.outputs["net_worth"] == Money(500_000 + 1_500_000 - 300_000_000, "USD")


def test_compute_net_worth_persists_audit_record(demo_engine: Engine) -> None:
    finance_service.compute_net_worth(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")

    with demo_engine.connect() as conn:
        rows = (
            conn.execute(
                select(models.financial_calculations).where(
                    models.financial_calculations.c.calculation_type == "net_worth"
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 1
    assert rows[0]["household_id"] == fixtures.DEMO_HOUSEHOLD_ID
    assert rows[0]["outputs_json"]["net_worth"] == {"amount_minor": -298_000_000, "currency": "USD"}


def test_compute_emergency_fund_uses_liquid_balances_and_monthly_bills(demo_engine: Engine) -> None:
    result = finance_service.compute_emergency_fund(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")

    # liquid = checking 500_000 + savings 1_500_000 = 2_000_000
    # monthly bills = mortgage 200_000 + internet 8_000 = 208_000
    assert result.outputs["liquid_balance"] == Money(2_000_000, "USD")
    assert result.outputs["monthly_essential_expenses"] == Money(208_000, "USD")
    assert result.outputs["emergency_fund_months"] == 2_000_000 / 208_000


def test_safe_to_spend_subtracts_bills_and_debt_not_just_the_emergency_fund(
    demo_engine: Engine,
) -> None:
    """The reported bug (2026-07-13): the advisor answered "how much can I spend"
    with liquid cash minus the emergency fund, ignoring every bill about to fall
    due and every minimum debt payment owed."""
    result, _ref = finance_service.compute_safe_to_spend(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD"
    )
    outputs = result.outputs

    # liquid = checking 500_000 + savings 1_500_000
    assert outputs["liquid_balance"] == Money(2_000_000, "USD")
    # The demo household has bills, so they MUST show up as committed money.
    assert outputs["bills_due"].amount_minor > 0

    committed = (
        outputs["emergency_fund_reserved"] + outputs["bills_due"] + outputs["minimum_debt_payments"]
    )
    assert outputs["committed_total"] == committed
    assert outputs["safe_to_spend"] == outputs["liquid_balance"] - committed
    # The old answer. Anything equal to it means bills/debt were ignored again.
    assert outputs["safe_to_spend"] != outputs["liquid_balance"] - outputs["emergency_fund_reserved"]


def test_safe_to_spend_flags_liabilities_with_no_recorded_minimum_payment(
    demo_engine: Engine,
) -> None:
    """The demo mortgage carries no terms, so its claim on the cash is invisible —
    the figure is overstated and must say so rather than quietly look healthy."""
    result, _ref = finance_service.compute_safe_to_spend(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD"
    )

    assert any("UNDERSTATED" in w for w in result.warnings)


def test_safe_to_spend_reports_liabilities_that_have_no_minimum_payment(
    demo_engine: Engine,
) -> None:
    """The user's real household carried $29,931 across three credit cards, none
    with a minimum payment recorded — so nothing was subtracted for debt and the
    advisor said nothing about it. Now the debt is reported and the shortfall
    named."""
    result, _ref = finance_service.compute_safe_to_spend(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD"
    )

    # The demo mortgage is a liability, so the household owes something.
    assert result.outputs["total_debt"].amount_minor > 0
    assert any("owes" in w for w in result.warnings)


def test_monthly_income_counts_compensation_profiles(demo_engine: Engine) -> None:
    """M73/M94: a compensation profile (W2/declared) must show up as income on the
    Overview, not just recurring income sources — the user's reported $0 bug."""
    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    before = finance_service.monthly_income_total(demo_engine, hh, "USD")

    repository.create_income_profile(
        demo_engine, hh, label="ACME",
        base_salary_minor=12_000_000,   # $120k base
        rsu_annual_minor=6_000_000,     # $60k RSU
        rsu_frequency="quarterly", rsu_next_vest_date=None,
        bonus_percent=10.0, bonus_month=None,      # +$12k bonus
        w2_year=None, w2_wages_minor=None, w2_withheld_minor=None,
    )

    after = finance_service.monthly_income_total(demo_engine, hh, "USD")
    # (120k + 60k + 12k) / 12 = $16,000/mo added.
    assert after.amount_minor - before.amount_minor == 1_600_000
