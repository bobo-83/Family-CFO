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


# --- M80: parameters cross-checked against Rev. Proc. 2025-32 ---


def test_head_of_household_24_percent_bracket_tops_at_201_750() -> None:
    """Rev. Proc. 2025-32 Table 2: HoH differs from single here ($201,750)."""
    # gross 234,150 - 24,150 deduction = taxable 210,000
    # Table 2: $39,207 + 32% * (210,000 - 201,750) = 39,207 + 2,640 = 41,847
    result = estimate_annual_tax(Money(23_415_000, "USD"), "head_of_household")

    assert result.outputs["taxable_income"] == Money(21_000_000, "USD")
    assert result.outputs["federal_income_tax"] == Money(4_184_700, "USD")


def test_current_year_estimate_carries_no_staleness_warning() -> None:
    from datetime import date

    result = estimate_annual_tax(
        Money(10_000_000, "USD"), "single", today=date(2026, 7, 12)
    )

    assert result.warnings == []
    assert not any("STALE" in a for a in result.assumptions)


def test_next_year_estimate_demands_the_parameter_refresh() -> None:
    from datetime import date

    result = estimate_annual_tax(
        Money(10_000_000, "USD"), "single", today=date(2027, 1, 2)
    )

    assert any("STALE TAX PARAMETERS" in w for w in result.warnings)
    assert any("2027" in a and "tax-year 2026" in a for a in result.assumptions)


# --- M65: state income tax ---


def test_california_single_100k_matches_hand_computation() -> None:
    # 2025 FTB parameters (M80). CA taxable = 100,000 - 5,706 = 94,294
    # 1%*11,079 + 2%*15,185 + 4%*15,188 + 6%*16,090 + 8%*15,182 + 9.3%*21,570
    # = 110.79 + 303.70 + 607.52 + 965.40 + 1,214.56 + 2,006.01 = 5,207.98
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="CA")

    assert result.outputs["state_income_tax"] == Money(520_798, "USD")
    federal = result.outputs["federal_income_tax"].amount_minor
    fica = result.outputs["fica_tax"].amount_minor
    assert result.outputs["total_tax"] == Money(federal + fica + 520_798, "USD")
    assert any("2025 FTB brackets" in a for a in result.assumptions)


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
