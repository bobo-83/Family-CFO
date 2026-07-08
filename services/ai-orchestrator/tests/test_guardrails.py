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
    text = "This purchase has a 22.5% interest rate impact on your finances."

    result = validate_recommendation(text, known)

    assert not result.passed
    assert "22.5" in result.violations


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
    text = "Your savings rate improved by 42% this week."

    result = validate_recommendation(text, known)

    assert not result.passed
    assert "42" in result.violations
