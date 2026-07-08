from family_cfo_api.explanation import (
    DeterministicExplanationAdapter,
    PurchaseExplanationContext,
    ReportExplanationContext,
    format_money,
)
from family_cfo_financial_engine import Money


def test_format_money_renders_negative_amounts_with_leading_sign() -> None:
    assert format_money(Money(-150_000, "USD")) == "-USD 1,500.00"
    assert format_money(Money(150_000, "USD")) == "USD 1,500.00"


def test_explain_purchase_includes_net_worth_and_emergency_fund_sentences() -> None:
    adapter = DeterministicExplanationAdapter()
    context = PurchaseExplanationContext(
        item="a new laptop",
        price=Money(150_000, "USD"),
        net_worth_after=Money(1_000_000, "USD"),
        emergency_fund_months_before=9.6,
        emergency_fund_months_after=8.9,
        discretionary_months_consumed=0.4,
        warnings=[],
    )

    result = adapter.explain_purchase(context)

    assert result.source == "deterministic_stub"
    assert result.model_version is None
    assert "a new laptop" in result.text
    assert "USD 1,500.00" in result.text
    assert "9.6" in result.text and "8.9" in result.text
    assert "0.4" in result.text
    assert "Note:" not in result.text


def test_explain_purchase_appends_warnings() -> None:
    adapter = DeterministicExplanationAdapter()
    context = PurchaseExplanationContext(
        item="a car",
        price=Money(2_000_000, "USD"),
        net_worth_after=Money(500_000, "USD"),
        emergency_fund_months_before=None,
        emergency_fund_months_after=None,
        discretionary_months_consumed=None,
        warnings=["purchase price exceeds available liquid balance"],
    )

    result = adapter.explain_purchase(context)

    assert "Note: purchase price exceeds available liquid balance" in result.text


def test_explain_report_includes_wins_risks_and_recommended_actions() -> None:
    adapter = DeterministicExplanationAdapter()
    context = ReportExplanationContext(
        report_type="weekly",
        period_start="2026-06-29",
        period_end="2026-07-05",
        net_cash_flow=Money(50_000, "USD"),
        wins=["You stayed within budget with USD 500.00 remaining."],
        risks=["Groceries spending rose from USD 100.00 to USD 200.00."],
        unusual_spending=["New spending in Travel: USD 300.00."],
        recommended_actions=["Review your Groceries spending, which increased from last period."],
    )

    result = adapter.explain_report(context)

    assert result.source == "deterministic_stub"
    assert "weekly" in result.text
    assert "2026-06-29" in result.text and "2026-07-05" in result.text
    assert "USD 500.00" in result.text
    assert "Wins:" in result.text and "Risks:" in result.text
    assert "Unusual spending:" in result.text and "Recommended actions:" in result.text


def test_explain_report_omits_empty_sections() -> None:
    adapter = DeterministicExplanationAdapter()
    context = ReportExplanationContext(
        report_type="monthly",
        period_start="2026-06-01",
        period_end="2026-06-30",
        net_cash_flow=Money(0, "USD"),
        wins=[],
        risks=[],
        unusual_spending=[],
        recommended_actions=[],
    )

    result = adapter.explain_report(context)

    assert "Wins:" not in result.text
    assert "Risks:" not in result.text
    assert "Unusual spending:" not in result.text
    assert "Recommended actions:" not in result.text
