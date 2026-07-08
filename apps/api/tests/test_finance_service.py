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
