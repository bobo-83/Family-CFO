from family_cfo_ai_orchestrator.guardrails import (
    find_unattributed_numbers,
    known_values_from_facts,
    known_values_from_report_facts,
    validate_recommendation,
)
from family_cfo_ai_orchestrator.prompts import PurchaseFacts, ReportFacts


def _facts() -> PurchaseFacts:
    return PurchaseFacts(
        item="a new laptop",
        price_display="USD 1,500.00",
        net_worth_after_display="-USD 2,981,500.00",
        emergency_fund_months_before=9.6,
        emergency_fund_months_after=8.9,
        discretionary_months_consumed=0.4,
    )


def _report_facts() -> ReportFacts:
    return ReportFacts(
        report_type="weekly",
        period_start="2026-06-29",
        period_end="2026-07-05",
        net_cash_flow_display="USD 500.00",
        wins=["You stayed within budget with USD 500.00 remaining."],
        risks=["Groceries spending rose from USD 100.00 to USD 200.00."],
    )


def test_known_values_extracted_from_facts() -> None:
    known = known_values_from_facts(_facts())

    assert "1500.00" in known
    assert "2981500.00" in known
    assert "9.6" in known
    assert "8.9" in known
    assert "0.4" in known


def test_validate_recommendation_passes_when_grounded() -> None:
    known = known_values_from_facts(_facts())
    text = "Buying a new laptop for USD 1,500.00 leaves emergency fund coverage at 8.9 months."

    result = validate_recommendation(text, known)

    assert result.passed
    assert result.violations == []


def test_validate_recommendation_fails_on_invented_number() -> None:
    known = known_values_from_facts(_facts())
    text = "This purchase adds USD 22,500.00 in financing costs to your finances."

    result = validate_recommendation(text, known)

    assert not result.passed
    assert "22500.00" in result.violations


def test_find_unattributed_numbers_ignores_known_values() -> None:
    known = {"9.6", "8.9"}

    violations = find_unattributed_numbers("Coverage moves from 9.6 to 8.9 months.", known)

    assert violations == []


def test_find_unattributed_numbers_normalizes_thousands_separators() -> None:
    known = {"1500.00"}

    violations = find_unattributed_numbers("The price was USD 1,500.00.", known)

    assert violations == []


def test_known_values_from_report_facts_extracts_all_report_numbers() -> None:
    known = known_values_from_report_facts(_report_facts())

    assert "500.00" in known
    assert "100.00" in known
    assert "200.00" in known


def test_validate_recommendation_passes_for_grounded_report_text() -> None:
    known = known_values_from_report_facts(_report_facts())
    text = "Your weekly report shows USD 500.00 remaining after Groceries rose to USD 200.00."

    result = validate_recommendation(text, known)

    assert result.passed


def test_validate_recommendation_fails_for_invented_report_number() -> None:
    known = known_values_from_report_facts(_report_facts())
    text = "Your spending fell by USD 4,200.00 this week."

    result = validate_recommendation(text, known)

    assert not result.passed
    assert "4200.00" in result.violations


# --- M56 tolerance: honest derivations are not fabrications ---


def test_immaterial_numbers_are_never_violations() -> None:
    text = "Experts suggest 3-6 months of expenses; this is about 0.1% of your net worth."

    violations = find_unattributed_numbers(text, set())

    assert violations == []


def test_year_like_integers_are_exempt() -> None:
    violations = find_unattributed_numbers("By 2030 your fund doubles.", set())

    assert violations == []


def test_rounded_restatement_within_one_percent_passes() -> None:
    known = {"979278.48"}
    text = "You would still have about USD 974,000 left after the purchase."

    violations = find_unattributed_numbers(text, known)

    assert violations == []


def test_material_invented_figure_still_fails() -> None:
    known = {"979278.48"}
    text = "Your outstanding car loan of USD 45,000.00 changes the picture."

    violations = find_unattributed_numbers(text, known)

    assert violations == ["45000.00"]


# --- M60: derived arithmetic and the 100 boundary ---


def test_one_hundred_is_immaterial() -> None:
    violations = find_unattributed_numbers("That covers 100% of the target.", set())

    assert violations == []


def test_difference_of_grounded_figures_passes() -> None:
    known = {"8215.64", "7000"}
    text = "After the USD 7,000.00 laptop you would keep USD 1,215.64 in liquid funds."

    violations = find_unattributed_numbers(text, known)

    assert violations == []


def test_sum_of_grounded_figures_passes() -> None:
    known = {"1200", "350.50"}
    text = "Together that is USD 1,550.50 a month."

    violations = find_unattributed_numbers(text, known)

    assert violations == []


