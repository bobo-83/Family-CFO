import pytest

from family_cfo_financial_engine.money import CurrencyMismatchError, Money


def test_add_preserves_integer_precision() -> None:
    total = Money(1_999, "USD") + Money(1, "USD")

    assert total == Money(2_000, "USD")
    assert isinstance(total.amount_minor, int)


def test_sub_and_neg() -> None:
    assert Money(500, "USD") - Money(700, "USD") == Money(-200, "USD")
    assert -Money(500, "USD") == Money(-500, "USD")


def test_mul_by_int_scalar() -> None:
    assert Money(300, "USD") * 3 == Money(900, "USD")
    assert 3 * Money(300, "USD") == Money(900, "USD")


def test_mul_rejects_float_factor() -> None:
    with pytest.raises(TypeError):
        Money(300, "USD") * 1.5


def test_currency_is_normalized_to_uppercase() -> None:
    assert Money(100, "usd").currency == "USD"


def test_invalid_currency_code_rejected() -> None:
    with pytest.raises(ValueError):
        Money(100, "US")


def test_non_int_amount_rejected() -> None:
    with pytest.raises(TypeError):
        Money(10.5, "USD")


def test_add_currency_mismatch_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(100, "USD") + Money(100, "EUR")


def test_comparison_currency_mismatch_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(100, "USD") < Money(100, "EUR")


def test_ordering() -> None:
    assert Money(100, "USD") < Money(200, "USD")
    assert Money(200, "USD") >= Money(200, "USD")


def test_scale_rounds_half_up() -> None:
    # 100 minor units * 1/3 = 33.33... -> rounds to 33
    assert Money(100, "USD").scale(1, 3) == Money(33, "USD")
    # 5 minor units * 1/2 = 2.5 -> rounds half up to 3
    assert Money(5, "USD").scale(1, 2) == Money(3, "USD")


def test_scale_by_zero_denominator_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        Money(100, "USD").scale(1, 0)


def test_ratio_returns_float() -> None:
    assert Money(50, "USD").ratio(Money(200, "USD")) == 0.25


def test_ratio_currency_mismatch_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(50, "USD").ratio(Money(200, "EUR"))


def test_ratio_by_zero_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        Money(50, "USD").ratio(Money.zero("USD"))


def test_is_negative_and_is_zero() -> None:
    assert Money(-1, "USD").is_negative()
    assert not Money(1, "USD").is_negative()
    assert Money.zero("USD").is_zero()


def test_to_dict() -> None:
    assert Money(1234, "USD").to_dict() == {"amount_minor": 1234, "currency": "USD"}
