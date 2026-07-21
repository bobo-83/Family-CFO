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


# The classic 4% safe-withdrawal heuristic: retirement is "reachable" once
# savings hit 25x annual spending. An honest, widely-taught rule of thumb —
# not planning-grade modeling (ADR 0003) — and the answer says so.
SAFE_WITHDRAWAL_RATE = 0.04
_SOLVE_MAX_AGE = 85


@dataclass(frozen=True)
class RetirementAgeSolveInput:
    """Inputs for solving "WHEN can I retire?" — the earliest age at which
    projected savings reach 25x annual expenses (the 4% rule)."""

    current_age: int
    current_savings: Money
    monthly_contribution: Money
    annual_return_rate: float
    annual_expenses: Money


def solve_retirement_age(inputs: RetirementAgeSolveInput) -> CalculationResult:
    currency = inputs.current_savings.currency
    if inputs.monthly_contribution.currency != currency:
        raise CurrencyMismatchError(currency, inputs.monthly_contribution.currency)
    if inputs.annual_expenses.currency != currency:
        raise CurrencyMismatchError(currency, inputs.annual_expenses.currency)
    if inputs.annual_return_rate < 0:
        raise ValueError("annual_return_rate must not be negative")
    if inputs.annual_expenses.amount_minor <= 0:
        raise ValueError("annual_expenses must be positive")

    required_minor = int(
        (Decimal(inputs.annual_expenses.amount_minor) / Decimal(str(SAFE_WITHDRAWAL_RATE)))
        .to_integral_value(rounding=ROUND_HALF_UP)
    )
    monthly_rate = Decimal(str(inputs.annual_return_rate)) / Decimal(12)

    balance = inputs.current_savings.amount_minor
    earliest_age: int | None = inputs.current_age if balance >= required_minor else None
    if earliest_age is None:
        for month in range(1, (_SOLVE_MAX_AGE - inputs.current_age) * 12 + 1):
            growth = int(
                (Decimal(balance) * monthly_rate).to_integral_value(rounding=ROUND_HALF_UP)
            )
            balance += growth + inputs.monthly_contribution.amount_minor
            if balance >= required_minor:
                # The age reached at this many months in, rounded up to the
                # year boundary the family would actually retire at.
                earliest_age = inputs.current_age + -(-month // 12)
                break

    outputs: dict = {
        "earliest_retirement_age": earliest_age,
        "required_balance": Money(required_minor, currency),
        "projected_balance_when_reached": Money(balance, currency) if earliest_age else None,
        "balance_at_max_age": None if earliest_age else Money(balance, currency),
        "max_age_searched": _SOLVE_MAX_AGE,
        "safe_withdrawal_rate": SAFE_WITHDRAWAL_RATE,
    }
    warnings: list[str] = []
    if earliest_age is None:
        warnings.append(
            f"savings do not reach 25x annual expenses by age {_SOLVE_MAX_AGE} "
            "at the assumed contribution and return"
        )

    return CalculationResult(
        calculation_type="retirement_age_solve",
        version=CALCULATION_ENGINE_VERSION,
        inputs={
            "current_age": inputs.current_age,
            "annual_return_rate": inputs.annual_return_rate,
            "current_savings_minor": inputs.current_savings.amount_minor,
            "monthly_contribution_minor": inputs.monthly_contribution.amount_minor,
            "annual_expenses_minor": inputs.annual_expenses.amount_minor,
            "currency": currency,
        },
        outputs=outputs,
        assumptions=[
            "earliest age uses the 4% safe-withdrawal rule of thumb: retirement is "
            "'reachable' once savings reach 25x annual spending",
            "constant returns; no inflation, tax, or market-sequence modeling — "
            "educational guidance, not a plan",
        ],
        warnings=warnings,
    )
