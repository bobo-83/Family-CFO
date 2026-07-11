import pytest

from family_cfo_financial_engine import Money, estimate_annual_tax, gross_up_from_net


def test_married_joint_150k_matches_hand_computation() -> None:
    # taxable = 150,000 - 32,200 = 117,800
    # federal = 10%*24,800 + 12%*76,000 + 22%*17,000 = 2,480 + 9,120 + 3,740 = 15,340
    # FICA    = 6.2%*150,000 + 1.45%*150,000 = 9,300 + 2,175 = 11,475
    result = estimate_annual_tax(Money(15_000_000, "USD"), "married_joint")

    outputs = result.outputs
    assert outputs["taxable_income"] == Money(11_780_000, "USD")
    assert outputs["federal_income_tax"] == Money(1_534_000, "USD")
    assert outputs["fica_tax"] == Money(1_147_500, "USD")
    assert outputs["total_tax"] == Money(2_681_500, "USD")
    assert outputs["effective_rate"] == pytest.approx(0.1788, abs=0.0001)


def test_single_50k_matches_hand_computation() -> None:
    # taxable = 50,000 - 16,100 = 33,900
    # federal = 10%*12,400 + 12%*21,500 = 1,240 + 2,580 = 3,820
    # FICA    = 6.2%*50,000 + 1.45%*50,000 = 3,100 + 725 = 3,825
    result = estimate_annual_tax(Money(5_000_000, "USD"), "single")

    outputs = result.outputs
    assert outputs["federal_income_tax"] == Money(382_000, "USD")
    assert outputs["fica_tax"] == Money(382_500, "USD")
    assert outputs["total_tax"] == Money(764_500, "USD")


def test_social_security_caps_at_wage_base_and_additional_medicare_applies() -> None:
    # gross 300,000 single: SS = 6.2% * 184,500 = 11,439;
    # medicare = 1.45%*300,000 + 0.9%*(300,000-200,000) = 4,350 + 900 = 5,250
    result = estimate_annual_tax(Money(30_000_000, "USD"), "single")

    assert result.outputs["fica_tax"] == Money(1_668_900, "USD")


def test_zero_income_is_all_zeros() -> None:
    result = estimate_annual_tax(Money(0, "USD"), "married_joint")

    assert result.outputs["total_tax"] == Money(0, "USD")
    assert result.outputs["effective_rate"] == 0.0


def test_invalid_filing_status_raises() -> None:
    with pytest.raises(ValueError):
        estimate_annual_tax(Money(1_000_00, "USD"), "married_separate")


def test_gross_up_round_trips() -> None:
    gross = estimate_annual_tax(Money(15_000_000, "USD"), "married_joint")
    net_minor = 15_000_000 - gross.outputs["total_tax"].amount_minor

    recovered = gross_up_from_net(Money(net_minor, "USD"), "married_joint")

    assert abs(recovered.outputs["gross_income"].amount_minor - 15_000_000) <= 200  # within $2
    assert recovered.outputs["net_income"] == Money(net_minor, "USD")
    assert any("TAKE-HOME" in a for a in recovered.assumptions)


def test_assumptions_disclose_the_limits() -> None:
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single")

    text = " ".join(result.assumptions)
    assert "state income tax is NOT included" in text  # no state set
    assert "Standard deduction only" in text


# --- M65: state income tax ---


def test_california_single_100k_matches_hand_computation() -> None:
    # CA taxable = 100,000 - 5,540 = 94,460
    # 1%*10,756 + 2%*14,743 + 4%*14,746 + 6%*15,621 + 8%*14,740 + 9.3%*23,854
    # = 107.56 + 294.86 + 589.84 + 937.26 + 1,179.20 + 2,218.42 = 5,327.14
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="CA")

    assert result.outputs["state_income_tax"] == Money(532_714, "USD")
    federal = result.outputs["federal_income_tax"].amount_minor
    fica = result.outputs["fica_tax"].amount_minor
    assert result.outputs["total_tax"] == Money(federal + fica + 532_714, "USD")
    assert any("2024 FTB brackets" in a for a in result.assumptions)


def test_no_wage_tax_state_is_zero_with_note() -> None:
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="TX")

    assert result.outputs["state_income_tax"] == Money(0, "USD")
    assert any("no state income tax on wages" in a for a in result.assumptions)


def test_unmodeled_state_warns_instead_of_silent_zero() -> None:
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="NY")

    assert result.outputs["state_income_tax"] is None
    assert any("not modeled yet" in a for a in result.assumptions)


def test_gross_up_includes_state_tax() -> None:
    with_state = gross_up_from_net(Money(12_000_000, "USD"), "married_joint", state="CA")
    without = gross_up_from_net(Money(12_000_000, "USD"), "married_joint")

    assert (
        with_state.outputs["gross_income"].amount_minor
        > without.outputs["gross_income"].amount_minor
    )
    net = with_state.outputs["net_income"].amount_minor
    gross = with_state.outputs["gross_income"].amount_minor
    total = with_state.outputs["total_tax"].amount_minor
    assert abs(gross - total - net) <= 2
