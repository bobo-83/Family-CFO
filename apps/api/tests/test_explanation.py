from family_cfo_api.explanation import (
    DeterministicExplanationAdapter,
    PurchaseExplanationContext,
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

    answer = adapter.explain_purchase(context)

    assert "a new laptop" in answer
    assert "USD 1,500.00" in answer
    assert "9.6" in answer and "8.9" in answer
    assert "0.4" in answer
    assert "Note:" not in answer


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

    answer = adapter.explain_purchase(context)

    assert "Note: purchase price exceeds available liquid balance" in answer
