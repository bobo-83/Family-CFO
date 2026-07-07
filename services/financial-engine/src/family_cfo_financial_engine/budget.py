from __future__ import annotations

from dataclasses import dataclass

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult


@dataclass(frozen=True, slots=True)
class CategorySpend:
    category: str
    amount: Money


def calculate_budget_summary(
    monthly_income: Money,
    monthly_bills: Money,
    category_spend: list[CategorySpend],
    currency: str,
) -> CalculationResult:
    if monthly_income.currency != currency:
        raise CurrencyMismatchError(currency, monthly_income.currency)
    if monthly_bills.currency != currency:
        raise CurrencyMismatchError(currency, monthly_bills.currency)

    total_spent = Money.zero(currency)
    by_category: dict[str, Money] = {}
    for entry in category_spend:
        if entry.amount.currency != currency:
            raise CurrencyMismatchError(currency, entry.amount.currency)

        total_spent += entry.amount
        by_category[entry.category] = by_category.get(entry.category, Money.zero(currency)) + entry.amount

    remaining = monthly_income - monthly_bills - total_spent

    warnings: list[str] = []
    if remaining.is_negative():
        warnings.append("projected spending exceeds income for the period")

    return CalculationResult(
        calculation_type="budget_summary",
        version=CALCULATION_ENGINE_VERSION,
        inputs={"category_count": len(category_spend), "currency": currency},
        assumptions=[
            "Category spend totals cover the same period as monthly income and bills.",
        ],
        outputs={
            "monthly_income": monthly_income,
            "monthly_bills": monthly_bills,
            "total_spent": total_spent,
            "by_category": by_category,
            "remaining": remaining,
        },
        warnings=warnings,
    )
