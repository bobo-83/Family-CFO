import pytest

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.safe_to_spend import (
    SafeToSpendInputs,
    calculate_safe_to_spend,
)


def _inputs(**overrides) -> SafeToSpendInputs:
    base = {
        "liquid_balance": Money(810_343, "USD"),
        "emergency_fund_reserved": Money(115_411, "USD"),
        "bills_due": Money(0, "USD"),
        "minimum_debt_payments": Money(0, "USD"),
    }
    base.update(overrides)
    return SafeToSpendInputs(**base)


def test_bills_and_debt_are_subtracted_not_just_the_emergency_fund() -> None:
    """The reported bug: with $8,103.43 liquid and a $1,154.11 emergency fund, the
    advisor called $6,949.32 'truly available' — ignoring every bill about to land
    and every minimum debt payment owed."""
    result = calculate_safe_to_spend(
        _inputs(
            bills_due=Money(240_000, "USD"),
            minimum_debt_payments=Money(45_000, "USD"),
        )
    )

    # NOT 810_343 - 115_411 = 694_932.
    assert result.outputs["safe_to_spend"] == Money(409_932, "USD")
    assert result.outputs["committed_total"] == Money(400_411, "USD")


def test_every_component_is_reported_so_the_answer_can_be_explained() -> None:
    result = calculate_safe_to_spend(
        _inputs(bills_due=Money(100_000, "USD"), minimum_debt_payments=Money(50_000, "USD"))
    )

    assert result.outputs["liquid_balance"] == Money(810_343, "USD")
    assert result.outputs["emergency_fund_reserved"] == Money(115_411, "USD")
    assert result.outputs["bills_due"] == Money(100_000, "USD")
    assert result.outputs["minimum_debt_payments"] == Money(50_000, "USD")


def test_obligations_exceeding_cash_is_a_warning_not_a_cheerful_number() -> None:
    result = calculate_safe_to_spend(
        _inputs(bills_due=Money(900_000, "USD"), minimum_debt_payments=Money(50_000, "USD"))
    )

    assert result.outputs["safe_to_spend"].is_negative()
    assert any("no discretionary money" in w for w in result.warnings)


def test_debts_without_a_minimum_payment_are_flagged_as_understating_the_figure() -> None:
    result = calculate_safe_to_spend(_inputs(unmodeled_debt_count=2))

    assert any("UNDERSTATED" in w for w in result.warnings)


def test_no_designated_emergency_fund_says_nothing_is_protected() -> None:
    result = calculate_safe_to_spend(
        _inputs(emergency_fund_reserved=Money.zero("USD"), bills_due=Money(1, "USD"))
    )

    assert any("protects no reserve" in w for w in result.warnings)


def test_no_bills_recorded_warns_the_figure_may_be_overstated() -> None:
    result = calculate_safe_to_spend(_inputs(bills_due=Money.zero("USD")))

    assert any("overstated" in w for w in result.warnings)


def test_income_is_not_counted_and_the_assumption_says_so() -> None:
    result = calculate_safe_to_spend(_inputs())

    assert any("Income expected during the window is NOT counted" in a for a in result.assumptions)


def test_mixed_currencies_are_refused_rather_than_silently_summed() -> None:
    with pytest.raises(CurrencyMismatchError):
        calculate_safe_to_spend(_inputs(bills_due=Money(100, "EUR")))


def test_outstanding_debt_is_reported_even_though_it_is_not_subtracted() -> None:
    """A balance is not due this month, so it isn't subtracted — but "you have
    $6,765 to spend" said beside a silent $29,931 of card debt is a true sentence
    that misleads. Both numbers must reach the family."""
    result = calculate_safe_to_spend(_inputs(total_debt=Money(2_993_144, "USD")))

    assert result.outputs["total_debt"] == Money(2_993_144, "USD")
    assert any("owes 29,931.44 USD" in w for w in result.warnings)
    assert any("never on its own" in w for w in result.warnings)


def test_the_unpayable_debt_warning_names_the_amount() -> None:
    result = calculate_safe_to_spend(
        _inputs(unmodeled_debt_count=3, unmodeled_debt_total=Money(2_993_144, "USD"))
    )

    warning = next(w for w in result.warnings if "UNDERSTATED" in w)
    assert "3 liability account(s)" in warning
    assert "29,931.44 USD" in warning
    assert "LOWER than shown" in warning


def test_no_debt_means_no_debt_warning() -> None:
    result = calculate_safe_to_spend(_inputs(total_debt=Money.zero("USD"), bills_due=Money(1, "USD")))

    assert not any("owes" in w for w in result.warnings)
    assert result.outputs["total_debt"] == Money.zero("USD")
