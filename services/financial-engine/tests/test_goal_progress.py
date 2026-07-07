import pytest

from family_cfo_financial_engine.goal_progress import GoalInput, calculate_goal_progress
from family_cfo_financial_engine.money import CurrencyMismatchError, Money


def test_goal_progress_computes_percent_and_remaining() -> None:
    goal = GoalInput(
        goal_id="goal-1",
        name="Emergency fund",
        target=Money(1_000_000, "USD"),
        current=Money(250_000, "USD"),
        monthly_contribution=Money(50_000, "USD"),
    )

    result = calculate_goal_progress(goal)

    assert result.outputs["remaining"] == Money(750_000, "USD")
    assert result.outputs["percent_complete"] == 25.0
    assert result.outputs["months_to_completion"] == 15
    assert result.warnings == []


def test_goal_progress_already_complete() -> None:
    goal = GoalInput(
        goal_id="goal-2",
        name="Vacation",
        target=Money(100_000, "USD"),
        current=Money(150_000, "USD"),
    )

    result = calculate_goal_progress(goal)

    assert result.outputs["remaining"] == Money(-50_000, "USD")
    assert result.outputs["months_to_completion"] == 0


def test_goal_progress_without_contribution_leaves_completion_unset() -> None:
    goal = GoalInput(
        goal_id="goal-3",
        name="College",
        target=Money(500_000, "USD"),
        current=Money(100_000, "USD"),
    )

    result = calculate_goal_progress(goal)

    assert result.outputs["months_to_completion"] is None


def test_goal_progress_zero_contribution_warns() -> None:
    goal = GoalInput(
        goal_id="goal-4",
        name="Car",
        target=Money(500_000, "USD"),
        current=Money(100_000, "USD"),
        monthly_contribution=Money.zero("USD"),
    )

    result = calculate_goal_progress(goal)

    assert result.outputs["months_to_completion"] is None
    assert result.warnings


def test_goal_progress_rejects_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatchError):
        calculate_goal_progress(
            GoalInput("goal-5", "x", Money(100, "USD"), Money(10, "EUR"))
        )
