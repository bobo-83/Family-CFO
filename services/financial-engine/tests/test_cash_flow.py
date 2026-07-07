import pytest

from family_cfo_financial_engine.cash_flow import RecurringAmount, calculate_cash_flow
from family_cfo_financial_engine.money import CurrencyMismatchError, Money


def test_cash_flow_normalizes_frequencies_to_monthly() -> None:
    income = [RecurringAmount("salary", Money(600_000, "USD"), "monthly")]
    bills = [
        RecurringAmount("rent", Money(200_000, "USD"), "monthly"),
        RecurringAmount("gym", Money(5_000, "USD"), "weekly"),
    ]

    result = calculate_cash_flow(income, bills, Money(50_000, "USD"), "USD")

    expected_monthly_bills = Money(200_000, "USD") + Money(5_000, "USD").scale(52, 12)
    assert result.outputs["monthly_income"] == Money(600_000, "USD")
    assert result.outputs["monthly_bills"] == expected_monthly_bills
    assert result.outputs["discretionary_cash_flow"] == Money(600_000, "USD") - expected_monthly_bills
    assert result.outputs["net_cash_flow"] == (
        Money(600_000, "USD") - expected_monthly_bills - Money(50_000, "USD")
    )


def test_cash_flow_warns_when_negative() -> None:
    income = [RecurringAmount("salary", Money(100_000, "USD"), "monthly")]
    bills = [RecurringAmount("rent", Money(200_000, "USD"), "monthly")]

    result = calculate_cash_flow(income, bills, Money.zero("USD"), "USD")

    assert result.outputs["net_cash_flow"].is_negative()
    assert "negative" in result.warnings[0]


def test_cash_flow_rejects_unsupported_frequency() -> None:
    income = [RecurringAmount("salary", Money(100_000, "USD"), "daily")]

    with pytest.raises(ValueError):
        calculate_cash_flow(income, [], Money.zero("USD"), "USD")


def test_cash_flow_rejects_currency_mismatch_on_discretionary_spending() -> None:
    with pytest.raises(CurrencyMismatchError):
        calculate_cash_flow([], [], Money.zero("EUR"), "USD")
