import pytest

from family_cfo_financial_engine.emergency_fund import calculate_emergency_fund_months
from family_cfo_financial_engine.money import CurrencyMismatchError, Money


def test_emergency_fund_months_computed() -> None:
    result = calculate_emergency_fund_months(Money(900_000, "USD"), Money(300_000, "USD"))

    assert result.outputs["emergency_fund_months"] == 3.0
    assert result.warnings == []


def test_emergency_fund_months_zero_expenses_is_undefined() -> None:
    result = calculate_emergency_fund_months(Money(900_000, "USD"), Money.zero("USD"))

    assert result.outputs["emergency_fund_months"] is None
    assert result.warnings


def test_emergency_fund_rejects_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatchError):
        calculate_emergency_fund_months(Money(900_000, "USD"), Money(300_000, "EUR"))
