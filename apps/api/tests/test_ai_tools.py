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


def test_system_prompt_layers_persona_over_invariant_grounding_rules() -> None:
    from family_cfo_api.config import Settings

    playful = ai_tools.build_system_prompt(Settings(ai_tone="playful"))
    professional = ai_tools.build_system_prompt(Settings(ai_tone="professional"))

    # Persona differs...
    assert "cheeky" in playful and "cheeky" not in professional
    assert "no emoji" in professional
    # ...but the grounding rules are identical and complete in both tones.
    for prompt in (playful, professional):
        assert "ONLY the provided tools" in prompt
        assert "missing_input" in prompt
        assert "never include names, account details" in prompt
    # Unknown tone falls back to playful, never to an empty persona.
    assert "cheeky" in ai_tools.build_system_prompt(Settings(ai_tone="klingon"))


def test_grounded_values_include_rounded_variants() -> None:
    """A model saying "9.6 months" for a tool's 9.6470588 is honest rounding."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    result = ToolCallingResult(
        answer="x",
        completed=True,
        tool_calls=[
            ToolCallRecord(
                name="get_emergency_fund",
                arguments={},
                result={"outputs": {"emergency_fund_months": 9.6470588}},
            )
        ],
    )
    values = ai_tools.grounded_values(result)
    assert {"9.6470588", "9.6", "9.65", "10"} <= values


def test_net_worth_tool_breaks_assets_into_spendability_categories(demo_engine: Engine) -> None:
    """M33: the model must see retirement money as not-spendable, separately."""
    result = _execute(demo_engine, "get_net_worth", {})

    breakdown = result["asset_breakdown"]
    assert "liquid" in breakdown  # demo household has checking/savings
    for category, money in breakdown.items():
        assert money["amount_minor"] >= 0, category
    assert "NOT available for purchases" in result["spendability_note"]
    assert "retirement" in result["spendability_note"]


# --- M64: income/tax, bills, budgets, spending tools ---


def _seed_income(engine: Engine) -> None:
    from datetime import date, timedelta

    from family_cfo_api import repository

    account = repository.create_account(
        engine, fixtures.DEMO_HOUSEHOLD_ID, name="Pay Checking", account_type="checking",
        currency="USD",
    )
    today = date.today()
    for i in range(4):
        repository.create_transaction(
            engine,
            household_id=fixtures.DEMO_HOUSEHOLD_ID,
            account_id=account.id,
            occurred_at=today - timedelta(days=14 * (4 - i)),
            amount_minor=461_538,
            currency="USD",
            merchant="ACME CORP PAYROLL",
            description=None,
            import_source=None,
            import_id=None,
            review_state="reviewed",
        )


def test_income_and_tax_tool_reports_sources_and_estimate(demo_engine: Engine) -> None:
    _seed_income(demo_engine)

    result = _execute(demo_engine, "get_income_and_tax", {})

    assert result["income_sources"][0]["name"] == "ACME CORP PAYROLL"
    assert result["income_sources"][0]["frequency"] == "biweekly"
    assert result["annual_income"]["amount_minor"] == 4 * 461_538
    tax = result["tax_estimate"]
    assert tax["tax_year"] == 2026
    assert tax["estimated_total_tax"]["amount_minor"] > 0
    assert tax["estimated_gross_income"]["amount_minor"] > 4 * 461_538
    assert any("state income tax is NOT included" in a for a in result["assumptions"])
    # Partial history is disclosed to the model too.
    assert any("not a full year" in w for w in result["warnings"])


def test_bills_tool_lists_bills_and_upcoming(demo_engine: Engine) -> None:
    from datetime import date, timedelta

    from family_cfo_api import repository

    repository.create_bill(
        engine=demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        name="Netflix",
        amount_minor=1_549,
        currency="USD",
        frequency="monthly",
        account_id=None,
        next_due_date=date.today() + timedelta(days=5),
    )

    result = _execute(demo_engine, "get_bills", {})

    assert [b["name"] for b in result["bills"]].count("Netflix") == 1
    due = [b for b in result["due_within_14_days"] if b["name"] == "Netflix"]
    assert due and due[0]["days_until"] == 5
    assert due[0]["amount"]["display"] == "USD 15.49"


def test_budgets_tool_reports_envelope_progress(demo_engine: Engine) -> None:
    from family_cfo_api import repository

    category = repository.create_category(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Groceries Envelope")
    repository.create_budget(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        category_id=category.id,
        limit_minor=50_000,
        currency="USD",
    )

    result = _execute(demo_engine, "get_budgets", {})

    envelope = result["month_budgets"][0]
    assert envelope["category"] == "Groceries Envelope"
    assert envelope["limit"]["amount_minor"] == 50_000
    assert envelope["status"] in ("under", "warning", "over")


def test_spending_insights_tool_reports_month_comparison(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "get_spending_insights", {})

    assert "month_to_date_spending" in result
    assert "same_window_last_month" in result
    assert isinstance(result["top_merchants"], list)
