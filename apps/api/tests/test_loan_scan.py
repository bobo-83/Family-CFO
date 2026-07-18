"""M96: parse a loan/lease statement's vision-model output into candidate values."""

from family_cfo_api.api.accounts import parse_loan_scan


def test_loan_statement_uses_stated_payoff_balance() -> None:
    result = parse_loan_scan(
        '{"lender": "Chase Auto", "monthly_payment": 450.00, '
        '"payoff_balance": 18250.75, "payments_remaining": 41, "apr": 4.9, '
        '"is_lease": false}'
    )
    assert result.name == "Chase Auto"
    assert result.monthly_payment_minor == 45_000
    assert result.balance_minor == 18_250_75
    assert result.apr_percent == 4.9
    assert result.is_lease is False


def test_lease_without_payoff_estimates_balance_from_remaining_payments() -> None:
    result = parse_loan_scan(
        '{"lender": "Subaru Lease", "monthly_payment": 450.00, '
        '"payoff_balance": null, "payments_remaining": 18, "apr": null, '
        '"is_lease": true}'
    )
    # 18 payments left × $450 = $8,100 remaining obligation.
    assert result.balance_minor == 8_100_00
    assert result.payments_remaining == 18
    assert result.is_lease is True
    assert "estimated" in result.note.lower()


def test_lease_derives_payments_left_from_maturity_date() -> None:
    """The real Solterra case: no payoff and no stated payment count, but the
    statement date and maturity date give the months left."""
    result = parse_loan_scan(
        '{"lender": "Subaru Motors Finance", "monthly_payment": 428.28, '
        '"payoff_balance": null, "payments_remaining": null, '
        '"statement_date": "2026-06-29", "maturity_date": "2026-10-17", '
        '"apr": null, "is_lease": true}'
    )
    # Jun -> Oct 2026 = 4 payments left × $428.28 = $1,713.12.
    assert result.payments_remaining == 4
    assert result.balance_minor == 1_713_12
    assert "4 payments left" in result.note
    assert "Oct 2026" in result.note


def test_us_date_format_from_statement_is_parsed() -> None:
    result = parse_loan_scan(
        '{"monthly_payment": 428.28, "statement_date": "06/29/2026", '
        '"maturity_date": "10/17/2026", "is_lease": true}'
    )
    assert result.payments_remaining == 4


def test_unreadable_output_falls_back_to_manual_entry() -> None:
    result = parse_loan_scan("sorry, I can't read that")
    assert result.monthly_payment_minor is None
    assert result.balance_minor is None
    assert "manually" in result.note.lower()


def test_json_in_code_fence_is_parsed() -> None:
    result = parse_loan_scan('```json\n{"monthly_payment": 300, "is_lease": false}\n```')
    assert result.monthly_payment_minor == 30_000

def test_scan_reads_the_next_payment_due_date() -> None:
    """ADR 0033: the loan scan now extracts the next payment due date so the editor
    can pre-fill it (accepts ISO and US date formats)."""
    from datetime import date

    result = parse_loan_scan(
        '{"lender": "U.S. Department of Education", "monthly_payment": 78.01, '
        '"payoff_balance": 9500.00, "payment_due_date": "2026-08-08", '
        '"is_lease": false}'
    )
    assert result.next_payment_due_date == date(2026, 8, 8)

    us = parse_loan_scan('{"monthly_payment": 78.01, "payment_due_date": "08/08/2026"}')
    assert us.next_payment_due_date == date(2026, 8, 8)

    none = parse_loan_scan('{"monthly_payment": 78.01}')
    assert none.next_payment_due_date is None

def test_scan_accepts_numbers_reported_as_strings() -> None:
    """The vision model sometimes returns a rate or amount as text ("5.5%",
    "$3,816.36") — parse those too rather than dropping them."""
    result = parse_loan_scan(
        '{"lender": "Aidvantage", "monthly_payment": "78.01", '
        '"payoff_balance": "$3,816.36", "apr": "5.5%", "is_lease": false}'
    )
    assert result.monthly_payment_minor == 78_01
    assert result.balance_minor == 3_816_36
    assert result.apr_percent == 5.5


def test_scan_leaves_apr_none_when_absent_never_guesses() -> None:
    """No rate on the statement -> apr stays None (0 in the UI); we never invent
    an interest rate, since it would corrupt payoff estimates."""
    result = parse_loan_scan('{"monthly_payment": 78.01, "payoff_balance": 3816.36}')
    assert result.apr_percent is None

def test_text_statement_parse_reads_rate_payment_balance_due() -> None:
    """A text (e-statement) PDF parses deterministically — including the interest
    rate a rasterized-image vision pass misses (ADR 0033 follow-up)."""
    from datetime import date

    from family_cfo_api.api.accounts import parse_loan_statement_text

    text = (
        "Current Balance $3,816.36\n"
        "Regular Monthly Payment Amount $78.01\n"
        "Current Statement Due Date 8/7/2026\n"
        "Interest Rate is  2.125%\n"
    )
    r = parse_loan_statement_text(text)
    assert r is not None
    assert r.monthly_payment_minor == 78_01
    assert r.balance_minor == 3_816_36
    assert r.next_payment_due_date == date(2026, 8, 7)
    assert r.apr_percent == 2.125


def test_text_statement_parse_returns_none_when_not_a_statement() -> None:
    from family_cfo_api.api.accounts import parse_loan_statement_text

    assert parse_loan_statement_text("Just a receipt, thanks!") is None
