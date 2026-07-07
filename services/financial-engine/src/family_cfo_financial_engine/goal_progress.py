from __future__ import annotations

import math
from dataclasses import dataclass

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult


@dataclass(frozen=True, slots=True)
class GoalInput:
    goal_id: str
    name: str
    target: Money
    current: Money
    monthly_contribution: Money | None = None


def calculate_goal_progress(goal: GoalInput) -> CalculationResult:
    if goal.current.currency != goal.target.currency:
        raise CurrencyMismatchError(goal.target.currency, goal.current.currency)

    remaining = goal.target - goal.current
    warnings: list[str] = []

    if goal.target.amount_minor <= 0:
        warnings.append("goal target is zero or negative; percent complete is undefined")
        percent_complete = None
    else:
        percent_complete = round(goal.current.ratio(goal.target) * 100, 2)

    months_to_completion: int | None = None
    if remaining.amount_minor <= 0:
        months_to_completion = 0
    elif goal.monthly_contribution is not None:
        if goal.monthly_contribution.currency != goal.target.currency:
            raise CurrencyMismatchError(goal.target.currency, goal.monthly_contribution.currency)

        if goal.monthly_contribution.amount_minor > 0:
            months_to_completion = math.ceil(remaining.ratio(goal.monthly_contribution))
        else:
            warnings.append("monthly contribution is zero or negative; completion cannot be projected")

    return CalculationResult(
        calculation_type="goal_progress",
        version=CALCULATION_ENGINE_VERSION,
        inputs={"goal_id": goal.goal_id, "currency": goal.target.currency},
        assumptions=[
            "Completion projection assumes a constant monthly contribution.",
        ],
        outputs={
            "goal_id": goal.goal_id,
            "remaining": remaining,
            "percent_complete": percent_complete,
            "months_to_completion": months_to_completion,
        },
        warnings=warnings,
    )
