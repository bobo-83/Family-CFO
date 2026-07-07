from __future__ import annotations

from dataclasses import dataclass

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult

# (occurrences per year, months per year) used to normalize a recurring amount to a monthly figure.
MONTHLY_FACTORS: dict[str, tuple[int, int]] = {
    "weekly": (52, 12),
    "biweekly": (26, 12),
    "semimonthly": (24, 12),
    "monthly": (1, 1),
    "quarterly": (1, 3),
    "annual": (1, 12),
}


@dataclass(frozen=True, slots=True)
class RecurringAmount:
    name: str
    amount: Money
    frequency: str

    def monthly_amount(self) -> Money:
        if self.frequency not in MONTHLY_FACTORS:
            raise ValueError(f"unsupported recurring frequency: {self.frequency!r}")

        numerator, denominator = MONTHLY_FACTORS[self.frequency]
        return self.amount.scale(numerator, denominator)


def calculate_cash_flow(
    income: list[RecurringAmount],
    bills: list[RecurringAmount],
    discretionary_spending: Money,
    currency: str,
) -> CalculationResult:
    monthly_income = Money.zero(currency)
    for item in income:
        monthly_income += item.monthly_amount()

    monthly_bills = Money.zero(currency)
    for item in bills:
        monthly_bills += item.monthly_amount()

    if discretionary_spending.currency != currency:
        raise CurrencyMismatchError(currency, discretionary_spending.currency)

    discretionary_cash_flow = monthly_income - monthly_bills
    net_cash_flow = discretionary_cash_flow - discretionary_spending

    warnings: list[str] = []
    if net_cash_flow.is_negative():
        warnings.append("projected monthly cash flow is negative")

    return CalculationResult(
        calculation_type="cash_flow",
        version=CALCULATION_ENGINE_VERSION,
        inputs={"income_count": len(income), "bill_count": len(bills), "currency": currency},
        assumptions=[
            "Recurring income and bills are normalized to a monthly amount using a 12-month year.",
            "Discretionary spending is the caller-supplied non-bill transaction total for the period.",
        ],
        outputs={
            "monthly_income": monthly_income,
            "monthly_bills": monthly_bills,
            "discretionary_spending": discretionary_spending,
            "discretionary_cash_flow": discretionary_cash_flow,
            "net_cash_flow": net_cash_flow,
        },
        warnings=warnings,
    )
