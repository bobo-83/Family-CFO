from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult

# Below this many years of covered expenses we flag the projection as thin.
LOW_COVERAGE_YEARS = 20


@dataclass(frozen=True, slots=True)
class RetirementInput:
    """Inputs to a deterministic retirement savings projection.

    A single constant-return growth curve — no inflation, tax, drawdown, or
    sequence-of-returns modeling. Honest educational guidance, not
    planning-grade modeling (ADR 0003).
    """

    current_age: int
    retirement_age: int
    current_savings: Money
    monthly_contribution: Money
    annual_return_rate: float
    annual_expenses: Money | None = None


def calculate_retirement_projection(inputs: RetirementInput) -> CalculationResult:
    currency = inputs.current_savings.currency
    if inputs.monthly_contribution.currency != currency:
        raise CurrencyMismatchError(currency, inputs.monthly_contribution.currency)
    if inputs.annual_expenses is not None and inputs.annual_expenses.currency != currency:
        raise CurrencyMismatchError(currency, inputs.annual_expenses.currency)
    if inputs.retirement_age <= inputs.current_age:
        raise ValueError("retirement_age must be greater than current_age")
    if inputs.annual_return_rate < 0:
        raise ValueError("annual_return_rate must not be negative")

    months = (inputs.retirement_age - inputs.current_age) * 12
    monthly_rate = Decimal(str(inputs.annual_return_rate)) / Decimal(12)

    balance = inputs.current_savings.amount_minor
    for _ in range(months):
        growth = int((Decimal(balance) * monthly_rate).to_integral_value(rounding=ROUND_HALF_UP))
        balance += growth
        balance += inputs.monthly_contribution.amount_minor

    projected = Money(balance, currency)
    outputs: dict = {
        "projected_balance": projected,
        "months_to_retirement": months,
    }
    warnings: list[str] = []

    if inputs.annual_expenses is not None and inputs.annual_expenses.amount_minor > 0:
        years_covered = round(projected.ratio(inputs.annual_expenses), 1)
        outputs["years_of_expenses_covered"] = years_covered
        if years_covered < LOW_COVERAGE_YEARS:
            warnings.append(
                "projected savings cover fewer than "
                f"{LOW_COVERAGE_YEARS} years of retirement expenses at the supplied spending"
            )
    else:
        outputs["years_of_expenses_covered"] = None

    return CalculationResult(
        calculation_type="retirement_projection",
        version=CALCULATION_ENGINE_VERSION,
        inputs={
            "current_age": inputs.current_age,
            "retirement_age": inputs.retirement_age,
            "annual_return_rate": inputs.annual_return_rate,
            "currency": currency,
        },
        assumptions=[
            "Savings grow monthly at annual_return_rate / 12 on the running balance.",
            "The monthly contribution is added every month until retirement.",
            "No inflation, taxes, drawdown, or sequence-of-returns effects are modeled.",
            "Expense coverage is projected_balance / annual_expenses, a simple ratio, not a drawdown simulation.",
        ],
        outputs=outputs,
        warnings=warnings,
    )
