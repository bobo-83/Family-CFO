from sqlalchemy import select
from sqlalchemy.engine import Engine

from family_cfo_api import ai_tools, fixtures, models


def _execute(engine: Engine, name: str, args: dict):
    executor = ai_tools.build_executor(engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")
    return executor(name, args)


def test_read_tool_returns_grounded_output_and_persists_calc(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "get_net_worth", {})

    assert "outputs" in result
    assert result["outputs"]["net_worth"]["currency"] == "USD"
    assert result["calculation_ref"].startswith("financial_calculations:")

    with demo_engine.connect() as conn:
        rows = conn.execute(
            select(models.financial_calculations).where(
                models.financial_calculations.c.calculation_type == "net_worth"
            )
        ).all()
    assert len(rows) >= 1


def test_future_value_tool_computes_growth(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "future_value",
        {"present_value_minor": 100_000, "annual_return_rate": 0.06, "years": 20},
    )

    # $1,000 at 6% for 20 years -> $3,207.14 (engine-verified).
    assert result["outputs"]["future_value"]["amount_minor"] == 320_714
    assert result["calculation_ref"].startswith("financial_calculations:")


def test_missing_required_argument_reports_missing_input(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "future_value", {"annual_return_rate": 0.06, "years": 20})

    assert result["error"] == "missing_input"
    assert result["missing"] == "present_value_minor"


def test_out_of_range_rate_reports_invalid_arguments(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "future_value",
        {"present_value_minor": 1000, "annual_return_rate": 5, "years": 10},
    )

    assert result["error"] == "invalid_arguments"


def test_foreign_currency_is_rejected_for_single_currency_household(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "project_purchase_impact",
        {"price_minor": 100_000, "currency": "EUR"},
    )

    assert result["error"] == "invalid_arguments"
    assert "USD" in result["detail"]


def test_retirement_requires_retirement_age_after_current_age(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "project_retirement",
        {
            "current_age": 50,
            "retirement_age": 40,
            "current_savings_minor": 1000,
            "monthly_contribution_minor": 100,
            "annual_return_rate": 0.05,
        },
    )

    assert result["error"] == "invalid_arguments"


def test_unknown_tool_is_reported(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "delete_everything", {})

    assert result["error"] == "unknown_tool"
