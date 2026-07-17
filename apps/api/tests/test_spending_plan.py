"""M113 (ADR 0027): left to spend this month. Dates are relative to today; the
demo fixture seeds two bills ("Internet" $80 due ~today+10, "Mortgage payment"
$2,000 due ~today+15) whose contribution depends on whether those dates land in
the current month, so expected values compute it the same way."""

from datetime import date, timedelta

from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, fixtures, repository
from family_cfo_api.finance_service import add_months

HH = fixtures.DEMO_HOUSEHOLD_ID
TODAY = date.today()
MONTH_START = TODAY.replace(day=1)
MONTH_END = add_months(MONTH_START, 1) - timedelta(days=1)


def _seeded_bills_in_month() -> int:
    """What the fixture's two bills contribute to bills_remaining."""
    total = 0
    if TODAY + timedelta(days=10) <= MONTH_END:
        total += 8_000  # Internet
    if TODAY + timedelta(days=15) <= MONTH_END:
        total += 200_000  # Mortgage payment (a bill in the fixture, no account link)
    return total


def _plan(engine: Engine) -> finance_service.SpendingPlan:
    return finance_service.spending_plan(engine, HH, "USD", today=TODAY)


def _checking(engine: Engine) -> str:
    account = repository.create_account(
        engine, HH, name="Plan Checking", account_type="checking", currency="USD"
    )
    return account.id


def _txn(
    engine: Engine, account_id: str, occurred: date, amount_minor: int, merchant: str,
    category_id: str | None = None,
) -> str:
    return repository.create_transaction(
        engine, HH, account_id=account_id, occurred_at=occurred,
        amount_minor=amount_minor, currency="USD", merchant=merchant,
        description=None, import_source=None, import_id=None,
        review_state="reviewed", category_id=category_id,
    )


def test_income_splits_received_and_projected(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    # Biweekly paychecks; most recent 10 days ago. Received counts only the
    # ones that landed inside THIS month.
    last = TODAY - timedelta(days=10)
    paydays = [last - timedelta(days=14 * n) for n in range(3)]
    for payday in paydays:
        _txn(demo_engine, checking, payday, 250_000, "ACME PAYROLL")

    plan = _plan(demo_engine)
    expected_received = 250_000 * sum(1 for p in paydays if p >= MONTH_START)
    assert plan.income_received_minor == expected_received
    # Projected: paydays stepped biweekly from the last sighting through month end.
    expected_projected = 0
    payday = last + timedelta(days=14)
    while payday <= MONTH_END:
        expected_projected += 250_000
        payday += timedelta(days=14)
    assert plan.income_projected_minor == expected_projected
    assert plan.expected_income_minor == expected_received + expected_projected


def test_left_is_income_minus_spent_minus_committed(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    _txn(demo_engine, checking, TODAY - timedelta(days=1), -12_345, "Costco")
    # An unpaid bill due at month end is committed.
    repository.create_bill(
        demo_engine, HH, name="Plan Utility", amount_minor=15_000, currency="USD",
        frequency="monthly", next_due_date=MONTH_END,
    )
    # A lease with a recorded payment claims the month once (transfer legs
    # never show in spending).
    repository.create_account(
        demo_engine, HH, name="Plan Lease", account_type="auto_loan", currency="USD",
        minimum_payment_minor=42_828,
    )

    plan = _plan(demo_engine)
    # Seeded fixture spending: Whole Foods -120.00 today, Trader Joe's -55.00
    # 3 days ago (this month only when within it).
    seeded_spent = 12_000 + (5_500 if TODAY - timedelta(days=3) >= MONTH_START else 0)
    assert plan.spent_minor == seeded_spent + 12_345
    assert plan.bills_remaining_minor == 15_000 + _seeded_bills_in_month()
    assert plan.account_obligations_minor == 42_828
    assert plan.left_minor == (
        plan.expected_income_minor
        - plan.spent_minor
        - plan.bills_remaining_minor
        - plan.account_obligations_minor
    )


def test_paid_bill_counts_as_spending_not_as_committed(demo_engine: Engine) -> None:
    checking = _checking(demo_engine)
    paid_on = TODAY - timedelta(days=2)
    repository.create_bill(
        demo_engine, HH, name="Plan Water", amount_minor=6_000, currency="USD",
        frequency="monthly", next_due_date=add_months(paid_on, 1),
    )
    _txn(demo_engine, checking, paid_on, -6_000, "PLAN WATER")

    plan = _plan(demo_engine)
    # Committed: only the seeded bills — Plan Water is paid (its next
    # occurrence is next month), and its charge sits in spent instead.
    assert plan.bills_remaining_minor == _seeded_bills_in_month()
    baseline_spent = 12_000 + (5_500 if TODAY - timedelta(days=3) >= MONTH_START else 0)
    assert plan.spent_minor == baseline_spent + (
        6_000 if paid_on >= MONTH_START else 0
    )


def test_cards_never_double_count(demo_engine: Engine) -> None:
    _checking(demo_engine)
    card = repository.create_account(
        demo_engine, HH, name="Plan Card", account_type="credit_card", currency="USD"
    )
    repository.record_account_balance(demo_engine, card.id, -50_000)
    # A charge ON the card is spending; the card's payment must not appear in
    # any committed term (its amount is the charges, already counted).
    _txn(demo_engine, card.id, TODAY - timedelta(days=1), -50_000, "Restaurant")

    plan = _plan(demo_engine)
    baseline_spent = 12_000 + (5_500 if TODAY - timedelta(days=3) >= MONTH_START else 0)
    assert plan.spent_minor == baseline_spent + 50_000
    assert plan.account_obligations_minor == 0  # cards are not obligations here
    assert plan.bills_remaining_minor == _seeded_bills_in_month()


def test_payroll_deducted_loans_never_claim_deposit_income(demo_engine: Engine) -> None:
    _checking(demo_engine)
    repository.create_account(
        demo_engine, HH, name="Plan 401k Loan", account_type="401k_loan", currency="USD",
        minimum_payment_minor=20_000,
    )

    assert _plan(demo_engine).account_obligations_minor == 0


def test_planned_savings_reserves_goal_contributions(demo_engine: Engine) -> None:
    _checking(demo_engine)
    repository.create_goal(
        demo_engine, HH, name="Vacation", goal_type="vacation",
        target_minor=500_000, currency="USD", target_date=None, priority=3,
        monthly_contribution_minor=50_000,
    )
    repository.create_goal(
        demo_engine, HH, name="No plan", goal_type="other",
        target_minor=100_000, currency="USD", target_date=None, priority=3,
    )

    plan = _plan(demo_engine)
    assert plan.planned_savings_minor == 50_000  # only declared contributions count
    assert plan.left_minor == (
        plan.expected_income_minor
        - plan.spent_minor
        - plan.bills_remaining_minor
        - plan.account_obligations_minor
        - 50_000
    )
