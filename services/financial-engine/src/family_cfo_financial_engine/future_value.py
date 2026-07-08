from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from family_cfo_financial_engine.money import Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult


@dataclass(frozen=True, slots=True)
class FutureValueInput:
    """Inputs to a lump-sum future-value / opportunity-cost projection.

    Answers "what would this amount grow to if invested instead" -- the
    opportunity cost of spending it. A single constant-return curve with annual
    compounding and no additional contributions (ADR 0003: honest guidance, not
    planning-grade modeling).
    """

    present_value: Money
    annual_return_rate: float
    years: int


def calculate_future_value(inputs: FutureValueInput) -> CalculationResult:
    currency = inputs.present_value.currency
    if inputs.annual_return_rate < 0:
        raise ValueError("annual_return_rate must not be negative")
    if inputs.years < 0:
        raise ValueError("years must not be negative")

    growth_factor = (Decimal(1) + Decimal(str(inputs.annual_return_rate))) ** inputs.years
    future_minor = int(
        (Decimal(inputs.present_value.amount_minor) * growth_factor).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    future_value = Money(future_minor, currency)
    growth = future_value - inputs.present_value

    return CalculationResult(
        calculation_type="future_value",
        version=CALCULATION_ENGINE_VERSION,
        inputs={
            "annual_return_rate": inputs.annual_return_rate,
            "years": inputs.years,
            "currency": currency,
        },
        assumptions=[
            "Value grows at a constant annual_return_rate, compounded annually.",
            "No additional contributions or withdrawals are modeled.",
        ],
        outputs={"future_value": future_value, "growth": growth},
        warnings=[],
    )
