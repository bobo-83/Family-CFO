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


# --- solve_retirement_age: "WHEN can I retire?" (4% rule) ----------------------

from family_cfo_financial_engine import RetirementAgeSolveInput, solve_retirement_age  # noqa: E402


def test_solver_finds_earliest_age_meeting_25x_expenses() -> None:
    # $500k at 5%, no contributions, $40k/yr spending -> needs $1M (25x).
    # Doubling at 5% monthly compounding takes ~14 years -> earliest ~49.
    result = solve_retirement_age(
        RetirementAgeSolveInput(
            current_age=35,
            current_savings=Money(50_000_000, "USD"),
            monthly_contribution=Money(0, "USD"),
            annual_return_rate=0.05,
            annual_expenses=Money(4_000_000, "USD"),
        )
    )
    age = result.outputs["earliest_retirement_age"]
    assert age is not None and 48 <= age <= 50
    assert result.outputs["required_balance"].amount_minor == 100_000_000
    assert result.outputs["projected_balance_when_reached"].amount_minor >= 100_000_000
    assert any("4%" in a for a in result.assumptions)


def test_solver_already_retirable_returns_current_age() -> None:
    result = solve_retirement_age(
        RetirementAgeSolveInput(
            current_age=60,
            current_savings=Money(120_000_000, "USD"),
            monthly_contribution=Money(0, "USD"),
            annual_return_rate=0.05,
            annual_expenses=Money(4_000_000, "USD"),
        )
    )
    assert result.outputs["earliest_retirement_age"] == 60


def test_solver_reports_unreachable_by_max_age() -> None:
    result = solve_retirement_age(
        RetirementAgeSolveInput(
            current_age=35,
            current_savings=Money(100_000, "USD"),
            monthly_contribution=Money(0, "USD"),
            annual_return_rate=0.0,
            annual_expenses=Money(10_000_000, "USD"),
        )
    )
    assert result.outputs["earliest_retirement_age"] is None
    assert result.outputs["balance_at_max_age"].amount_minor == 100_000
    assert result.warnings


def test_solver_rejects_nonpositive_expenses() -> None:
    import pytest

    with pytest.raises(ValueError):
        solve_retirement_age(
            RetirementAgeSolveInput(
                current_age=35,
                current_savings=Money(1, "USD"),
                monthly_contribution=Money(0, "USD"),
                annual_return_rate=0.05,
                annual_expenses=Money(0, "USD"),
            )
        )
