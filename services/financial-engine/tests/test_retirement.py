import pytest

from family_cfo_financial_engine import (
    CurrencyMismatchError,
    Money,
    RetirementInput,
    calculate_retirement_projection,
)


def test_zero_return_is_just_contributions_plus_starting_balance() -> None:
    result = calculate_retirement_projection(
        RetirementInput(
            current_age=30,
            retirement_age=31,
            current_savings=Money(100_000, "USD"),
            monthly_contribution=Money(1_000, "USD"),
            annual_return_rate=0.0,
        )
    )

    # 12 months of $10 contributions on top of $1,000, no growth.
    assert result.outputs["months_to_retirement"] == 12
    assert result.outputs["projected_balance"] == Money(100_000 + 12 * 1_000, "USD")
    assert result.outputs["years_of_expenses_covered"] is None


def test_positive_return_grows_the_balance_beyond_contributions() -> None:
    result = calculate_retirement_projection(
        RetirementInput(
            current_age=40,
            retirement_age=65,
            current_savings=Money(5_000_000, "USD"),
            monthly_contribution=Money(50_000, "USD"),
            annual_return_rate=0.06,
        )
    )

    contributions_only = 5_000_000 + 25 * 12 * 50_000
    assert result.outputs["projected_balance"].amount_minor > contributions_only
    assert result.outputs["projected_balance"].currency == "USD"


def test_expense_coverage_ratio_and_low_coverage_warning() -> None:
    result = calculate_retirement_projection(
        RetirementInput(
            current_age=60,
            retirement_age=61,
            current_savings=Money(10_000_000, "USD"),  # $100,000
            monthly_contribution=Money(0, "USD"),
            annual_return_rate=0.0,
            annual_expenses=Money(5_000_000, "USD"),  # $50,000/yr -> ~2 years
        )
    )

    assert result.outputs["years_of_expenses_covered"] == pytest.approx(2.0)
    assert any("fewer than" in w for w in result.warnings)


def test_ample_coverage_has_no_warning() -> None:
    result = calculate_retirement_projection(
        RetirementInput(
            current_age=60,
            retirement_age=61,
            current_savings=Money(200_000_000, "USD"),  # $2,000,000
            monthly_contribution=Money(0, "USD"),
            annual_return_rate=0.0,
            annual_expenses=Money(5_000_000, "USD"),  # 40 years
        )
    )

    assert result.outputs["years_of_expenses_covered"] == pytest.approx(40.0)
    assert result.warnings == []


def test_retirement_age_must_exceed_current_age() -> None:
    with pytest.raises(ValueError):
        calculate_retirement_projection(
            RetirementInput(
                current_age=65,
                retirement_age=65,
                current_savings=Money(0, "USD"),
                monthly_contribution=Money(0, "USD"),
                annual_return_rate=0.05,
            )
        )


def test_negative_return_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_retirement_projection(
            RetirementInput(
                current_age=30,
                retirement_age=60,
                current_savings=Money(0, "USD"),
                monthly_contribution=Money(0, "USD"),
                annual_return_rate=-0.01,
            )
        )


def test_currency_mismatch_is_rejected() -> None:
    with pytest.raises(CurrencyMismatchError):
        calculate_retirement_projection(
            RetirementInput(
                current_age=30,
                retirement_age=60,
                current_savings=Money(0, "USD"),
                monthly_contribution=Money(0, "EUR"),
                annual_return_rate=0.05,
            )
        )
