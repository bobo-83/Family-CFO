from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, fixtures, models, repository
from family_cfo_financial_engine import Money


def test_compute_net_worth_sums_demo_accounts(demo_engine: Engine) -> None:
    result = finance_service.compute_net_worth(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")

    # checking 500_000 + savings 1_500_000 - mortgage 300_000_000
    assert result.outputs["net_worth"] == Money(500_000 + 1_500_000 - 300_000_000, "USD")


def test_compute_net_worth_persists_audit_record(demo_engine: Engine) -> None:
    finance_service.compute_net_worth(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")

    with demo_engine.connect() as conn:
        rows = (
            conn.execute(
                select(models.financial_calculations).where(
                    models.financial_calculations.c.calculation_type == "net_worth"
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 1
    assert rows[0]["household_id"] == fixtures.DEMO_HOUSEHOLD_ID
    assert rows[0]["outputs_json"]["net_worth"] == {"amount_minor": -298_000_000, "currency": "USD"}


def test_compute_emergency_fund_uses_liquid_balances_and_monthly_bills(demo_engine: Engine) -> None:
    result = finance_service.compute_emergency_fund(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")

    # liquid = checking 500_000 + savings 1_500_000 = 2_000_000
    # The minimal demo has no debts with minimum payments and its only transactions
    # fall in the current partial month (excluded from the trailing window), so the
    # essential-expenses denominator collapses to the recurring bills:
    # mortgage 200_000 + internet 8_000 = 208_000.
    assert result.outputs["liquid_balance"] == Money(2_000_000, "USD")
    assert result.outputs["monthly_essential_expenses"] == Money(208_000, "USD")
    assert result.outputs["emergency_fund_months"] == 2_000_000 / 208_000


def test_monthly_debt_minimums_sums_liabilities_excluding_retirement_loans(
    demo_engine: Engine,
) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    # Minimal demo has no liability accounts with minimum payments.
    assert finance_service._monthly_debt_minimums(demo_engine, hh, "USD") == Money.zero("USD")

    repository.create_account(
        demo_engine, hh, "Student Loan", "student_loan", "USD", minimum_payment_minor=50_000
    )
    # A 401(k) loan is repaid by payroll deduction and must NOT claim liquid cash.
    repository.create_account(
        demo_engine, hh, "401k Loan", "401k_loan", "USD", minimum_payment_minor=30_000
    )

    assert finance_service._monthly_debt_minimums(demo_engine, hh, "USD") == Money(50_000, "USD")


def test_monthly_essential_expenses_adds_debt_minimums_and_spending_above_bills(
    demo_engine: Engine,
) -> None:
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 6, 15)  # window = the 3 complete months Mar–May 2026

    # A loan whose minimum payment is a recurring claim on cash.
    repository.create_account(
        demo_engine, hh, "Auto Loan", "auto_loan", "USD", minimum_payment_minor=40_000
    )
    # Everyday spending inside the trailing window: 300_000/mo average, above the
    # 208_000 of bills. Uncategorized outflows count as spending.
    checking = repository.create_account(demo_engine, hh, "Spending Checking", "checking", "USD")
    for occurred in (date(2026, 3, 10), date(2026, 4, 10), date(2026, 5, 10)):
        repository.create_transaction(
            demo_engine, household_id=hh, account_id=checking.id, occurred_at=occurred,
            amount_minor=-300_000, currency="USD", merchant="Market", description=None,
            import_source=None, import_id=None, review_state="reviewed",
        )

    result = finance_service.monthly_essential_expenses(demo_engine, hh, "USD", today=today)

    # bills 208_000 + debt minimum 40_000 + (avg spending 300_000 − bills 208_000)
    assert result == Money(208_000 + 40_000 + (300_000 - 208_000), "USD")
    # The whole point: strictly more than bills alone (the old, over-optimistic base).
    assert result.amount_minor > finance_service._monthly_bill_total(demo_engine, hh, "USD").amount_minor


def test_safe_to_spend_subtracts_bills_and_debt_not_just_the_emergency_fund(
    demo_engine: Engine,
) -> None:
    """The reported bug (2026-07-13): the advisor answered "how much can I spend"
    with liquid cash minus the emergency fund, ignoring every bill about to fall
    due and every minimum debt payment owed."""
    result, _ref = finance_service.compute_safe_to_spend(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD"
    )
    outputs = result.outputs

    # liquid = checking 500_000 + savings 1_500_000
    assert outputs["liquid_balance"] == Money(2_000_000, "USD")
    # The demo household has bills, so they MUST show up as committed money.
    assert outputs["bills_due"].amount_minor > 0

    committed = (
        outputs["emergency_fund_reserved"] + outputs["bills_due"] + outputs["minimum_debt_payments"]
    )
    assert outputs["committed_total"] == committed
    assert outputs["safe_to_spend"] == outputs["liquid_balance"] - committed
    # The old answer. Anything equal to it means bills/debt were ignored again.
    assert outputs["safe_to_spend"] != outputs["liquid_balance"] - outputs["emergency_fund_reserved"]


def test_safe_to_spend_commits_full_card_balances_when_paid_in_full(
    demo_engine: Engine,
) -> None:
    """M96: a household that pays cards in full has the whole balance committed
    (not just a minimum), and no 'unrecorded minimum' warning for those cards."""
    from family_cfo_api import repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    card = repository.create_account(demo_engine, hh, "Amex", "credit_card", "USD")
    repository.record_account_balance(demo_engine, card.id, -500_000)  # owes $5,000

    before, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD")
    repository.set_credit_cards_paid_in_full(demo_engine, hh, True)
    after, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD")

    assert after.outputs["credit_card_payments"] == Money(500_000, "USD")
    # The full card balance is now committed, so safe-to-spend drops by $5,000.
    drop = before.outputs["safe_to_spend"].amount_minor - after.outputs["safe_to_spend"].amount_minor
    assert drop == 500_000


def test_401k_loan_payment_is_payroll_deducted_and_balance_is_not_external_debt(
    demo_engine: Engine,
) -> None:
    """M97: a 401(k) loan is repaid by payroll deduction, so its payment does NOT
    reduce safe-to-spend (the money never reaches the bank); its balance is netted
    against retirement, so it's not added to reported total debt either. It stays
    'modeled' so it isn't warned as a liability with no recorded minimum."""
    from family_cfo_api import repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    before, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD")
    warnings_before = len(before.warnings)

    loan = repository.create_account(
        demo_engine, hh, "401k loan", "401k_loan", "USD",
        annual_interest_rate=0.0, minimum_payment_minor=40_000,  # $400/mo
    )
    repository.record_account_balance(demo_engine, loan.id, -2_000_000)  # owes $20,000
    after, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD")

    # The payment is NOT committed against liquid cash (payroll-deducted).
    assert (
        after.outputs["minimum_debt_payments"].amount_minor
        == before.outputs["minimum_debt_payments"].amount_minor
    )
    # The $20k balance is NOT added to reported total debt (netted against retirement).
    assert after.outputs["total_debt"].amount_minor == before.outputs["total_debt"].amount_minor
    # ...and it isn't flagged as an unmodeled liability (no new warning).
    assert len(after.warnings) == warnings_before


def test_safe_to_spend_flags_liabilities_with_no_recorded_minimum_payment(
    demo_engine: Engine,
) -> None:
    """The demo mortgage carries no terms, so its claim on the cash is invisible —
    the figure is overstated and must say so rather than quietly look healthy."""
    result, _ref = finance_service.compute_safe_to_spend(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD"
    )

    assert any("UNDERSTATED" in w for w in result.warnings)


def test_safe_to_spend_reports_liabilities_that_have_no_minimum_payment(
    demo_engine: Engine,
) -> None:
    """The user's real household carried $29,931 across three credit cards, none
    with a minimum payment recorded — so nothing was subtracted for debt and the
    advisor said nothing about it. Now the debt is reported and the shortfall
    named."""
    result, _ref = finance_service.compute_safe_to_spend(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD"
    )

    # The demo mortgage is a liability, so the household owes something.
    assert result.outputs["total_debt"].amount_minor > 0
    assert any("owes" in w for w in result.warnings)


def test_monthly_income_ignores_compensation_profiles(demo_engine: Engine) -> None:
    """M96: the W2 / compensation profile is a prior-year baseline for tax
    prediction, NOT this year's income. Income is actual money in, so adding a
    profile must not move the Overview income number."""
    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    before = finance_service.monthly_income_total(demo_engine, hh, "USD")

    repository.create_income_profile(
        demo_engine, hh, label="ACME",
        base_salary_minor=12_000_000,   # $120k base
        rsu_annual_minor=6_000_000,     # $60k RSU
        rsu_frequency="quarterly", rsu_next_vest_date=None,
        bonus_percent=10.0, bonus_month=None,      # +$12k bonus
        w2_year=None, w2_wages_minor=None, w2_withheld_minor=None,
    )

    after = finance_service.monthly_income_total(demo_engine, hh, "USD")
    assert after.amount_minor == before.amount_minor


def test_monthly_income_counts_categorized_income_inflows(demo_engine: Engine) -> None:
    """M96: inflows filed under the Income category count as actual money in,
    averaged over the trailing 12 complete months."""
    from datetime import date

    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date(2026, 7, 14)
    account_id = repository.list_account_balances(demo_engine, hh)[0].account_id
    income = repository.create_category(demo_engine, hh, "Income")
    before = finance_service.monthly_income_total(demo_engine, hh, "USD", today=today)

    # Two paychecks last month, filed as Income -> $12,000 over 12 months = $1,000/mo.
    for day in (date(2026, 6, 15), date(2026, 6, 30)):
        repository.create_transaction(
            demo_engine, household_id=hh, account_id=account_id, occurred_at=day,
            amount_minor=600_000, currency="USD", merchant="Online Transfer",
            description=None, import_source=None, import_id=None,
            review_state="reviewed", category_id=income.id,
        )

    after = finance_service.monthly_income_total(demo_engine, hh, "USD", today=today)
    assert after.amount_minor - before.amount_minor == 100_000


def test_categorizing_one_transaction_fills_the_merchants_uncategorized_siblings(
    demo_engine: Engine,
) -> None:
    """M96: categorizing one 'Blue Sky Center' files every other uncategorized
    'Blue Sky Center' under the same category (minimize duplicate input) — while a
    same-merchant inflow (sign differs), an already-categorized sibling, and a
    different merchant are all left alone."""
    from datetime import date

    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    account_id = repository.list_account_balances(demo_engine, hh)[0].account_id
    dining = repository.create_category(demo_engine, hh, "DiningTest")
    other = repository.create_category(demo_engine, hh, "OtherTest")

    def make(day: date, amount: int, merchant: str = "Blue Sky Center", cat: str | None = None) -> str:
        return repository.create_transaction(
            demo_engine, household_id=hh, account_id=account_id, occurred_at=day,
            amount_minor=amount, currency="USD", merchant=merchant, description=None,
            import_source=None, import_id=None, review_state="reviewed", category_id=cat,
        )

    target = make(date(2026, 5, 1), -1_323, cat=dining.id)  # the one we categorized
    sibling1 = make(date(2026, 5, 6), -1_282)  # uncategorized outflow -> should fill
    sibling2 = make(date(2026, 5, 29), -701)  # uncategorized outflow -> should fill
    inflow = make(date(2026, 5, 3), 5_000)  # inflow: sign differs, left alone
    already = make(date(2026, 5, 8), -900, cat=other.id)  # already categorized, left alone
    elsewhere = make(date(2026, 5, 4), -400, merchant="Proclean")  # other merchant

    n = finance_service.propagate_category_to_merchant(demo_engine, hh, target, dining.id)

    assert n == 2
    assert repository.get_transaction(demo_engine, hh, sibling1).category_id == dining.id
    assert repository.get_transaction(demo_engine, hh, sibling2).category_id == dining.id
    assert repository.get_transaction(demo_engine, hh, inflow).category_id is None
    assert repository.get_transaction(demo_engine, hh, already).category_id == other.id
    assert repository.get_transaction(demo_engine, hh, elsewhere).category_id is None


def test_flag_possible_duplicates_flags_exact_groups_and_respects_dismissal(
    demo_engine: Engine,
) -> None:
    """M97: two identical bank charges (same account/date/amount/merchant) with
    different provider ids are flagged for review; a lone charge is not; and once
    the user dismisses them, a re-run never flags them again."""
    from datetime import date

    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    account_id = repository.list_account_balances(demo_engine, hh)[0].account_id

    def synced(ext: str, amount: int, day: date, merchant: str) -> None:
        repository.create_transaction_deduped(
            demo_engine, hh, account_id, day, amount, "USD", merchant, merchant,
            "bank_sync", external_id=ext,
        )

    synced("TRN-1", -2_400, date(2026, 5, 19), "Proclean Auto Wash")
    synced("TRN-2", -2_400, date(2026, 5, 19), "Proclean Auto Wash")  # exact dup
    synced("TRN-3", -999, date(2026, 5, 20), "Cafe")  # lone charge

    assert repository.flag_possible_duplicates(demo_engine, hh) == 2
    assert repository.count_review_transactions(demo_engine, hh) == 2
    review = repository.list_transactions(
        demo_engine, hh, duplicate_states=repository.REVIEW_DUPLICATE_STATES
    )
    assert {t.merchant for t in review} == {"Proclean Auto Wash"}

    # Re-running is idempotent — already-flagged rows aren't re-counted.
    assert repository.flag_possible_duplicates(demo_engine, hh) == 0

    # "Keep both" dismisses the group; detection must never re-flag it.
    for t in review:
        repository.set_transaction_duplicate_state(demo_engine, hh, t.id, "dismissed")
    assert repository.flag_possible_duplicates(demo_engine, hh) == 0
    assert repository.count_review_transactions(demo_engine, hh) == 0


def test_flag_skips_non_spending_categories_and_clears_stale_flags(
    demo_engine: Engine,
) -> None:
    """M97: identical RSU sell-to-cover lots (Income) and their tax journals
    (Taxes) legitimately repeat, so they must NOT clutter the Review queue — and a
    row flagged before it was categorized as non-spending gets cleared."""
    from datetime import date

    from family_cfo_api import fixtures, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    account_id = repository.list_account_balances(demo_engine, hh)[0].account_id
    income = repository.create_category(demo_engine, hh, "Income")

    for ext in ("RSU-1", "RSU-2"):
        repository.create_transaction_deduped(
            demo_engine, hh, account_id, date(2026, 6, 18), 1_009_701, "USD",
            "Broadcom Inc", "BROADCOM INC", "bank_sync", external_id=ext,
        )

    # Flagged first (while uncategorized)...
    assert repository.flag_possible_duplicates(demo_engine, hh) == 2
    # ...then filed as Income, which should clear the flags on the next run.
    ids = [
        t.id
        for t in repository.list_transactions(demo_engine, hh, limit=100_000)
        if t.merchant == "Broadcom Inc"
    ]
    repository.set_transactions_category(demo_engine, hh, ids, income.id)

    assert repository.flag_possible_duplicates(demo_engine, hh) == 0
    assert repository.count_review_transactions(demo_engine, hh) == 0


def test_debt_that_is_also_a_bill_is_reserved_once_not_twice(demo_engine: Engine) -> None:
    """ADR 0032: a loan modeled as both an account and an explicit bill must be
    reserved once. Adding the bill for an already-reserved debt moves the payment
    from the debt line to the bills line — it never reserves it a second time, so
    safe-to-spend is unchanged."""
    hh = fixtures.DEMO_HOUSEHOLD_ID
    today = date.today()
    loan = repository.create_account(
        demo_engine, hh, name="U.S. Department of Education",
        account_type="student_loan", currency="USD", minimum_payment_minor=7_801,
    )
    repository.record_account_balance(demo_engine, loan.id, -1_000_000)

    before, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD", today=today)

    # Now ALSO model the same payment as an explicit bill, due within the horizon.
    repository.create_bill(
        demo_engine, hh, name="Department of Education", amount_minor=7_801,
        currency="USD", frequency="monthly", next_due_date=today + timedelta(days=5),
    )
    after, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD", today=today)

    # Same money reserved, just relabeled from the debt line to the bill line.
    assert after.outputs["safe_to_spend"] == before.outputs["safe_to_spend"]
    assert after.outputs["minimum_debt_payments"] == (
        before.outputs["minimum_debt_payments"] - Money(7_801, "USD")
    )
    assert after.outputs["bills_due"] == before.outputs["bills_due"] + Money(7_801, "USD")
