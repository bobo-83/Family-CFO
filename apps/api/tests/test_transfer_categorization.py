"""M96: transfers are excluded from spending, and obvious transfers / known
merchants file themselves so the user need not tag them twice."""

from datetime import date

from family_cfo_api import finance_service, fixtures, repository

_HH = fixtures.DEMO_HOUSEHOLD_ID


def _account(engine) -> str:
    return repository.list_account_balances(engine, _HH)[0].account_id


def _txn(engine, account_id, amount_minor, *, merchant=None, description=None, category_id=None) -> str:
    return repository.create_transaction(
        engine,
        household_id=_HH,
        account_id=account_id,
        occurred_at=date(2026, 6, 15),
        amount_minor=amount_minor,
        currency="USD",
        merchant=merchant,
        description=description,
        import_source=None,
        import_id=None,
        review_state="reviewed",
        category_id=category_id,
    )


def test_transfers_category_excluded_from_spending(demo_engine) -> None:
    account_id = _account(demo_engine)
    transfers = repository.create_category(demo_engine, _HH, "Transfers")
    dining = repository.create_category(demo_engine, _HH, "Dining")

    _txn(demo_engine, account_id, -3_000_00, merchant="Internet Transfer", category_id=transfers.id)
    _txn(demo_engine, account_id, -5_000, merchant="Cafe", category_id=dining.id)
    _txn(demo_engine, account_id, -2_000, merchant="Uncategorized outflow")  # still counts

    total = repository.sum_spending(demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD")
    assert total == 7_000  # the $3,000 transfer is excluded; the rest counts

    by_cat = repository.sum_spending_by_category(
        demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD"
    )
    assert transfers.id not in by_cat
    assert by_cat[dining.id] == 5_000


def test_autofile_transfers_files_obvious_transfers(demo_engine) -> None:
    account_id = _account(demo_engine)
    repository.create_category(demo_engine, _HH, "Transfers")

    outflow = _txn(demo_engine, account_id, -3_000_00, merchant="Internet Transfer TO BANK OF AMERICA")
    inflow_payment = _txn(demo_engine, account_id, 2_100_58, merchant="Credit Card Payment")
    bare_inflow = _txn(demo_engine, account_id, 649_73, merchant="Payment")
    real_expense = _txn(demo_engine, account_id, -8_000, merchant="Grocery Store")

    filed = finance_service.autofile_transfers(demo_engine, _HH)
    assert filed == 2

    txns = {t.id: t for t in repository.list_transactions(demo_engine, _HH, limit=1000)}
    transfers_id = next(c.id for c in repository.list_categories(demo_engine, _HH) if c.name == "Transfers")
    assert txns[outflow].category_id == transfers_id          # transfer-labelled outflow
    assert txns[inflow_payment].category_id == transfers_id   # explicit card payment
    # A lone "Payment" inflow with no matching outflow is ambiguous (could be a
    # client paying you) — left for the user rather than buried as a transfer.
    assert txns[bare_inflow].category_id is None
    assert txns[real_expense].category_id is None  # a real expense is left alone


def test_paycheck_labeled_online_transfer_is_income_not_transfer(demo_engine) -> None:
    """The RSU/paycheck case: an inflow labelled 'Online Transfer' with no matching
    outflow from a linked account is money arriving — a paycheck — and must be
    recognised as income, never buried as a transfer."""
    account_id = _account(demo_engine)
    repository.create_category(demo_engine, _HH, "Transfers")
    income = repository.create_category(demo_engine, _HH, "Income")

    # Biweekly paychecks, all labelled "Online Transfer", no matching outflow.
    from datetime import date

    for day in (date(2026, 5, 1), date(2026, 5, 15), date(2026, 5, 29), date(2026, 6, 12)):
        repository.create_transaction(
            demo_engine, household_id=_HH, account_id=account_id, occurred_at=day,
            amount_minor=283_078, currency="USD", merchant="Online Transfer",
            description=None, import_source=None, import_id=None, review_state="reviewed",
        )

    # Transfers must NOT claim them.
    assert finance_service.autofile_transfers(demo_engine, _HH) == 0
    # Income detection recognises the recurring deposit.
    assert finance_service.autofile_income(demo_engine, _HH) == 4
    txns = {t.id: t for t in repository.list_transactions(demo_engine, _HH, limit=1000)}
    assert all(
        t.category_id == income.id for t in txns.values() if t.merchant == "Online Transfer"
    )


def test_internal_transfer_with_matching_outflow_is_transfer(demo_engine) -> None:
    """An inflow that matches money leaving a linked account IS an internal transfer."""
    from datetime import date

    account_id = _account(demo_engine)
    transfers = repository.create_category(demo_engine, _HH, "Transfers")

    # $5,000 leaves one account and $5,000 arrives, same day -> internal transfer.
    out_id = _txn(demo_engine, account_id, -500_000, merchant="Online Transfer")
    in_id = repository.create_transaction(
        demo_engine, household_id=_HH, account_id=account_id, occurred_at=date(2026, 6, 15),
        amount_minor=500_000, currency="USD", merchant="Online Transfer",
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )

    finance_service.autofile_transfers(demo_engine, _HH)
    txns = {t.id: t for t in repository.list_transactions(demo_engine, _HH, limit=1000)}
    assert txns[in_id].category_id == transfers.id   # matched inflow -> transfer
    assert txns[out_id].category_id == transfers.id   # the outflow leg -> transfer


def test_issuer_named_card_payment_is_transfer(demo_engine) -> None:
    """An outflow paying a card, labelled by the issuer ('American Express Credit
    Card') rather than 'credit card payment', is still a transfer, not spending."""
    account_id = _account(demo_engine)
    transfers = repository.create_category(demo_engine, _HH, "Transfers")
    amex = _txn(demo_engine, account_id, -3_300_00, merchant="American Express Credit Card")

    finance_service.autofile_transfers(demo_engine, _HH)
    txns = {t.id: t for t in repository.list_transactions(demo_engine, _HH, limit=1000)}
    assert txns[amex].category_id == transfers.id


def test_transfer_counterparty_pairs_the_two_legs(demo_engine) -> None:
    """A transfer's two legs (out of one account, into another, same amount, close
    in time) resolve each other as counterparty so the UI can show source → dest."""
    from family_cfo_api.api.transactions import _counterparties

    accounts = repository.account_name_map(demo_engine, _HH)
    acct_a, acct_b = list(accounts)[:2]

    out_id = repository.create_transaction(
        demo_engine, household_id=_HH, account_id=acct_a, occurred_at=date(2026, 6, 15),
        amount_minor=-500_000, currency="USD", merchant="Online Transfer",
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )
    in_id = repository.create_transaction(
        demo_engine, household_id=_HH, account_id=acct_b, occurred_at=date(2026, 6, 16),
        amount_minor=500_000, currency="USD", merchant="Online Transfer",
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )

    records = repository.list_transactions(demo_engine, _HH, limit=1000)
    pairs = _counterparties(records, accounts)
    assert pairs[out_id] == accounts[acct_b]  # outflow's dest is account B
    assert pairs[in_id] == accounts[acct_a]   # inflow's source is account A


def test_refund_in_a_spending_category_nets_against_it(demo_engine) -> None:
    """A refund is a positive transaction filed under a spending category — it must
    cancel the purchase, not sit ignored while the expense still counts."""
    account_id = _account(demo_engine)
    shopping = repository.create_category(demo_engine, _HH, "Shopping")

    _txn(demo_engine, account_id, -7_500, merchant="Lululemon", category_id=shopping.id)  # buy
    _txn(demo_engine, account_id, 7_500, merchant="Lululemon", category_id=shopping.id)  # refund
    _txn(demo_engine, account_id, -2_000, merchant="Cafe")  # unrelated outflow

    total = repository.sum_spending(demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD")
    assert total == 2_000  # the Lululemon purchase and refund cancel out

    by_cat = repository.sum_spending_by_category(
        demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD"
    )
    assert by_cat.get(shopping.id, 0) == 0  # net Shopping spend is zero


def test_uncategorized_inflow_is_not_treated_as_a_refund(demo_engine) -> None:
    """An UNcategorized deposit (e.g. a stray credit) is not a refund for any
    category, so it must not reduce spending."""
    account_id = _account(demo_engine)
    _txn(demo_engine, account_id, -5_000, merchant="Store")  # outflow counts
    _txn(demo_engine, account_id, 9_000, merchant="Mystery deposit")  # uncategorized inflow

    total = repository.sum_spending(demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD")
    assert total == 5_000  # the uncategorized inflow does NOT net


def test_gencash_filed_as_taxes_and_excluded_from_spending(demo_engine) -> None:
    """RSU sell-to-cover ('Gencash … Lapse') is tax withholding: filed under a
    Taxes category (auto-created) and kept out of the discretionary spending total."""
    account_id = _account(demo_engine)
    gencash = _txn(
        demo_engine, account_id, -9_827_82,
        merchant="Gencash Transaction for Lapse Tool",
        description="Gencash transaction for SPS RS Lapse Tool",
    )
    _txn(demo_engine, account_id, -5_000, merchant="Cafe")  # real spending

    filed = finance_service.autofile_taxes(demo_engine, _HH)
    assert filed == 1
    taxes = next(c for c in repository.list_categories(demo_engine, _HH) if c.name == "Taxes")
    txns = {t.id: t for t in repository.list_transactions(demo_engine, _HH, limit=1000)}
    assert txns[gencash].category_id == taxes.id

    total = repository.sum_spending(demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD")
    assert total == 5_000  # the $9,827 tax withholding is NOT counted as spending
    tax_total = repository.sum_taxes(demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD")
    assert tax_total == 9_827_82


def test_autofile_transfers_noop_without_category(demo_engine) -> None:
    account_id = _account(demo_engine)
    _txn(demo_engine, account_id, -3_000_00, merchant="Internet Transfer")
    assert finance_service.autofile_transfers(demo_engine, _HH) == 0


def test_income_category_excluded_from_spending(demo_engine) -> None:
    """An outflow that ended up under Income (or Transfers) must not appear in the
    spending total or breakdown — Income is not a spending category."""
    account_id = _account(demo_engine)
    income = repository.create_category(demo_engine, _HH, "Income")
    dining = repository.create_category(demo_engine, _HH, "Dining")

    _txn(demo_engine, account_id, -4_000, merchant="Mislabelled", category_id=income.id)
    _txn(demo_engine, account_id, -6_000, merchant="Cafe", category_id=dining.id)

    total = repository.sum_spending(demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD")
    by_cat = repository.sum_spending_by_category(
        demo_engine, _HH, date(2026, 6, 1), date(2026, 6, 30), "USD"
    )
    assert total == 6_000              # the Income-tagged outflow is excluded
    assert income.id not in by_cat
    assert by_cat[dining.id] == 6_000


def test_autocategorize_does_not_cross_signs(demo_engine) -> None:
    """A merchant's inflow category must not be applied to its outflows: a Broadcom
    RSU deposit filed as Income must not drag a Broadcom purchase into Income."""
    account_id = _account(demo_engine)
    income = repository.create_category(demo_engine, _HH, "Income")

    _txn(demo_engine, account_id, 50_000, merchant="Broadcom Inc", category_id=income.id)  # inflow -> Income
    outflow = _txn(demo_engine, account_id, -12_000, merchant="Broadcom Inc")  # uncategorized outflow

    finance_service.autocategorize_by_history(demo_engine, _HH)
    txns = {t.id: t for t in repository.list_transactions(demo_engine, _HH, limit=1000)}
    assert txns[outflow].category_id is None  # outflow NOT dragged into Income


def test_autocategorize_by_history_reuses_prior_choice(demo_engine) -> None:
    account_id = _account(demo_engine)
    dining = repository.create_category(demo_engine, _HH, "Dining")

    # The user categorized Starbucks once before.
    _txn(demo_engine, account_id, -6_50, merchant="STARBUCKS #123", category_id=dining.id)
    # New syncs bring more Starbucks, uncategorized, plus an unknown merchant.
    again = _txn(demo_engine, account_id, -5_25, merchant="STARBUCKS #999")
    unknown = _txn(demo_engine, account_id, -9_00, merchant="New Bodega")

    filed = finance_service.autocategorize_by_history(demo_engine, _HH)
    assert filed == 1

    txns = {t.id: t for t in repository.list_transactions(demo_engine, _HH, limit=1000)}
    assert txns[again].category_id == dining.id
    assert txns[unknown].category_id is None  # never seen -> left for the user
