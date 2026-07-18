"""M111 (ADR 0024): the Bills payment timeline — bills, card payments, and
loan/lease payments as one time-ordered list with paid-matching.

Dates are relative to today because the demo fixture seeds its own bills and
balances relative to today (checking $5,000 + savings $15,000, an "Internet"
bill, a "Mortgage payment" bill); assertions avoid exact totals where seeded
rows contribute.
"""

from datetime import date, timedelta

from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, fixtures, repository
from family_cfo_api.finance_service import add_months

HH = fixtures.DEMO_HOUSEHOLD_ID
TODAY = date.today()
SEEDED_LIQUID = 500_000 + 1_500_000  # demo checking + savings


def _checking(engine: Engine, balance_minor: int = 500_000) -> str:
    account = repository.create_account(
        engine, HH, name="Timeline Checking", account_type="checking", currency="USD"
    )
    repository.record_account_balance(engine, account.id, balance_minor)
    return account.id


def _charge(
    engine: Engine, account_id: str, occurred: date, amount_minor: int, merchant: str
) -> str:
    return repository.create_transaction(
        engine, HH, account_id=account_id, occurred_at=occurred,
        amount_minor=amount_minor, currency="USD", merchant=merchant,
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )


def _items(engine: Engine) -> dict[str, finance_service.PaymentTimelineItem]:
    timeline = finance_service.payment_timeline(engine, HH, "USD", today=TODAY)
    return {item.name: item for item in timeline.items}


