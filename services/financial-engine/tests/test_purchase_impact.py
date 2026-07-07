from family_cfo_financial_engine.goal_progress import GoalInput
from family_cfo_financial_engine.money import Money
from family_cfo_financial_engine.purchase_impact import PurchaseImpactInputs, calculate_purchase_impact


def _base_inputs(**overrides: object) -> PurchaseImpactInputs:
    defaults = dict(
        price=Money(50_000, "USD"),
        net_worth_before=Money(1_000_000, "USD"),
        liquid_balance_before=Money(500_000, "USD"),
        monthly_essential_expenses=Money(200_000, "USD"),
        discretionary_cash_flow=Money(100_000, "USD"),
        liability_total=Money.zero("USD"),
    )
    defaults.update(overrides)
    return PurchaseImpactInputs(**defaults)


def test_purchase_impact_computes_net_worth_and_liquidity_deltas() -> None:
    result = calculate_purchase_impact(_base_inputs())

    assert result.calculation_type == "purchase_impact"
    assert result.outputs["net_worth_after"] == Money(1_000_000 - 50_000, "USD")
    assert result.outputs["emergency_fund_months_before"] == 500_000 / 200_000
    assert result.outputs["emergency_fund_months_after"] == (500_000 - 50_000) / 200_000
    assert result.warnings == []


def test_purchase_impact_computes_discretionary_burn() -> None:
    result = calculate_purchase_impact(_base_inputs())

    assert result.outputs["discretionary_months_consumed"] == 50_000 / 100_000


def test_purchase_impact_warns_when_price_exceeds_liquid_balance() -> None:
    result = calculate_purchase_impact(_base_inputs(price=Money(600_000, "USD")))

    assert any("exceeds available liquid balance" in w for w in result.warnings)


def test_purchase_impact_warns_when_discretionary_cash_flow_not_positive() -> None:
    result = calculate_purchase_impact(_base_inputs(discretionary_cash_flow=Money.zero("USD")))

    assert result.outputs["discretionary_months_consumed"] is None
    assert any("discretionary cash flow" in w for w in result.warnings)


def test_purchase_impact_computes_top_goal_opportunity_cost() -> None:
    goal = GoalInput(
        goal_id="goal-1",
        name="Vacation",
        target=Money(200_000, "USD"),
        current=Money(50_000, "USD"),
    )

    result = calculate_purchase_impact(_base_inputs(top_goal=goal))

    # remaining = 150_000; price 50_000 / 150_000 * 100 = 33.33
    assert result.outputs["top_goal_impact_percent"] == round(50_000 / 150_000 * 100, 2)


def test_purchase_impact_without_top_goal_leaves_percent_unset() -> None:
    result = calculate_purchase_impact(_base_inputs())

    assert result.outputs["top_goal_impact_percent"] is None


def test_purchase_impact_warns_about_debt_data_when_liabilities_present() -> None:
    result = calculate_purchase_impact(_base_inputs(liability_total=Money(-100_000, "USD")))

    assert any("debt payoff impact" in w for w in result.warnings)


def test_purchase_impact_no_debt_warning_without_liabilities() -> None:
    result = calculate_purchase_impact(_base_inputs())

    assert not any("debt payoff impact" in w for w in result.warnings)
