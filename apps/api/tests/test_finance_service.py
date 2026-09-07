from datetime import date, timedelta

from family_cfo_financial_engine import Money
from sqlalchemy import select
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, fixtures, models, repository


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
    """A household can carry five figures across several credit cards with no
    minimum payment recorded — so nothing was subtracted for debt and the
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
            demo_engine, hh, account_id, date(2026, 6, 18), 1_023_459, "USD",
            "Initrode Inc", "INITRODE INC", "bank_sync", external_id=ext,
        )

    # Flagged first (while uncategorized)...
    assert repository.flag_possible_duplicates(demo_engine, hh) == 2
    # ...then filed as Income, which should clear the flags on the next run.
    ids = [
        t.id
        for t in repository.list_transactions(demo_engine, hh, limit=100_000)
        if t.merchant == "Initrode Inc"
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
        account_type="student_loan", currency="USD", minimum_payment_minor=8_153,
    )
    repository.record_account_balance(demo_engine, loan.id, -1_000_000)

    before, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD", today=today)

    # Now ALSO model the same payment as an explicit bill, due within the horizon.
    repository.create_bill(
        demo_engine, hh, name="Department of Education", amount_minor=8_153,
        currency="USD", frequency="monthly", next_due_date=today + timedelta(days=5),
    )
    after, _ = finance_service.compute_safe_to_spend(demo_engine, hh, "USD", today=today)

    # Same money reserved, just relabeled from the debt line to the bill line.
    assert after.outputs["safe_to_spend"] == before.outputs["safe_to_spend"]
    assert after.outputs["minimum_debt_payments"] == (
        before.outputs["minimum_debt_payments"] - Money(8_153, "USD")
    )
    assert after.outputs["bills_due"] == before.outputs["bills_due"] + Money(8_153, "USD")


# --- #152: a foreign-currency account is excluded from base-currency figures and disclosed ---

_HH = fixtures.DEMO_HOUSEHOLD_ID
_EUR_WARNING = (
    "1 account held in EUR is not counted in this USD figure; a balance in another "
    "currency is never converted."
)
_EUR_DESIGNATION_WARNING = (
    "An emergency-fund designation on 1 account held in EUR is ignored; designations "
    "count only in USD."
)


def _persisted_rows(engine: Engine, calculation_type: str):
    with engine.connect() as conn:
        return (
            conn.execute(
                select(models.financial_calculations).where(
                    models.financial_calculations.c.calculation_type == calculation_type
                )
            )
            .mappings()
            .all()
        )


def _demo_savings(engine: Engine):
    return next(b for b in repository.list_account_balances(engine, _HH) if b.name == "Savings")


def test_partition_splits_on_currency_and_honours_eligibility(
    demo_engine: Engine, foreign_currency_account
) -> None:
    balances = repository.list_account_balances(demo_engine, _HH)

    everything = finance_service.partition_balances_by_currency(balances, "USD")
    assert {b.currency for b in everything.in_base} == {"USD"}
    assert [b.name for b in everything.foreign] == ["Euro Savings"]
    # Typed in the account's OWN currency, so it can never read as USD.
    assert everything.excluded_accounts[0].balance == Money(400_000, "EUR")

    # Eligibility-specific: a foreign account a figure never counted is not
    # "excluded" from it — the disclosure must not claim otherwise.
    none = finance_service.partition_balances_by_currency(
        balances, "USD", eligible=lambda b: b.account_type == "brokerage"
    )
    assert none.foreign == []
    assert len(none.in_base) == len(everything.in_base)


def test_net_worth_excludes_the_foreign_account_and_discloses_it(
    demo_engine: Engine, foreign_currency_account
) -> None:
    """THE regression: this raised CurrencyMismatchError (USD vs EUR)."""
    result = finance_service.compute_net_worth(demo_engine, _HH, "USD")

    assert result.outputs["net_worth"] == Money(500_000 + 1_500_000 - 300_000_000, "USD")
    assert result.outputs["excluded_accounts"] == [
        {"name": "Euro Savings", "type": "savings", "balance": Money(400_000, "EUR")}
    ]
    assert result.warnings == [_EUR_WARNING]
    assert result.inputs["excluded_account_count"] == 1
    assert result.inputs["excluded_currencies"] == ["EUR"]


def test_persisted_calculation_records_the_exclusion_without_the_account_name(
    demo_engine: Engine, foreign_currency_account
) -> None:
    """ADR 0003 wants the row to say what was left out. ADR 0072 keeps account
    names sealed — and the calculation's JSON columns are plaintext, so the row
    carries the count, the currencies and a generic warning, never a name."""
    import json

    finance_service.compute_net_worth(demo_engine, _HH, "USD")

    rows = _persisted_rows(demo_engine, "net_worth")
    assert len(rows) == 1
    row = rows[0]
    assert row["inputs_json"]["excluded_account_count"] == 1
    assert row["inputs_json"]["excluded_currencies"] == ["EUR"]
    assert row["warnings_json"] == [_EUR_WARNING]
    assert "excluded_accounts" not in row["outputs_json"]  # live disclosure only
    stored = json.dumps([row["inputs_json"], row["outputs_json"], row["warnings_json"]])
    assert "Euro Savings" not in stored


def test_single_currency_household_records_zero_exclusions(demo_engine: Engine) -> None:
    result = finance_service.compute_net_worth(demo_engine, _HH, "USD")

    assert result.outputs["excluded_accounts"] == []
    assert result.warnings == []
    assert result.inputs["excluded_account_count"] == 0
    assert result.inputs["excluded_currencies"] == []


def test_emergency_fund_all_liquid_fallback_leaves_out_the_foreign_savings(
    demo_engine: Engine, foreign_currency_account
) -> None:
    inputs = finance_service.emergency_fund_inputs(demo_engine, _HH, "USD")
    assert inputs.fund == Money(2_000_000, "USD")
    assert inputs.using_designations is False
    assert [a.name for a in inputs.excluded_accounts] == ["Euro Savings"]
    assert inputs.warnings == []  # nothing designated, so nothing ignored

    result = finance_service.compute_emergency_fund(demo_engine, _HH, "USD")
    assert result.outputs["liquid_balance"] == Money(2_000_000, "USD")
    assert result.outputs["emergency_fund_months"] == 2_000_000 / 208_000
    assert result.warnings == [_EUR_WARNING]


def test_designation_on_a_foreign_account_is_ignored_and_disclosed(
    demo_engine: Engine, foreign_currency_account
) -> None:
    """The designation loop already filtered on currency — silently. Silent was
    the bug: a family that earmarked its EUR savings for emergencies saw a fund
    that ignored it and was given no reason."""
    repository.update_account(
        demo_engine, _HH, _demo_savings(demo_engine).account_id, emergency_fund_percent=50.0
    )
    repository.update_account(
        demo_engine, _HH, foreign_currency_account.id, emergency_fund_percent=100.0
    )

    inputs = finance_service.emergency_fund_inputs(demo_engine, _HH, "USD")
    assert inputs.using_designations is True
    assert inputs.fund == Money(750_000, "USD")
    assert [a.name for a in inputs.excluded_accounts] == ["Euro Savings"]
    assert inputs.warnings == [_EUR_DESIGNATION_WARNING]

    result = finance_service.compute_emergency_fund(demo_engine, _HH, "USD")
    assert result.outputs["liquid_balance"] == Money(750_000, "USD")
    assert result.warnings == [_EUR_WARNING, _EUR_DESIGNATION_WARNING]


def test_undesignated_foreign_liquid_account_is_not_excluded_from_a_designated_fund(
    demo_engine: Engine, foreign_currency_account
) -> None:
    """Once designations define the fund, an undesignated foreign savings account
    was never part of it — so it is not reported as excluded from it."""
    repository.update_account(
        demo_engine, _HH, _demo_savings(demo_engine).account_id, emergency_fund_percent=50.0
    )

    inputs = finance_service.emergency_fund_inputs(demo_engine, _HH, "USD")
    assert inputs.using_designations is True
    assert inputs.excluded_accounts == []
    assert inputs.warnings == []


def test_safe_to_spend_survives_a_foreign_account_and_discloses_it(
    demo_engine: Engine, foreign_currency_account
) -> None:
    """Its own liquid loop filtered correctly; it inherited the crash through
    emergency_fund_inputs."""
    result, _ = finance_service.compute_safe_to_spend(demo_engine, _HH, "USD")

    assert result.outputs["liquid_balance"] == Money(2_000_000, "USD")
    assert [a["name"] for a in result.outputs["excluded_accounts"]] == ["Euro Savings"]
    assert _EUR_WARNING in result.warnings


def test_safe_to_spend_discloses_a_foreign_debt_but_not_a_foreign_brokerage(
    demo_engine: Engine,
) -> None:
    """Disclosure is per figure. Safe-to-spend touches cash, reservations and
    debts, so a foreign card is reported as left out of it — while a foreign
    brokerage account, which it never counted, is not. Net worth reports both."""
    before, _ = finance_service.compute_safe_to_spend(demo_engine, _HH, "USD")
    card = repository.create_account(demo_engine, _HH, "Euro Card", "credit_card", "EUR")
    repository.record_account_balance(demo_engine, card.id, -50_000)
    brokerage = repository.create_account(demo_engine, _HH, "Euro Brokerage", "brokerage", "EUR")
    repository.record_account_balance(demo_engine, brokerage.id, 1_000_000)

    after, _ = finance_service.compute_safe_to_spend(demo_engine, _HH, "USD")
    assert [a["name"] for a in after.outputs["excluded_accounts"]] == ["Euro Card"]
    # Neither added nor converted: every USD figure is exactly what it was.
    assert after.outputs["total_debt"] == before.outputs["total_debt"]
    assert after.outputs["safe_to_spend"] == before.outputs["safe_to_spend"]

    net_worth = finance_service.compute_net_worth(demo_engine, _HH, "USD")
    assert sorted(a["name"] for a in net_worth.outputs["excluded_accounts"]) == [
        "Euro Brokerage",
        "Euro Card",
    ]
    assert net_worth.warnings == [
        (
            "2 accounts held in EUR are not counted in this USD figure; a balance in another "
            "currency is never converted."
        )
    ]


def test_purchase_impact_survives_a_foreign_account(
    demo_engine: Engine, foreign_currency_account
) -> None:
    result, _ = finance_service.compute_purchase_impact(
        demo_engine, _HH, "USD", Money(100_000, "USD")
    )

    assert result.outputs["net_worth_before"] == Money(-298_000_000, "USD")
    assert result.outputs["net_worth_after"] == Money(-298_100_000, "USD")
    # liquid 2_000_000 - 100_000 over 208_000/month of essentials — USD only.
    assert result.outputs["emergency_fund_months_after"] == 1_900_000 / 208_000
    assert [a["name"] for a in result.outputs["excluded_accounts"]] == ["Euro Savings"]
    assert _EUR_WARNING in result.warnings


def test_goal_current_for_an_emergency_fund_goal_survives_a_foreign_account(
    demo_engine: Engine, foreign_currency_account
) -> None:
    """`goal_current_minor` reads the fund through emergency_fund_inputs, so the
    Overview's top-goal card and GET /goals went down with the home screen."""
    goal = next(
        g for g in repository.list_goals(demo_engine, _HH) if g.goal_type == "emergency_fund"
    )
    # No designation: the stored current (the M41 rule), and no crash.
    assert finance_service.goal_current_minor(demo_engine, _HH, goal) == 1_500_000

    # Designations in the goal's currency define the fund; the EUR one is ignored.
    repository.update_account(
        demo_engine, _HH, _demo_savings(demo_engine).account_id, emergency_fund_percent=50.0
    )
    repository.update_account(
        demo_engine, _HH, foreign_currency_account.id, emergency_fund_percent=100.0
    )
    assert finance_service.goal_current_minor(demo_engine, _HH, goal) == 750_000
