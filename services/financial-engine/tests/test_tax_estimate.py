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
    assert "State and local taxes are NOT included" in text
    assert "Standard deduction only" in text