def test_bill_paid_this_cycle_matches_the_actual_charge(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    paid_on = TODAY - timedelta(days=5)
    next_due = add_months(paid_on, 1)
    repository.create_bill(
        demo_engine, HH, name="Goldfish Swim School", amount_minor=14_800,
        currency="USD", frequency="monthly", next_due_date=next_due,
    )
    _charge(demo_engine, checking, paid_on, -14_800, "GOLDFISH SWIM SCHOOL")

    item = _items(demo_engine)["Goldfish Swim School"]
    assert item.status == "paid"
    assert item.paid is not None
    assert item.paid.occurred_at == paid_on
    assert item.due_date == next_due  # next occurrence still shown


def test_variable_utility_matches_within_tolerance_and_reports_actual(
    demo_engine: Engine,
) -> None:
    # A $120-typical utility on a fixed due day actually charged $101.37 — the
    # fixed due day + generous amount tolerance is how variable bills are captured.
    checking = _checking(demo_engine)
    paid_on = TODAY - timedelta(days=4)
    repository.create_bill(
        demo_engine, HH, name="PSE&G", amount_minor=12_000, currency="USD",
        frequency="monthly", next_due_date=add_months(paid_on, 1),
    )
    _charge(demo_engine, checking, paid_on, -10_137, "PSE G 2026-07")

    item = _items(demo_engine)["PSE&G"]
    assert item.status == "paid"
    assert item.paid is not None and item.paid.amount_minor == 10_137  # the ACTUAL


def test_unpaid_recent_due_date_is_overdue_not_hidden(demo_engine: Engine) -> None:
    _checking(demo_engine)
    due = TODAY - timedelta(days=5)
    repository.create_bill(
        demo_engine, HH, name="Water Bill", amount_minor=6_000, currency="USD",
        frequency="monthly", next_due_date=due,  # five days late, no matching charge
    )

    item = _items(demo_engine)["Water Bill"]
    assert item.status == "overdue"
    assert item.due_date == due


def test_last_cycles_payment_does_not_mark_tomorrows_charge_paid(
    demo_engine: Engine,
) -> None:
    # Paid a month ago; due again tomorrow. The old payment must NOT produce a
    # checkmark — a false "Paid" is worse than no status.
    checking = _checking(demo_engine)
    next_due = TODAY + timedelta(days=1)
    repository.create_bill(
        demo_engine, HH, name="Fiber Internet", amount_minor=8_000, currency="USD",
        frequency="monthly", next_due_date=next_due,
    )
    _charge(demo_engine, checking, add_months(next_due, -1), -8_000, "FIBER INTERNET")

    item = _items(demo_engine)["Fiber Internet"]
    assert item.status == "due_soon"
    assert item.paid is None


def test_credit_card_infers_due_day_from_payment_history(demo_engine: Engine) -> None:
    _checking(demo_engine)
    card = repository.create_account(
        demo_engine, HH, name="Timeline Visa", account_type="credit_card", currency="USD"
    )
    repository.record_account_balance(demo_engine, card.id, -250_000)
    # Monthly "Payment" inflows on the card's own account; the next one is due
    # about a month after the last, which lands inside the 14-day window.
    last_paid = TODAY - timedelta(days=26)
    _charge(demo_engine, card.id, add_months(last_paid, -1), 180_000, "Credit Card Payment")
    _charge(demo_engine, card.id, last_paid, 210_000, "Credit Card Payment")
    # A refund inflow must not be mistaken for a payment.
    _charge(demo_engine, card.id, TODAY - timedelta(days=2), 4_551, "Macy's")

    item = _items(demo_engine)["Timeline Visa"]
    assert item.kind == "credit_card"
    assert item.amount_minor == 250_000  # pay-in-full: the current balance
    assert item.due_date == add_months(last_paid, 1)  # inferred from history
    assert item.status == "due_soon"


def test_card_paid_recently_with_far_next_due_shows_paid(demo_engine: Engine) -> None:
    _checking(demo_engine)
    card = repository.create_account(
        demo_engine, HH, name="Platinum Timeline", account_type="credit_card", currency="USD"
    )
    repository.record_account_balance(demo_engine, card.id, -1_000_000)
    paid_on = TODAY - timedelta(days=3)
    _charge(demo_engine, card.id, paid_on, 909_996, "Payment")

    item = _items(demo_engine)["Platinum Timeline"]
    assert item.status == "paid"
    assert item.paid is not None and item.paid.occurred_at == paid_on
    assert item.due_date == add_months(paid_on, 1)


def test_card_with_no_payment_history_is_undated_never_overdue(
    demo_engine: Engine,
) -> None:
    _checking(demo_engine)
    card = repository.create_account(
        demo_engine, HH, name="Fresh Card", account_type="credit_card", currency="USD"
    )
    repository.record_account_balance(demo_engine, card.id, -50_000)

    item = _items(demo_engine)["Fresh Card"]
    assert item.status == "no_date"
    assert item.due_date is None


def test_headline_sums_whats_due_against_liquid(demo_engine: Engine) -> None:
    _checking(demo_engine, balance_minor=100_000)
    # Far larger than seeded liquid ($20,000) + ours, so covered must be False.
    repository.create_bill(
        demo_engine, HH, name="Big Rent", amount_minor=5_000_000, currency="USD",
        frequency="monthly", next_due_date=TODAY + timedelta(days=3),
    )

    timeline = finance_service.payment_timeline(demo_engine, HH, "USD", today=TODAY)
    assert timeline.due_total_minor >= 5_000_000  # seeded due-soon bills may add
    assert timeline.liquid_minor == SEEDED_LIQUID + 100_000
    assert timeline.covered is False
    # And the rent itself is in the due-soon bucket.
    assert _items(demo_engine)["Big Rent"].status == "due_soon"


def test_payroll_deducted_loans_stay_off_the_timeline(demo_engine: Engine) -> None:
    _checking(demo_engine)
    repository.create_account(
        demo_engine, HH, name="401k Loan", account_type="401k_loan", currency="USD",
        minimum_payment_minor=20_000,
    )

    assert "401k Loan" not in _items(demo_engine)


def test_debt_also_set_up_as_a_bill_shows_once_with_a_due_date(
    demo_engine: Engine,
) -> None:
    """A loan added as an account AND set up as a bill is one obligation, not two
    (ADR 0032). The bill — which carries the due date and matches the real charge —
    is shown; the derived account obligation is suppressed, so it never appears a
    second time under 'No due date yet'."""
    checking = _checking(demo_engine)
    loan = repository.create_account(
        demo_engine, HH, name="U.S. Department of Education",
        account_type="student_loan", currency="USD", minimum_payment_minor=7_801,
    )
    repository.record_account_balance(demo_engine, loan.id, -1_000_000)
    paid_on = TODAY - timedelta(days=10)
    next_due = add_months(paid_on, 1)
    repository.create_bill(
        demo_engine, HH, name="Department of Education", amount_minor=7_801,
        currency="USD", frequency="monthly", next_due_date=next_due,
    )
    _charge(demo_engine, checking, paid_on, -7_801, "DEPARTMENT OF EDUCATION")

    items = _items(demo_engine)
    # The bill is shown, with a real due date (not "no_date").
    assert "Department of Education" in items
    assert items["Department of Education"].due_date is not None
    assert items["Department of Education"].status != "no_date"
    # The derived loan obligation is NOT listed a second time.
    assert "U.S. Department of Education" not in items
    # ...and the obligation list itself excludes the covered account everywhere.
    obligations = finance_service.recurring_liability_obligations(demo_engine, HH, "USD")
    assert all(o.account_id != loan.id for o in obligations)


def test_loan_without_a_matching_bill_is_still_shown(demo_engine: Engine) -> None:
    """The dedup is name+amount specific: an unrelated loan with a payment on its
    own account still appears as a first-class obligation (ADR 0032 doesn't hide
    genuine debts)."""
    _checking(demo_engine)
    loan = repository.create_account(
        demo_engine, HH, name="U.S. Department of Education",
        account_type="student_loan", currency="USD", minimum_payment_minor=7_801,
    )
    repository.record_account_balance(demo_engine, loan.id, -1_000_000)
    # A bill for a DIFFERENT creditor must not suppress this loan.
    repository.create_bill(
        demo_engine, HH, name="Goldfish Swim School", amount_minor=14_800,
        currency="USD", frequency="monthly", next_due_date=TODAY + timedelta(days=6),
    )

    obligations = finance_service.recurring_liability_obligations(demo_engine, HH, "USD")
    assert any(o.account_id == loan.id for o in obligations)
