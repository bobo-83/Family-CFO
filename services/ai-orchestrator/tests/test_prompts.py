from family_cfo_ai_orchestrator.prompts import (
    PurchaseFacts,
    ReportFacts,
    build_purchase_explanation_prompt,
    build_report_explanation_prompt,
)


def test_build_prompt_includes_all_facts() -> None:
    facts = PurchaseFacts(
        item="a new laptop",
        price_display="USD 1,500.00",
        net_worth_after_display="-USD 2,981,500.00",
        emergency_fund_months_before=9.6,
        emergency_fund_months_after=8.9,
        discretionary_months_consumed=0.4,
        warnings=["debt payoff impact requires interest rate and payment data not yet modeled"],
    )

    messages = build_purchase_explanation_prompt(facts)

    assert messages[0].role == "system"
    assert messages[1].role == "user"
    user_text = messages[1].content
    assert "a new laptop" in user_text
    assert "USD 1,500.00" in user_text
    assert "9.6" in user_text and "8.9" in user_text
    assert "0.4" in user_text
    assert "Known limitations" in user_text


def test_build_prompt_omits_optional_facts_when_unset() -> None:
    facts = PurchaseFacts(
        item="snacks",
        price_display="USD 5.00",
        net_worth_after_display="USD 100.00",
    )

    messages = build_purchase_explanation_prompt(facts)

    user_text = messages[1].content
    assert "Emergency fund" not in user_text
    assert "Discretionary" not in user_text
    assert "Known limitations" not in user_text


def test_build_report_prompt_includes_all_facts() -> None:
    facts = ReportFacts(
        report_type="weekly",
        period_start="2026-06-29",
        period_end="2026-07-05",
        net_cash_flow_display="USD 500.00",
        wins=["You stayed within budget with USD 500.00 remaining."],
        risks=["Groceries spending rose from USD 100.00 to USD 200.00."],
        unusual_spending=["New spending in Travel: USD 300.00."],
        recommended_actions=["Review your Groceries spending, which increased from last period."],
    )

    messages = build_report_explanation_prompt(facts)

    assert messages[0].role == "system"
    assert messages[1].role == "user"
    user_text = messages[1].content
    assert "weekly" in user_text
    assert "2026-06-29" in user_text and "2026-07-05" in user_text
    assert "USD 500.00" in user_text
    assert "Wins:" in user_text
    assert "Risks:" in user_text
    assert "Unusual spending:" in user_text
    assert "Recommended actions:" in user_text


def test_build_report_prompt_omits_optional_facts_when_unset() -> None:
    facts = ReportFacts(
        report_type="monthly",
        period_start="2026-06-01",
        period_end="2026-06-30",
        net_cash_flow_display="USD 0.00",
    )

    messages = build_report_explanation_prompt(facts)

    user_text = messages[1].content
    assert "Wins:" not in user_text
    assert "Risks:" not in user_text
    assert "Unusual spending:" not in user_text
    assert "Recommended actions:" not in user_text
