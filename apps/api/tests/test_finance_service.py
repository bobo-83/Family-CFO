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
