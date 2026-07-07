from __future__ import annotations

from dataclasses import dataclass

from family_cfo_financial_engine.emergency_fund import calculate_emergency_fund_months
from family_cfo_financial_engine.goal_progress import GoalInput, calculate_goal_progress
from family_cfo_financial_engine.money import Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult


@dataclass(frozen=True, slots=True)
class PurchaseImpactInputs:
    """Inputs to a one-time cash purchase impact calculation.

    The purchase is assumed to be paid in cash from liquid balances; it does
    not model financing, recurring costs, or multi-item purchases.
    """

    price: Money
    net_worth_before: Money
    liquid_balance_before: Money
    monthly_essential_expenses: Money
    discretionary_cash_flow: Money
    liability_total: Money
    top_goal: GoalInput | None = None


def calculate_purchase_impact(inputs: PurchaseImpactInputs) -> CalculationResult:
    price = inputs.price
    net_worth_after = inputs.net_worth_before - price
    liquid_balance_after = inputs.liquid_balance_before - price

    warnings: list[str] = []
    if price > inputs.liquid_balance_before:
        warnings.append("purchase price exceeds available liquid balance")

    emergency_fund_before = calculate_emergency_fund_months(
        inputs.liquid_balance_before, inputs.monthly_essential_expenses
    )
    emergency_fund_after = calculate_emergency_fund_months(
        liquid_balance_after, inputs.monthly_essential_expenses
    )
    warnings.extend(emergency_fund_after.warnings)

    discretionary_months_consumed: float | None
    if inputs.discretionary_cash_flow.amount_minor > 0:
        discretionary_months_consumed = price.ratio(inputs.discretionary_cash_flow)
    else:
        discretionary_months_consumed = None
        warnings.append("discretionary cash flow is zero or negative; purchase burn rate is undefined")

    top_goal_impact_percent: float | None = None
    if inputs.top_goal is not None:
        goal_progress = calculate_goal_progress(inputs.top_goal)
        remaining = goal_progress.outputs["remaining"]
        if remaining.amount_minor > 0:
            top_goal_impact_percent = round(price.ratio(remaining) * 100, 2)
        warnings.extend(goal_progress.warnings)

    if inputs.liability_total.amount_minor < 0:
        warnings.append(
            "debt payoff impact requires interest rate and payment data not yet modeled"
        )

    return CalculationResult(
        calculation_type="purchase_impact",
        version=CALCULATION_ENGINE_VERSION,
        inputs={
            "price": price.to_dict(),
            "currency": price.currency,
            "has_top_goal": inputs.top_goal is not None,
            "has_liabilities": inputs.liability_total.amount_minor < 0,
        },
        assumptions=[
            "The purchase is assumed to be paid in cash from liquid checking/savings balances.",
            "Discretionary cash flow and essential expenses are held constant except for the purchase price.",
        ],
        outputs={
            "price": price,
            "net_worth_before": inputs.net_worth_before,
            "net_worth_after": net_worth_after,
            "emergency_fund_months_before": emergency_fund_before.outputs["emergency_fund_months"],
            "emergency_fund_months_after": emergency_fund_after.outputs["emergency_fund_months"],
            "discretionary_months_consumed": discretionary_months_consumed,
            "top_goal_impact_percent": top_goal_impact_percent,
        },
        warnings=warnings,
    )
