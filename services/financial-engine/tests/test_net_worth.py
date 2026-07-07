from family_cfo_financial_engine.money import Money
from family_cfo_financial_engine.net_worth import AccountBalance, calculate_net_worth


def test_net_worth_sums_assets_and_liabilities() -> None:
    balances = [
        AccountBalance("checking-1", "checking", Money(500_000, "USD")),
        AccountBalance("savings-1", "savings", Money(1_000_000, "USD")),
        AccountBalance("mortgage-1", "mortgage", Money(-300_000_000, "USD")),
        AccountBalance("cc-1", "credit_card", Money(-150_000, "USD")),
    ]

    result = calculate_net_worth(balances, "USD")

    assert result.calculation_type == "net_worth"
    assert result.outputs["asset_total"] == Money(1_500_000, "USD")
    assert result.outputs["liability_total"] == Money(-300_150_000, "USD")
    assert result.outputs["net_worth"] == Money(1_500_000 - 300_150_000, "USD")
    assert result.warnings == []


def test_net_worth_with_no_accounts_is_zero() -> None:
    result = calculate_net_worth([], "USD")

    assert result.outputs["net_worth"] == Money.zero("USD")


def test_net_worth_flags_unrecognized_account_type_but_still_counts_it() -> None:
    balances = [AccountBalance("mystery-1", "space_bucks", Money(1_000, "USD"))]

    result = calculate_net_worth(balances, "USD")

    assert result.outputs["net_worth"] == Money(1_000, "USD")
    assert result.outputs["asset_total"] == Money.zero("USD")
    assert result.outputs["liability_total"] == Money.zero("USD")
    assert len(result.warnings) == 1
    assert "space_bucks" in result.warnings[0]


def test_net_worth_result_includes_audit_fields() -> None:
    result = calculate_net_worth([], "USD")

    assert result.version
    assert result.assumptions
    assert result.inputs == {"account_count": 0, "currency": "USD"}
    assert result.computed_at is not None
