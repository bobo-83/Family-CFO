"""ADR 0057: parse the vision model's account-statement extraction defensively."""

from family_cfo_api.api.accounts import merge_account_scans, parse_account_scan


def test_hsa_statement_prefills_name_type_and_balance() -> None:
    result = parse_account_scan(
        '{"account_name": "HealthEquity HSA", "account_type": "hsa", '
        '"cash_balance": 8412.57, "statement_date": "2026-06-30"}'
    )
    assert result.name == "HealthEquity HSA"
    assert result.account_type == "hsa"
    assert result.balance_minor == 841_257
    assert str(result.statement_date) == "2026-06-30"
    assert "CONFIRM" in result.note


def test_cash_plus_investment_sum_to_the_balance() -> None:
    """The $1,000-cash-next-to-$102k-invested lesson: an HSA's true value is
    cash + investments, and the note spells the breakdown out."""
    result = parse_account_scan(
        '{"account_name": "HealthEquity", "account_type": "hsa", '
        '"cash_balance": 1000.04, "investment_value": 102269.18}'
    )
    assert result.cash_balance_minor == 100_004
    assert result.investment_value_minor == 10_226_918
    assert result.balance_minor == 10_326_922
    assert "cash" in result.note and "invested" in result.note


def test_multi_page_merge_combines_cash_and_investment_pages() -> None:
    """An HSA statement shows cash on one page and investments on another —
    the merge must find both instead of stopping at the first readable page."""
    page1 = parse_account_scan(
        '{"account_name": "HealthEquity", "account_type": "hsa", "cash_balance": 1000.04}'
    )
    page2 = parse_account_scan('{"investment_value": 102269.18}')
    merged = merge_account_scans([page1, page2])
    assert merged.name == "HealthEquity"
    assert merged.account_type == "hsa"
    assert merged.cash_balance_minor == 100_004
    assert merged.investment_value_minor == 10_226_918
    assert merged.balance_minor == 10_326_922


def test_legacy_single_balance_key_still_parses_as_cash() -> None:
    result = parse_account_scan('{"balance": 8412.57}')
    assert result.balance_minor == 841_257
    assert result.cash_balance_minor == 841_257


def test_type_synonyms_map_to_app_types() -> None:
    assert parse_account_scan('{"account_type": "Health Savings Account"}').account_type == "hsa"
    assert parse_account_scan('{"account_type": "Investment"}').account_type == "brokerage"
    assert parse_account_scan('{"account_type": "401k"}').account_type == "retirement"
    assert parse_account_scan('{"account_type": "money market"}').account_type == "savings"
    assert parse_account_scan('{"account_type": "college savings"}').account_type == "529"


def test_unknown_type_and_missing_fields_stay_none() -> None:
    result = parse_account_scan('{"account_type": "crypto wallet", "balance": null}')
    assert result.account_type is None
    assert result.balance_minor is None
    assert result.name is None


def test_balance_reported_as_string_is_parsed() -> None:
    result = parse_account_scan('{"balance": "$12,345.67"}')
    assert result.balance_minor == 1_234_567


def test_json_in_code_fence_is_parsed() -> None:
    result = parse_account_scan('```json\n{"account_type": "hsa", "balance": 100}\n```')
    assert result.account_type == "hsa"
    assert result.balance_minor == 10_000


def test_unreadable_output_falls_back_to_manual_entry() -> None:
    result = parse_account_scan("I could not read this image, sorry!")
    assert result.name is None
    assert result.balance_minor is None
    assert "manually" in result.note


def test_us_date_format_is_parsed() -> None:
    result = parse_account_scan('{"statement_date": "06/30/2026"}')
    assert str(result.statement_date) == "2026-06-30"


def test_negative_or_zero_balance_is_dropped() -> None:
    # An asset account prefill never proposes a negative balance.
    assert parse_account_scan('{"balance": -50}').balance_minor is None
    assert parse_account_scan('{"balance": 0}').balance_minor is None
