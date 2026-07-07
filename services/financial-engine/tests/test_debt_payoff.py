import pytest

from family_cfo_financial_engine.debt_payoff import DebtInput, calculate_debt_payoff
from family_cfo_financial_engine.money import CurrencyMismatchError, Money


def test_debt_payoff_zero_balance_is_already_paid_off() -> None:
    debt = DebtInput(
        debt_id="debt-1",
        name="Paid off card",
        balance=Money.zero("USD"),
        annual_interest_rate=0.1999,
        minimum_payment=Money(2_500, "USD"),
    )

    result = calculate_debt_payoff(debt)

    assert result.calculation_type == "debt_payoff"
    assert result.outputs["months_to_payoff"] == 0
    assert result.outputs["total_interest_paid"] == Money.zero("USD")
    assert result.warnings == []


def test_debt_payoff_zero_interest_rate_is_simple_division() -> None:
    debt = DebtInput(
        debt_id="debt-2",
        name="Interest-free loan",
        balance=Money(120_000, "USD"),
        annual_interest_rate=0.0,
        minimum_payment=Money(10_000, "USD"),
    )

    result = calculate_debt_payoff(debt)

    assert result.outputs["months_to_payoff"] == 12
    assert result.outputs["total_interest_paid"] == Money.zero("USD")
    assert result.warnings == []


def test_debt_payoff_with_interest_accrues_and_pays_off() -> None:
    debt = DebtInput(
        debt_id="debt-3",
        name="Credit card",
        balance=Money(500_000, "USD"),
        annual_interest_rate=0.24,
        minimum_payment=Money(25_000, "USD"),
    )

    result = calculate_debt_payoff(debt)

    assert result.outputs["months_to_payoff"] is not None
    assert result.outputs["months_to_payoff"] > 0
    assert result.outputs["total_interest_paid"].amount_minor > 0
    assert result.warnings == []


def test_debt_payoff_extra_payment_reduces_months() -> None:
    base_debt = DebtInput(
        debt_id="debt-4",
        name="Auto loan",
        balance=Money(1_000_000, "USD"),
        annual_interest_rate=0.06,
        minimum_payment=Money(30_000, "USD"),
    )
    with_extra = DebtInput(
        debt_id="debt-4",
        name="Auto loan",
        balance=Money(1_000_000, "USD"),
        annual_interest_rate=0.06,
        minimum_payment=Money(30_000, "USD"),
        extra_monthly_payment=Money(20_000, "USD"),
    )

    base_result = calculate_debt_payoff(base_debt)
    extra_result = calculate_debt_payoff(with_extra)

    assert extra_result.outputs["months_to_payoff"] < base_result.outputs["months_to_payoff"]
    assert extra_result.outputs["total_interest_paid"] < base_result.outputs["total_interest_paid"]


def test_debt_payoff_payment_below_interest_warns_and_leaves_outputs_unset() -> None:
    debt = DebtInput(
        debt_id="debt-5",
        name="Stuck balance",
        balance=Money(1_000_000, "USD"),
        annual_interest_rate=0.30,
        minimum_payment=Money(1_000, "USD"),
    )

    result = calculate_debt_payoff(debt)

    assert result.outputs["months_to_payoff"] is None
    assert result.outputs["total_interest_paid"] is None
    assert any("does not cover accruing interest" in w for w in result.warnings)


def test_debt_payoff_zero_payment_warns() -> None:
    debt = DebtInput(
        debt_id="debt-6",
        name="No payment",
        balance=Money(100_000, "USD"),
        annual_interest_rate=0.1,
        minimum_payment=Money.zero("USD"),
    )

    result = calculate_debt_payoff(debt)

    assert result.outputs["months_to_payoff"] is None
    assert any("will never be paid off" in w for w in result.warnings)


def test_debt_payoff_rejects_negative_interest_rate() -> None:
    debt = DebtInput(
        debt_id="debt-7",
        name="Invalid",
        balance=Money(100_000, "USD"),
        annual_interest_rate=-0.01,
        minimum_payment=Money(5_000, "USD"),
    )

    with pytest.raises(ValueError):
        calculate_debt_payoff(debt)


def test_debt_payoff_rejects_currency_mismatch_on_minimum_payment() -> None:
    debt = DebtInput(
        debt_id="debt-8",
        name="Mismatch",
        balance=Money(100_000, "USD"),
        annual_interest_rate=0.1,
        minimum_payment=Money(5_000, "EUR"),
    )

    with pytest.raises(CurrencyMismatchError):
        calculate_debt_payoff(debt)


def test_debt_payoff_rejects_currency_mismatch_on_extra_payment() -> None:
    debt = DebtInput(
        debt_id="debt-9",
        name="Mismatch",
        balance=Money(100_000, "USD"),
        annual_interest_rate=0.1,
        minimum_payment=Money(5_000, "USD"),
        extra_monthly_payment=Money(1_000, "EUR"),
    )

    with pytest.raises(CurrencyMismatchError):
        calculate_debt_payoff(debt)