def test_figure_matching_no_pair_still_fails() -> None:
    known = {"8215.64", "7000"}
    text = "Plus your USD 4,900.00 boat payment."

    violations = find_unattributed_numbers(text, known)

    assert violations == ["4900.00"]


# --- M122: digits glued to a letter name a thing, not an amount ---


def test_account_type_names_are_not_money_claims() -> None:
    """"401k", "529", "1099-DIV" are names. The advisor must be able to say them
    without the guardrail reading 401 or 529 as an invented figure — account
    names no longer ground numbers, so nothing else would rescue them."""
    text = "Your 401k and the 529 plan stay untouched; a 1099-DIV arrives in February."

    violations = find_unattributed_numbers(text, set())

    assert violations == []


def test_a_figure_next_to_a_name_is_still_checked() -> None:
    """The tolerance is for digits INSIDE a name, not for money beside one."""
    text = "Your 401k holds USD 12,000,000.00 today."

    violations = find_unattributed_numbers(text, {"120000.00"})

    assert violations == ["12000000.00"]


def test_a_plan_identifier_written_as_an_amount_is_still_checked() -> None:
    """Only the bare identifier is a name; with decimals it is money again."""
    violations = find_unattributed_numbers("It cost USD 529.00 to open.", set())

    assert violations == ["529.00"]


# --- #152 review: a money claim carries its currency ---

import pytest  # noqa: E402
from family_cfo_ai_orchestrator import find_currency_mismatches  # noqa: E402

_EUR_ONLY = {"9000.00": {"EUR"}, "9000.0": {"EUR"}, "9000": {"EUR"}}


def test_a_grounded_figure_in_the_wrong_currency_is_a_violation() -> None:
    """An excluded EUR 9,000.00 pension grounds "9000.00"; restating it as USD is a
    real figure in the wrong unit — the same harm as an invented one."""
    result = validate_recommendation(
        "Your pension holds USD 9,000.00.", {"9000.00"}, known_money=_EUR_ONLY
    )

    assert result.passed is False
    assert result.violations == ["USD 9,000.00 is grounded only in EUR"]


@pytest.mark.parametrize(
    "text",
    [
        "Your pension holds EUR 9,000.00.",
        "Your pension holds 9,000.00 EUR, not counted in the USD total.",
        "Your pension holds €9,000.",
    ],
)
def test_the_same_figure_in_its_own_currency_passes(text: str) -> None:
    assert validate_recommendation(text, {"9000.00"}, known_money=_EUR_ONLY).passed is True


def test_a_dollar_sign_is_a_dollar_claim() -> None:
    assert find_currency_mismatches("That is $9,000 today.", _EUR_ONLY) == [
        "$9,000 is grounded only in EUR"
    ]
    assert find_currency_mismatches("That is $9,000 today.", {"9000": {"USD"}}) == []


def test_a_figure_grounded_in_both_currencies_may_be_said_in_either() -> None:
    both = {"9000.00": {"EUR", "USD"}}

    assert find_currency_mismatches("USD 9,000.00 here, EUR 9,000.00 there.", both) == []


def test_a_bare_number_is_left_to_the_number_check() -> None:
    """No currency named, no currency to contradict — the number check still owns it."""
    assert find_currency_mismatches("about 9,000 sits in the pension", _EUR_ONLY) == []
    assert validate_recommendation(
        "about 9,000 sits in the pension", {"9000"}, known_money=_EUR_ONLY
    ).passed


def test_a_rounded_claim_in_the_wrong_currency_still_fails() -> None:
    """The ±1% rounding tolerance must not become a currency loophole."""
    assert find_currency_mismatches("USD 9,050", _EUR_ONLY) == [
        "USD 9,050 is grounded only in EUR"
    ]


def test_an_ungrounded_figure_is_not_this_checks_business() -> None:
    """A claim matching no grounded money is the number check's violation, not a
    currency one — reported once, as an invented figure."""
    result = validate_recommendation("USD 123,456.00", {"9000.00"}, known_money=_EUR_ONLY)

    assert result.violations == ["123456.00"]


def test_immaterial_amounts_are_never_currency_violations() -> None:
    assert find_currency_mismatches("USD 50", {"50": {"EUR"}}) == []


def test_without_known_money_the_number_check_is_unchanged() -> None:
    assert validate_recommendation("USD 9,000.00", {"9000.00"}).passed is True
    assert validate_recommendation("USD 9,000.00", {"9000.00"}, known_money={}).passed is True
