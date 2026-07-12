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


def test_massachusetts_single_100k_matches_hand_computation() -> None:
    # M81, 2026 DOR parameters.
    # MA taxable = 100,000 - 4,400 exemption - 2,000 FICA deduction = 93,600
    # tax = 5% * 93,600 = 4,680.00 (below the surtax threshold)
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="MA")

    assert result.outputs["state_income_tax"] == Money(468_000, "USD")
    assert any("Massachusetts" in a and "PFML" in a for a in result.assumptions)


def test_massachusetts_surtax_applies_above_the_indexed_threshold() -> None:
    # married_joint gross 1,500,000:
    # taxable = 1,500,000 - 8,800 - 4,000 = 1,487,200
    # tax = 5% * 1,487,200 + 4% * (1,487,200 - 1,107,750)
    #     = 74,360 + 15,178 = 89,538
    result = estimate_annual_tax(Money(150_000_000, "USD"), "married_joint", state="MA")

    assert result.outputs["state_income_tax"] == Money(8_953_800, "USD")
    assert any("1,107,750" in a for a in result.assumptions)


def test_no_wage_tax_state_is_zero_with_note() -> None:
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="TX")

    assert result.outputs["state_income_tax"] == Money(0, "USD")
    assert any("no state income tax on wages" in a for a in result.assumptions)


def test_unknown_state_code_warns_instead_of_silent_zero() -> None:
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="PR")

    assert result.outputs["state_income_tax"] is None
    assert any("not modeled yet" in a for a in result.assumptions)


# --- M82: every state + DC is modeled ---

_ALL_JURISDICTIONS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
]


def test_every_state_and_dc_produces_a_state_tax_figure() -> None:
    assert len(_ALL_JURISDICTIONS) == 51
    for code in _ALL_JURISDICTIONS:
        for status in ("single", "married_joint", "head_of_household"):
            result = estimate_annual_tax(Money(15_000_000, "USD"), status, state=code)
            tax = result.outputs["state_income_tax"]
            assert tax is not None, f"{code}/{status} returned no state tax"
            assert tax.amount_minor >= 0, f"{code}/{status} negative"
            assert not any("not modeled yet" in a for a in result.assumptions), code


def test_illinois_flat_rate_matches_hand_computation() -> None:
    # IL: (100,000 - 2,925 exemption) * 4.95% = 4,805.21 (rounded)
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="IL")

    assert result.outputs["state_income_tax"] == Money(480_521, "USD")
    assert any("Tax Foundation 2026 compilation" in a for a in result.assumptions)


def test_new_york_single_100k_matches_hand_computation() -> None:
    # NY taxable = 100,000 - 8,000 = 92,000
    # 3.9%*8,500 + 4.4%*3,200 + 5.15%*2,200 + 5.4%*66,750 + 5.9%*11,350
    # = 331.50 + 140.80 + 113.30 + 3,604.50 + 669.65 = 4,859.75
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="NY")

    assert result.outputs["state_income_tax"] == Money(485_975, "USD")
    assert any("New York City" in a for a in result.assumptions)


def test_oregon_personal_credit_reduces_the_tax() -> None:
    # OR taxable = 100,000 - 2,910 = 97,090
    # 4.75%*4,550 + 6.75%*6,850 + 8.75%*85,690 = 216.13+462.38+7,497.88
    # = 8,176.375 - 256 credit = 7,920.375 -> 7,920.38
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="OR")

    assert result.outputs["state_income_tax"] == Money(792_038, "USD")


def test_hawaii_married_doubles_the_single_thresholds() -> None:
    single = estimate_annual_tax(Money(20_000_000, "USD"), "single", state="HI")
    married = estimate_annual_tax(Money(20_000_000, "USD"), "married_joint", state="HI")

    assert (
        married.outputs["state_income_tax"].amount_minor
        < single.outputs["state_income_tax"].amount_minor
    )


def test_maryland_warns_about_mandatory_county_tax() -> None:
    result = estimate_annual_tax(Money(10_000_000, "USD"), "single", state="MD")

    assert result.outputs["state_income_tax"].amount_minor > 0
    assert any("MANDATORY local income tax" in a for a in result.assumptions)


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
