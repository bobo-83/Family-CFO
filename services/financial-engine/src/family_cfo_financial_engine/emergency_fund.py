from __future__ import annotations

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult


def calculate_emergency_fund_months(
    liquid_balance: Money,
    monthly_essential_expenses: Money,
) -> CalculationResult:
    if liquid_balance.currency != monthly_essential_expenses.currency:
        raise CurrencyMismatchError(liquid_balance.currency, monthly_essential_expenses.currency)

    warnings: list[str] = []
    months: float | None

    if monthly_essential_expenses.amount_minor <= 0:
        warnings.append("monthly essential expenses is zero or negative; months of coverage is undefined")
        months = None
    else:
        months = liquid_balance.ratio(monthly_essential_expenses)

    return CalculationResult(
        calculation_type="emergency_fund",
        version=CALCULATION_ENGINE_VERSION,
        inputs={"currency": liquid_balance.currency},
        assumptions=[
            "Liquid balance includes only checking and savings account balances.",
            "Monthly essential expenses combine recurring bills, debt minimum "
            "payments, and everyday spending above those bills.",
        ],
        outputs={
            "liquid_balance": liquid_balance,
            "monthly_essential_expenses": monthly_essential_expenses,
            "emergency_fund_months": months,
        },
        warnings=warnings,
    )
