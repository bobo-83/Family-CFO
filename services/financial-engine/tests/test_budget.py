import pytest

from family_cfo_financial_engine.budget import CategorySpend, calculate_budget_summary
from family_cfo_financial_engine.money import CurrencyMismatchError, Money


def test_budget_summary_computes_remaining_and_by_category() -> None:
    spend = [
        CategorySpend("groceries", Money(40_000, "USD")),
        CategorySpend("dining", Money(15_000, "USD")),
        CategorySpend("groceries", Money(5_000, "USD")),
    ]

    result = calculate_budget_summary(
        Money(600_000, "USD"), Money(200_000, "USD"), spend, "USD"
    )

    assert result.outputs["total_spent"] == Money(60_000, "USD")
    assert result.outputs["by_category"]["groceries"] == Money(45_000, "USD")
    assert result.outputs["by_category"]["dining"] == Money(15_000, "USD")
    assert result.outputs["remaining"] == Money(600_000 - 200_000 - 60_000, "USD")
    assert result.warnings == []


def test_budget_summary_warns_when_overspent() -> None:
    spend = [CategorySpend("shopping", Money(500_000, "USD"))]

    result = calculate_budget_summary(
        Money(100_000, "USD"), Money(50_000, "USD"), spend, "USD"
    )

    assert result.outputs["remaining"].is_negative()
    assert "exceeds" in result.warnings[0]


def test_budget_summary_rejects_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatchError):
        calculate_budget_summary(Money(100, "EUR"), Money(0, "USD"), [], "USD")

    with pytest.raises(CurrencyMismatchError):
        calculate_budget_summary(
            Money(100, "USD"), Money(0, "USD"), [CategorySpend("x", Money(1, "EUR"))], "USD"
        )
