from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult

MAX_SIMULATED_MONTHS = 1_200  # 100 years; a safety cap, not a real-world assumption.


@dataclass(frozen=True, slots=True)
class DebtInput:
    """Inputs to a debt payoff simulation.

    ``balance`` is the positive amount owed. Rates and payments are supplied
    by the caller (mocked/synthetic in tests, or account-level fields once
    the schema persists them) — this function has no database dependency.
    """

    debt_id: str
    name: str
    balance: Money
    annual_interest_rate: float
    minimum_payment: Money
    extra_monthly_payment: Money | None = None


def calculate_debt_payoff(debt: DebtInput) -> CalculationResult:
    currency = debt.balance.currency
    if debt.minimum_payment.currency != currency:
        raise CurrencyMismatchError(currency, debt.minimum_payment.currency)

    extra_payment = debt.extra_monthly_payment or Money.zero(currency)
    if extra_payment.currency != currency:
        raise CurrencyMismatchError(currency, extra_payment.currency)

    if debt.annual_interest_rate < 0:
        raise ValueError("annual_interest_rate must not be negative")

    inputs = {
        "debt_id": debt.debt_id,
        "annual_interest_rate": debt.annual_interest_rate,
        "currency": currency,
    }
    assumptions = [
        "Interest accrues monthly on the remaining balance at annual_interest_rate / 12.",
        "The full minimum payment plus any extra payment is applied every month until payoff.",
    ]

    if debt.balance.amount_minor <= 0:
        return CalculationResult(
            calculation_type="debt_payoff",
            version=CALCULATION_ENGINE_VERSION,
            inputs=inputs,
            assumptions=assumptions,
            outputs={"months_to_payoff": 0, "total_interest_paid": Money.zero(currency)},
            warnings=[],
        )

    monthly_payment = debt.minimum_payment + extra_payment
    monthly_rate = Decimal(str(debt.annual_interest_rate)) / Decimal(12)

    if monthly_payment.amount_minor <= 0:
        return CalculationResult(
            calculation_type="debt_payoff",
            version=CALCULATION_ENGINE_VERSION,
            inputs=inputs,
            assumptions=assumptions,
            outputs={"months_to_payoff": None, "total_interest_paid": None},
            warnings=["monthly payment is zero or negative; the balance will never be paid off"],
        )

    remaining = debt.balance.amount_minor
    total_interest = 0
    months = 0
    warnings: list[str] = []

    while remaining > 0 and months < MAX_SIMULATED_MONTHS:
        interest = int(
            (Decimal(remaining) * monthly_rate).to_integral_value(rounding=ROUND_HALF_UP)
        )
        remaining += interest
        total_interest += interest
        payment = min(monthly_payment.amount_minor, remaining)
        remaining -= payment
        months += 1

        if interest > 0 and payment <= interest and remaining > 0:
            warnings.append(
                "the monthly payment does not cover accruing interest; the balance will not be paid off"
            )
            return CalculationResult(
                calculation_type="debt_payoff",
                version=CALCULATION_ENGINE_VERSION,
                inputs=inputs,
                assumptions=assumptions,
                outputs={"months_to_payoff": None, "total_interest_paid": None},
                warnings=warnings,
            )

    if remaining > 0:
        warnings.append(
            f"balance was not paid off within {MAX_SIMULATED_MONTHS} months at the supplied payment"
        )
        months_to_payoff = None
        interest_output = None
    else:
        months_to_payoff = months
        interest_output = Money(total_interest, currency)

    return CalculationResult(
        calculation_type="debt_payoff",
        version=CALCULATION_ENGINE_VERSION,
        inputs=inputs,
        assumptions=assumptions,
        outputs={"months_to_payoff": months_to_payoff, "total_interest_paid": interest_output},
        warnings=warnings,
    )
