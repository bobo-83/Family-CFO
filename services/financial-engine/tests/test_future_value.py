import pytest

from family_cfo_financial_engine import FutureValueInput, Money, calculate_future_value


def test_zero_years_is_the_present_value() -> None:
    result = calculate_future_value(
        FutureValueInput(present_value=Money(100_000, "USD"), annual_return_rate=0.06, years=0)
    )
    assert result.outputs["future_value"] == Money(100_000, "USD")
    assert result.outputs["growth"] == Money(0, "USD")


def test_zero_rate_does_not_grow() -> None:
    result = calculate_future_value(
        FutureValueInput(present_value=Money(100_000, "USD"), annual_return_rate=0.0, years=30)
    )
    assert result.outputs["future_value"] == Money(100_000, "USD")


def test_known_compound_growth() -> None:
    # $1,000 at 6% for 20 years -> 1000 * 1.06^20 = 3207.135...
    result = calculate_future_value(
        FutureValueInput(present_value=Money(100_000, "USD"), annual_return_rate=0.06, years=20)
    )
    assert result.outputs["future_value"] == Money(320_714, "USD")
    assert result.outputs["growth"] == Money(320_714 - 100_000, "USD")


def test_negative_rate_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_future_value(
            FutureValueInput(present_value=Money(1, "USD"), annual_return_rate=-0.01, years=5)
        )


def test_negative_years_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_future_value(
            FutureValueInput(present_value=Money(1, "USD"), annual_return_rate=0.05, years=-1)
        )
