"""M112 (ADR 0026): the 30-day cash outlook — paychecks in, payments out, and
the lowest point the balance reaches. Dates are relative to today (the demo
fixture seeds bills/balances relative to today; see test_payment_timeline)."""

from datetime import date, timedelta

from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, fixtures, repository
from family_cfo_api.finance_service import add_months

HH = fixtures.DEMO_HOUSEHOLD_ID
TODAY = date.today()
SEEDED_LIQUID = 500_000 + 1_500_000  # demo checking + savings
# The demo fixture seeds an "Internet" bill (due ~today+10) and a "Mortgage
# payment" bill (due ~today+15); both land inside the 30-day horizon.
SEEDED_BILLS_MINOR = 8_000 + 200_000


def _outlook(engine: Engine) -> finance_service.CashOutlook:
    return finance_service.cash_outlook(engine, HH, "USD", today=TODAY)


def _paychecks(engine: Engine, account_id: str, *, amount: int = 250_000) -> None:
    """Three biweekly PAYROLL deposits, most recent 10 days ago — enough history
    for detection, next payday inferred 4 days from now."""
    last = TODAY - timedelta(days=10)
    for periods_back in range(3):
        repository.create_transaction(
            engine, HH, account_id=account_id,
            occurred_at=last - timedelta(days=14 * periods_back),
            amount_minor=amount, currency="USD", merchant="ACME PAYROLL",
            description=None, import_source=None, import_id=None,
            review_state="reviewed",
        )


def _checking(engine: Engine) -> str:
    account = repository.create_account(
        engine, HH, name="Outlook Checking", account_type="checking", currency="USD"
    )
    return account.id


def test_paydays_are_projected_from_recurring_deposits(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    _paychecks(demo_engine, checking)

    outlook = _outlook(demo_engine)
    paydays = [e for e in outlook.events if e.kind == "income"]
    # Last paycheck 10 days ago, biweekly → paydays at +4 and +18 days.
    assert [e.occurred_on for e in paydays] == [
        TODAY + timedelta(days=4),
        TODAY + timedelta(days=18),
    ]
    assert all(e.amount_minor == 250_000 for e in paydays)
    assert outlook.expected_income_minor == 500_000


def test_projection_tracks_running_balance_and_lowest_point(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    _paychecks(demo_engine, checking)
    # A big rent due in 2 days — BEFORE the +4d payday — creates the dip.
    repository.create_bill(
        demo_engine, HH, name="Outlook Rent", amount_minor=1_900_000, currency="USD",
        frequency="monthly", next_due_date=TODAY + timedelta(days=2),
    )

    outlook = _outlook(demo_engine)
    assert outlook.starting_cash_minor == SEEDED_LIQUID
    # Lowest point: after rent (day 2), before the payday (day 4).
    assert outlook.lowest_minor == SEEDED_LIQUID - 1_900_000
    assert outlook.lowest_date == TODAY + timedelta(days=2)
    # Ending: cash − rent − seeded bills + two paydays.
    assert outlook.ending_cash_minor == (
        SEEDED_LIQUID - 1_900_000 - SEEDED_BILLS_MINOR + 500_000
    )


def test_weekly_bills_recur_within_the_window(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    repository.create_bill(
        demo_engine, HH, name="Cleaner", amount_minor=10_000, currency="USD",
        frequency="weekly", next_due_date=TODAY + timedelta(days=3),
    )
    # Last week's charge, so the bill reads paid (not overdue) going in.
    repository.create_transaction(
        demo_engine, HH, account_id=checking, occurred_at=TODAY - timedelta(days=4),
        amount_minor=-10_000, currency="USD", merchant="CLEANER",
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )

    occurrences = [
        e for e in _outlook(demo_engine).events if e.name == "Cleaner"
    ]
    # Days 3, 10, 17, 24 — and day 31 is outside the 30-day horizon.
    assert [e.occurred_on for e in occurrences] == [
        TODAY + timedelta(days=3 + 7 * i) for i in range(4)
    ]


def test_card_payment_is_placed_once_never_projected_twice(demo_engine: Engine) -> None:
    _checking(demo_engine)
    card = repository.create_account(
        demo_engine, HH, name="Outlook Card", account_type="credit_card", currency="USD"
    )
    repository.record_account_balance(demo_engine, card.id, -300_000)
    repository.create_transaction(
        demo_engine, HH, account_id=card.id, occurred_at=TODAY - timedelta(days=26),
        amount_minor=280_000, currency="USD", merchant="Payment",
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )

    card_events = [
        e for e in _outlook(demo_engine).events if e.name == "Outlook Card"
    ]
    # Next payment inferred at +4 days for the current balance; the statement
    # after that is unknowable, so exactly ONE event.
    assert len(card_events) == 1
    assert card_events[0].occurred_on == add_months(TODAY - timedelta(days=26), 1)
    assert card_events[0].amount_minor == -300_000


def test_transfers_are_not_counted_as_paydays(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    savings = repository.create_account(
        demo_engine, HH, name="Outlook Savings", account_type="savings", currency="USD"
    )
    # Monthly $1,000 moves savings → checking: an inflow with a matching
    # same-amount outflow, i.e. an internal transfer, not income.
    for months_back in range(1, 4):
        when = add_months(TODAY - timedelta(days=5), -months_back)
        repository.create_transaction(
            demo_engine, HH, account_id=checking, occurred_at=when,
            amount_minor=100_000, currency="USD", merchant="Online Transfer",
            description=None, import_source=None, import_id=None, review_state="reviewed",
        )
        repository.create_transaction(
            demo_engine, HH, account_id=savings.id, occurred_at=when,
            amount_minor=-100_000, currency="USD", merchant="Online Transfer",
            description=None, import_source=None, import_id=None, review_state="reviewed",
        )

    assert [e for e in _outlook(demo_engine).events if e.kind == "income"] == []
