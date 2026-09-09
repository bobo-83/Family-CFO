"""ADR 0076: readable aggregate leaves survive sealed amount corruption."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet
from family_cfo_financial_engine import Money
from sqlalchemy import func, select
from sqlalchemy import text as sql_text

from family_cfo_api import (
    ai_tools,
    finance_service,
    fixtures,
    household_clock,
    household_crypto,
    models,
    repository,
    savings_detection,
)
from family_cfo_api.config import get_settings
from family_cfo_api.qualified_amounts import Qualified, UnreadableAmountSource, union_sources

HH = fixtures.DEMO_HOUSEHOLD_ID


@pytest.fixture
def _master_key(monkeypatch):
    """Use an explicit synthetic key; tests never depend on a host credential."""
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _damage_cell(engine, table: str, row_id: str, column: str, plaintext: bytes = b"1234") -> str:
    foreign = household_crypto._subkey_fernet(Fernet.generate_key(), b"rows")
    token = household_crypto.ENC_PREFIX + foreign.encrypt(plaintext).decode()
    with engine.begin() as conn:
        conn.execute(
            sql_text(f"update {table} set {column} = :value where id = :id"),
            {"value": token, "id": row_id},
        )
    return token


def _transaction(
    engine,
    *,
    amount_minor: int,
    occurred_at: date,
    category_id: str | None = None,
    merchant: str = "Qualification fixture",
    account_id: str | None = None,
):
    resolved_account_id = (
        account_id or repository.list_account_balances(engine, HH)[0].account_id
    )
    return repository.create_transaction(
        engine,
        household_id=HH,
        account_id=resolved_account_id,
        occurred_at=occurred_at,
        amount_minor=amount_minor,
        currency="USD",
        merchant=merchant,
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
        category_id=category_id,
    )


def _card_with_statements(engine, *, today: date) -> tuple[str, str, str]:
    card = repository.create_account(
        engine,
        HH,
        name="Qualification Visa",
        account_type="credit_card",
        currency="USD",
    )
    repository.record_account_balance(engine, card.id, -999_999)
    older = repository.upsert_card_statement(
        engine,
        HH,
        account_id=card.id,
        statement_balance_minor=22_000,
        minimum_due_minor=2_200,
        currency="USD",
        due_date=today + timedelta(days=3),
    )
    newest = repository.upsert_card_statement(
        engine,
        HH,
        account_id=card.id,
        statement_balance_minor=44_000,
        minimum_due_minor=4_400,
        currency="USD",
        due_date=today + timedelta(days=5),
    )
    return card.id, older.id, newest.id


def test_source_identity_and_union_are_immutable_and_deduplicated() -> None:
    source = UnreadableAmountSource(HH, "transactions", "row-1", "amount_minor")
    other = UnreadableAmountSource(HH, "transactions", "row-2", "amount_minor")
    first = Qualified(10, frozenset({source}))
    second = Qualified(20, frozenset({source, other}))

    combined = union_sources(first, second, first)

    assert combined == frozenset({source, other})
    assert len(combined) == 2
    with pytest.raises(AttributeError):
        combined.add(source)  # type: ignore[attr-defined]


def test_category_totals_are_direct_and_sources_are_attributed(
    _master_key, demo_engine
) -> None:
    category = repository.create_category(demo_engine, HH, "Qualification category")
    readable = _transaction(
        demo_engine,
        amount_minor=-3_000,
        occurred_at=date.today(),
        category_id=category.id,
    )
    damaged = _transaction(
        demo_engine,
        amount_minor=-7_000,
        occurred_at=date.today(),
        category_id=category.id,
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    totals = repository.category_spending_totals(
        demo_engine, HH, date.today().replace(day=1), date.today(), "USD"
    )
    category_total = totals.by_category[category.id]

    assert readable != damaged
    assert category_total.value == 3_000
    assert category_total.incomplete_count == 1
    assert totals.categorized_total.incomplete_count == 1
    assert totals.overall.incomplete_count == 1
    assert totals.uncategorized.incomplete_count == 0
    assert len(union_sources(category_total, totals.categorized_total, totals.overall)) == 1


def test_nonmatching_sql_predicates_do_not_taint_aggregate(
    _master_key, demo_engine
) -> None:
    damaged = _transaction(
        demo_engine,
        amount_minor=-8_000,
        occurred_at=date.today() - timedelta(days=500),
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    result = repository.sum_spending(
        demo_engine, HH, date.today().replace(day=1), date.today(), "USD"
    )

    assert result.is_complete


def test_single_candidate_lookup_is_not_limited_by_bulk_iterator(
    _master_key, demo_engine, monkeypatch
) -> None:
    transaction_id = _transaction(
        demo_engine, amount_minor=-1_234, occurred_at=date.today()
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("single lookup must not use the bulk iterator")

    monkeypatch.setattr(repository, "iter_transaction_amount_candidates", forbidden)
    candidate = repository.get_transaction_amount_candidate(
        demo_engine, HH, transaction_id
    )

    assert candidate is not None
    assert candidate.metadata.id == transaction_id
    assert candidate.amount == -1_234


def test_transaction_candidate_iterator_exhausts_equal_timestamp_pages(
    demo_engine, monkeypatch
) -> None:
    category = repository.create_category(demo_engine, HH, "Paged candidates")
    dates = [date(2035, 1, 2), date(2035, 1, 2), date(2035, 1, 2), date(2035, 1, 1)]
    ids = [
        _transaction(
            demo_engine,
            amount_minor=-(index + 1),
            occurred_at=occurred_at,
            category_id=category.id,
        )
        for index, occurred_at in enumerate(dates)
    ]
    monkeypatch.setattr(repository, "_TRANSACTION_AMOUNT_PAGE_SIZE", 2)

    candidates = list(
        repository.iter_transaction_amount_candidates(
            demo_engine,
            HH,
            start=date(2035, 1, 1),
            end=date(2035, 1, 2),
            currency="USD",
            category_id=category.id,
        )
    )

    actual = [(candidate.metadata.occurred_at, candidate.metadata.id) for candidate in candidates]
    expected = sorted(zip(dates, ids, strict=True), reverse=True)
    assert actual == expected
    assert len({candidate.metadata.id for candidate in candidates}) == len(ids)


def test_excluded_unreadable_possible_debit_marks_only_its_currency_unavailable(
    _master_key, demo_engine
) -> None:
    eur = repository.create_account(
        demo_engine, HH, "EUR checking", "checking", "EUR"
    )
    damaged_eur = repository.create_transaction(
        demo_engine,
        household_id=HH,
        account_id=eur.id,
        occurred_at=date.today(),
        amount_minor=50_000,
        currency="EUR",
        merchant="EUR payroll",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    _damage_cell(demo_engine, "transactions", damaged_eur, "amount_minor")
    excluded_usd = _transaction(
        demo_engine,
        amount_minor=60_000,
        occurred_at=date.today(),
        merchant="Excluded payroll",
    )
    assert repository.set_income_override(demo_engine, HH, excluded_usd, "exclude")
    _damage_cell(demo_engine, "transactions", excluded_usd, "amount_minor")

    detection = finance_service.recurring_income_candidates(
        demo_engine, HH, since=date.today() - timedelta(days=365)
    )

    assert len(detection.sources_for_currency("USD")) == 1
    assert len(detection.sources_for_currency("EUR")) == 1
    assert not detection.is_complete_for("USD")
    assert not detection.is_complete_for("EUR")


def test_paged_candidate_reaches_reconstruction_subscription_and_savings(
    _master_key, demo_engine, monkeypatch
) -> None:
    today = date.today()
    category = next(
        (
            candidate
            for candidate in repository.list_categories(demo_engine, HH)
            if candidate.name.lower() == "subscriptions"
        ),
        None,
    ) or repository.create_category(demo_engine, HH, "Subscriptions")
    transaction_ids = [
        _transaction(
            demo_engine,
            amount_minor=-1_500,
            occurred_at=today,
            category_id=category.id,
            merchant="Paged subscription",
        )
        for _ in range(3)
    ]
    damaged = min(transaction_ids)
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")
    monkeypatch.setattr(repository, "_TRANSACTION_AMOUNT_PAGE_SIZE", 2)

    reconstructed = finance_service.reconstruct_net_worth(
        demo_engine, HH, today - timedelta(days=1), "USD"
    )
    subscription = finance_service.subscription_forecast(
        demo_engine, HH, "USD", today=today
    )
    savings = savings_detection.qualified_detect_for_household(
        demo_engine, HH, today=today
    )

    assert reconstructed.incomplete_count == 1
    assert subscription.total is None
    assert subscription.detection.incomplete_count == 1
    assert savings.incomplete_count == 1


def test_foreign_liability_corruption_does_not_taint_base_debt_history(
    _master_key, demo_engine
) -> None:
    liability = repository.create_account(
        demo_engine, HH, "EUR loan", "auto_loan", "EUR"
    )
    damaged = repository.create_transaction(
        demo_engine,
        household_id=HH,
        account_id=liability.id,
        occurred_at=date.today(),
        amount_minor=-5_000,
        currency="EUR",
        merchant="Loan payment",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    reconstructed = finance_service.reconstruct_debt_total(
        demo_engine, HH, date.today() - timedelta(days=1), "USD"
    )

    assert reconstructed.is_complete


@pytest.mark.anyio
async def test_household_keeps_siblings_and_qualifies_transaction_leaves(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    category = repository.create_category(demo_engine, HH, "Damaged current spend")
    damaged = _transaction(
        demo_engine,
        amount_minor=-12_345,
        occurred_at=date.today(),
        category_id=category.id,
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    response = await demo_client.get("/api/v1/household", headers=_headers(demo_token))

    assert response.status_code == 200, response.text
    body = response.json()
    cash = body["monthly_cash_flow"]
    assert cash["spending"]["incomplete_count"] == 1
    assert cash["net"] is None
    assert body["net_worth"]["incomplete_count"] == 0
    assert body["asset_breakdown"]
    assert body["upcoming_bills"]
    category_row = next(
        item for item in body["spending_by_category"]["categories"]
        if item["category_id"] == category.id
    )
    assert category_row["amount"] == {
        "value": {"amount_minor": 0, "currency": "USD"},
        "incomplete_count": 1,
    }
    assert body["spending_by_category"]["total"]["incomplete_count"] == 1

    advisor = ai_tools.build_executor(demo_engine, HH, "USD")
    categories = advisor("get_spending_by_category", {})
    damaged_category = next(
        item for item in categories["categories"]
        if item["category"] == "Damaged current spend"
    )
    assert damaged_category["spent"]["incomplete_count"] == 1
    assert damaged_category["spent"]["partial"] is True
    assert categories["total"]["incomplete_count"] == 1
    insights = advisor("get_spending_insights", {})
    assert insights["month_to_date_spending"]["incomplete_count"] == 1
    assert insights["change_percent"] is None
    assert insights["top_merchants"] is None


@pytest.mark.anyio
async def test_current_month_unreadable_uncategorized_income_qualifies_cash_flow_without_changing_normalization(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    normalized = finance_service.monthly_income_total(demo_engine, HH, "USD")
    checking = repository.create_account(
        demo_engine,
        HH,
        name="Current payroll checking",
        account_type="checking",
        currency="USD",
    )
    readable = _transaction(
        demo_engine,
        amount_minor=12_345,
        occurred_at=date.today(),
        merchant="Readable current payroll",
        account_id=checking.id,
    )
    damaged = _transaction(
        demo_engine,
        amount_minor=98_765,
        occurred_at=date.today(),
        merchant="Unreadable current payroll",
        account_id=checking.id,
    )
    assert repository.set_income_override(demo_engine, HH, readable, "include")
    assert repository.set_income_override(demo_engine, HH, damaged, "include")
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    # The shared normalized-income service intentionally uses complete trailing
    # months and must retain both its value and completeness semantics.
    after_damage = finance_service.monthly_income_total(demo_engine, HH, "USD")
    assert after_damage == normalized
    received = finance_service.income_received_between(
        demo_engine, HH, "USD", date.today().replace(day=1), date.today()
    )
    assert received.value >= 12_345
    assert received.incomplete_count == 1

    response = await demo_client.get("/api/v1/household", headers=_headers(demo_token))

    assert response.status_code == 200, response.text
    cash = response.json()["monthly_cash_flow"]
    assert cash["income"] == {
        # Current readable paychecks qualify the leaf but are not added to the
        # trailing complete-month normalization a second time.
        "value": {"amount_minor": normalized.value.amount_minor, "currency": "USD"},
        "incomplete_count": 1,
    }
    assert cash["net"] is None


def test_two_unreadable_cells_count_twice(_master_key, demo_engine) -> None:
    damaged_ids = [
        _transaction(
            demo_engine,
            amount_minor=-amount,
            occurred_at=date.today(),
        )
        for amount in (1_111, 2_222)
    ]
    for damaged_id in damaged_ids:
        _damage_cell(demo_engine, "transactions", damaged_id, "amount_minor")

    result = repository.sum_spending(
        demo_engine, HH, date.today().replace(day=1), date.today(), "USD"
    )

    assert result.incomplete_count == 2


@pytest.mark.anyio
async def test_emergency_and_savings_decisions_are_unavailable(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    previous_month = date.today().replace(day=1) - timedelta(days=1)
    damaged = _transaction(
        demo_engine,
        amount_minor=-45_000,
        occurred_at=previous_month,
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    response = await demo_client.get("/api/v1/household", headers=_headers(demo_token))

    assert response.status_code == 200, response.text
    body = response.json()
    emergency = body["emergency_fund"]
    assert emergency["monthly_expenses"]["incomplete_count"] == 1
    assert emergency["months"] is None
    assert emergency["gap_to_recommended"] is None
    assert emergency["status"] == "unavailable"
    savings = body["savings_rate"]
    assert savings["average_monthly_spending"]["incomplete_count"] == 1
    assert savings["percent"] is None
    assert savings["gross_income"] is None
    assert savings["residual"] is None
    assert savings["total_saved"] is None

    advisor = ai_tools.build_executor(demo_engine, HH, "USD")
    emergency_tool = advisor("get_emergency_fund", {})
    monthly = emergency_tool["outputs"]["monthly_essential_expenses"]
    assert monthly["incomplete_count"] == 1
    assert monthly["partial"] is True
    assert emergency_tool["outputs"]["emergency_fund_months"] is None


@pytest.mark.anyio
async def test_budget_mutations_succeed_and_server_summary_is_unavailable(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    headers = _headers(demo_token)
    category = repository.create_category(demo_engine, HH, "Damaged envelope")
    damaged = _transaction(
        demo_engine,
        amount_minor=-9_000,
        occurred_at=date.today(),
        category_id=category.id,
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    created = await demo_client.post(
        "/api/v1/budgets",
        headers=headers,
        json={
            "category_id": category.id,
            "limit": {"amount_minor": 10_000, "currency": "USD"},
        },
    )
    assert created.status_code == 201, created.text
    budget = created.json()
    assert budget["spent"]["value"]["amount_minor"] == 0
    assert budget["spent"]["incomplete_count"] == 1
    assert budget["remaining"] is None
    assert budget["percent_used"] is None
    assert budget["status"] is None

    updated = await demo_client.patch(
        f"/api/v1/budgets/{budget['id']}",
        headers=headers,
        json={"limit": {"amount_minor": 11_000, "currency": "USD"}},
    )
    assert updated.status_code == 200, updated.text
    listed = await demo_client.get("/api/v1/budgets", headers=headers)
    assert listed.status_code == 200
    summary = listed.json()["summary"]
    assert summary["total_spent"]["incomplete_count"] == 1
    assert summary["over_count"] is None
    assert summary["warning_count"] is None

    advisor = ai_tools.build_executor(demo_engine, HH, "USD")
    budget_tool = advisor("get_budgets", {})
    envelope = next(
        item for item in budget_tool["month_budgets"]
        if item["category"] == "Damaged envelope"
    )
    assert envelope["spent_so_far"]["incomplete_count"] == 1
    assert envelope["remaining"] is None
    assert envelope["percent_used"] is None
    assert envelope["status"] is None
    assert budget_tool["summary"]["total_spent"]["incomplete_count"] == 1
    assert budget_tool["summary"]["over_count"] is None


@pytest.mark.anyio
async def test_detection_instability_suppresses_outlook_and_plan_decisions(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    damaged = _transaction(
        demo_engine,
        amount_minor=250_000,
        occurred_at=date.today(),
        merchant="ACME PAYROLL",
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")
    headers = _headers(demo_token)

    outlook_response = await demo_client.get(
        "/api/v1/overview/cash-outlook", headers=headers
    )
    plan_response = await demo_client.get(
        "/api/v1/overview/spending-plan", headers=headers
    )

    assert outlook_response.status_code == 200, outlook_response.text
    outlook = outlook_response.json()
    assert outlook["income_projection"] == {
        "status": "unavailable",
        "incomplete_count": 1,
    }
    assert outlook["expected_income"] is None
    assert outlook["ending_cash"] is None
    assert outlook["lowest_balance"] is None
    assert outlook["shortfall"] is None
    assert outlook["sell_by_date"] is None

    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["income_received"]["incomplete_count"] == 1
    assert plan["income_projected"] is None
    assert plan["expected_income"] is None
    assert plan["left_to_spend"] is None
    assert plan["per_day"] is None


@pytest.mark.anyio
async def test_income_and_yearly_overview_are_partial_and_review_stays_strict(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    headers = _headers(demo_token)
    generated = await demo_client.post("/api/v1/overview/yearly/review", headers=headers)
    assert generated.status_code == 200, generated.text
    cached_summary = generated.json()["summary"]

    category = next(
        (
            category
            for category in repository.list_categories(demo_engine, HH)
            if category.name.lower() == "income"
        ),
        None,
    ) or repository.create_category(demo_engine, HH, "Income")
    damaged = _transaction(
        demo_engine,
        amount_minor=321_000,
        occurred_at=date.today(),
        category_id=category.id,
        merchant="Damaged payroll",
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    income_response = await demo_client.get("/api/v1/income/analysis", headers=headers)
    assert income_response.status_code == 200, income_response.text
    income = income_response.json()
    assert income["detection"] == {"status": "unavailable", "incomplete_count": 1}
    assert income["rollup"]["annual_income"]["incomplete_count"] >= 1
    assert income["rollup"]["transaction_count"] is None
    assert income["tax"] is None

    advisor = ai_tools.build_executor(demo_engine, HH, "USD")
    income_tool = advisor("get_income_and_tax", {})
    assert income_tool["detection"] == {
        "status": "unavailable",
        "incomplete_count": 1,
    }
    assert income_tool["annual_income_detected"]["incomplete_count"] == 1
    assert income_tool["tax_estimate"] is None
    assert income_tool["take_home"] is None

    yearly_response = await demo_client.get("/api/v1/overview/yearly", headers=headers)
    assert yearly_response.status_code == 200, yearly_response.text
    yearly = yearly_response.json()
    current_month = date.today().strftime("%Y-%m")
    month = next(item for item in yearly["months"] if item["month"] == current_month)
    assert month["income"]["incomplete_count"] == 1
    assert month["net"] is None
    assert yearly["total_income"]["incomplete_count"] == 1
    assert yearly["total_net"] is None
    # Its Income category is a non-spending predicate, so spending ranking stays stable.
    assert yearly["top_categories"] is not None
    assert yearly["review"] is None

    refused = await demo_client.post("/api/v1/overview/yearly/review", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "sealed_amount_unreadable"
    cached = repository.get_yearly_review(demo_engine, HH, date.today().year)
    assert cached is not None
    assert cached.summary == cached_summary


def test_undated_liability_payment_uncertainty_qualifies_timeline_and_outlook(
    _master_key, demo_engine, monkeypatch
) -> None:
    today = date.today()
    loan = repository.create_account(
        demo_engine,
        HH,
        name="Undated qualification loan",
        account_type="student_loan",
        currency="USD",
        minimum_payment_minor=31_415,
    )
    repository.record_account_balance(demo_engine, loan.id, -500_000)
    payment = repository.create_transaction(
        demo_engine,
        household_id=HH,
        account_id=loan.id,
        occurred_at=today - timedelta(days=3),
        amount_minor=31_415,
        currency="USD",
        merchant="Loan payment",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    _damage_cell(demo_engine, "transactions", payment, "amount_minor")
    monkeypatch.setattr(repository, "_TRANSACTION_AMOUNT_PAGE_SIZE", 2)

    timeline = finance_service.payment_timeline(demo_engine, HH, "USD", today=today)
    item = next(row for row in timeline.items if row.id == loan.id)
    assert item.due_date is None
    assert item.status == "unknown"
    assert len(item.incomplete_sources) == 1
    assert timeline.due_total.incomplete_count == 1
    assert timeline.covered is None

    outlook = finance_service.cash_outlook(demo_engine, HH, "USD", today=today)
    assert outlook.obligations.incomplete_count == 1
    assert outlook.ending_cash_minor is None
    assert outlook.lowest_minor is None


def test_newest_statement_is_selected_before_decode_and_minimum_is_irrelevant(
    _master_key, demo_engine
) -> None:
    today = date.today()
    card_id, older_id, newest_id = _card_with_statements(demo_engine, today=today)
    _damage_cell(demo_engine, "card_statements", newest_id, "statement_balance_minor")

    candidates = repository.list_authoritative_statement_balance_candidates(
        demo_engine,
        HH,
        currency="USD",
        due_on_or_before=today + timedelta(days=30),
    )
    selected = next(c for c in candidates if c.metadata.account_id == card_id)
    assert selected.metadata.id == newest_id
    assert selected.amount is None
    assert selected.incomplete_source is not None
    assert selected.incomplete_source.row_id == newest_id
    assert selected.metadata.id != older_id

    timeline = finance_service.payment_timeline(demo_engine, HH, "USD", today=today)
    item = next(i for i in timeline.items if i.id == card_id)
    assert item.amount_minor is None
    assert item.status == "unknown"
    assert item.source == "statement"
    assert timeline.due_total.incomplete_count == 1


@pytest.mark.anyio
async def test_corrupt_statement_balance_keeps_endpoint_partial_and_omits_drilldown(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    today = date.today()
    card_id, _older_id, newest_id = _card_with_statements(demo_engine, today=today)
    _damage_cell(demo_engine, "card_statements", newest_id, "statement_balance_minor")
    headers = _headers(demo_token)
    enabled = await demo_client.patch(
        "/api/v1/household",
        headers=headers,
        json={"credit_cards_paid_in_full": True},
    )
    assert enabled.status_code == 200, enabled.text

    household_response = await demo_client.get("/api/v1/household", headers=headers)
    timeline_response = await demo_client.get("/api/v1/bills/timeline", headers=headers)

    assert household_response.status_code == 200, household_response.text
    safe = household_response.json()["safe_to_spend"]
    assert safe["credit_card_payments"]["incomplete_count"] == 1
    assert safe["committed_total"] is None
    assert safe["safe_to_spend"] is None
    assert all(item["name"] != "Qualification Visa" for item in safe["credit_card_items"])

    assert timeline_response.status_code == 200, timeline_response.text
    timeline = timeline_response.json()
    item = next(row for row in timeline["items"] if row["id"] == card_id)
    assert item["amount"] is None
    assert item["status"] == "unknown"
    assert timeline["due_total"]["incomplete_count"] == 1
    assert any(row["id"] != card_id for row in timeline["items"])


@pytest.mark.anyio
async def test_corrupt_statement_minimum_keeps_aggregates_exact_and_strict_routes_409(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    today = date.today()
    card_id, _older_id, newest_id = _card_with_statements(demo_engine, today=today)
    _damage_cell(demo_engine, "card_statements", newest_id, "minimum_due_minor")

    candidates = repository.list_authoritative_statement_balance_candidates(
        demo_engine,
        HH,
        currency="USD",
        due_on_or_before=today + timedelta(days=30),
    )
    selected = next(c for c in candidates if c.metadata.account_id == card_id)
    assert selected.amount == 44_000
    assert selected.incomplete_source is None

    headers = _headers(demo_token)
    requests = [
        await demo_client.get("/api/v1/accounts/card-statements", headers=headers),
        await demo_client.post(
            f"/api/v1/accounts/card-statements/{newest_id}/paid",
            headers=headers,
            json={"paid_at": today.isoformat()},
        ),
        await demo_client.put(
            f"/api/v1/accounts/card-statements/{newest_id}/lines",
            headers=headers,
            json={"lines": []},
        ),
        await demo_client.get(
            f"/api/v1/accounts/card-statements/{newest_id}/reconciliation",
            headers=headers,
        ),
        await demo_client.delete(
            f"/api/v1/accounts/card-statements/{newest_id}", headers=headers
        ),
    ]
    assert [response.status_code for response in requests] == [409] * len(requests)


def test_corrupt_statement_minimum_keeps_aggregate_exact_but_raw_read_strict(
    _master_key, demo_engine
) -> None:
    today = date.today()
    card_id, _older_id, newest_id = _card_with_statements(demo_engine, today=today)
    _damage_cell(demo_engine, "card_statements", newest_id, "minimum_due_minor")

    candidates = repository.list_authoritative_statement_balance_candidates(
        demo_engine,
        HH,
        currency="USD",
        due_on_or_before=today + timedelta(days=30),
    )
    selected = next(c for c in candidates if c.metadata.account_id == card_id)
    assert selected.amount == 44_000
    assert selected.incomplete_source is None
    with pytest.raises(household_crypto.SealedAmountUnreadableError):
        repository.list_card_statements(demo_engine, HH, account_id=card_id)


@pytest.mark.anyio
async def test_record_statement_strict_preread_returns_409(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    today = date.today()
    card_id, _older_id, newest_id = _card_with_statements(demo_engine, today=today)
    _damage_cell(demo_engine, "card_statements", newest_id, "minimum_due_minor")

    response = await demo_client.post(
        "/api/v1/accounts/card-statements",
        headers=_headers(demo_token),
        json={
            "account_id": card_id,
            "statement_balance": {"amount_minor": 55_000, "currency": "USD"},
            "minimum_due": {"amount_minor": 5_500, "currency": "USD"},
            "due_date": (today + timedelta(days=5)).isoformat(),
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "sealed_amount_unreadable"


@pytest.mark.anyio
async def test_bill_suggestions_strict_reader_returns_409(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    checking = next(
        account
        for account in repository.list_account_balances(demo_engine, HH)
        if account.account_type == "checking" and account.currency == "USD"
    )
    damaged = repository.create_transaction(
        demo_engine,
        household_id=HH,
        account_id=checking.account_id,
        occurred_at=date.today(),
        amount_minor=-7_777,
        currency="USD",
        merchant="Strict recurring candidate",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    response = await demo_client.get(
        "/api/v1/bills/suggestions", headers=_headers(demo_token)
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "sealed_amount_unreadable"


@pytest.mark.anyio
async def test_generate_report_strict_reader_returns_409(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    damaged = _transaction(
        demo_engine,
        amount_minor=-8_888,
        occurred_at=date.today() - timedelta(days=1),
        merchant="Strict report input",
    )
    _damage_cell(demo_engine, "transactions", damaged, "amount_minor")

    response = await demo_client.post(
        "/api/v1/reports/generate",
        headers=_headers(demo_token),
        json={"report_type": "weekly"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "sealed_amount_unreadable"


@pytest.mark.anyio
async def test_income_monthly_average_uses_base_rollup_sources_only(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    account = repository.create_account(
        demo_engine, HH, "Euro Checking", "checking", "EUR"
    )
    transaction = repository.create_transaction(
        demo_engine,
        household_id=HH,
        account_id=account.id,
        occurred_at=date.today() - timedelta(days=14),
        amount_minor=250_000,
        currency="EUR",
        merchant="Foreign payroll",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
    )
    _damage_cell(demo_engine, "transactions", transaction, "amount_minor")

    response = await demo_client.get(
        "/api/v1/income/analysis", headers=_headers(demo_token)
    )

    assert response.status_code == 200, response.text
    rollup = response.json()["rollup"]
    assert rollup["annual_income"]["incomplete_count"] == 0
    assert rollup["monthly_average"]["incomplete_count"] == 0


@pytest.mark.anyio
async def test_unavailable_subscription_detection_suppresses_household_forecast(
    demo_engine, demo_client, demo_token, monkeypatch
) -> None:
    source = UnreadableAmountSource(HH, "transactions", "private-row", "amount_minor")
    forecast = finance_service.SubscriptionForecast(
        items=[],
        total=Qualified.complete(Money(12_345, "USD")),
        detection=Qualified(None, frozenset({source})),
    )
    monkeypatch.setattr(
        finance_service, "subscription_forecast", lambda *_args, **_kwargs: forecast
    )

    attempt, _ = finance_service.compute_safe_to_spend(demo_engine, HH, "USD")

    assert attempt.response.subscription_detection.incomplete_count == 1
    assert attempt.response.subscription_forecast is None
    assert attempt.outputs["subscription_forecast"] is None

    response = await demo_client.get(
        "/api/v1/household", headers=_headers(demo_token)
    )
    assert response.status_code == 200, response.text
    safe_to_spend = response.json()["safe_to_spend"]
    assert safe_to_spend["subscription_forecast"] is None
    assert safe_to_spend["subscription_detection"] == {
        "status": "unavailable",
        "incomplete_count": 1,
    }


@pytest.mark.anyio
async def test_safe_to_spend_details_share_one_projection_and_household_date(
    demo_engine, demo_client, demo_token, monkeypatch
) -> None:
    boundary = date(2026, 1, 31)
    repository.set_credit_cards_paid_in_full(demo_engine, HH, True)
    _card_with_statements(demo_engine, today=boundary)
    monkeypatch.setattr(
        household_clock,
        "today_for_household",
        lambda *_args, **_kwargs: boundary,
    )

    statement_calls: list[date] = []
    subscription_calls: list[date] = []
    savings_calls: list[date] = []
    original_statements = repository.list_authoritative_statement_balance_candidates

    def statements(*args, **kwargs):
        statement_calls.append(kwargs["due_on_or_before"])
        return original_statements(*args, **kwargs)

    def subscriptions(*_args, today, **_kwargs):
        subscription_calls.append(today)
        return finance_service.SubscriptionForecast(
            items=[
                finance_service.SubscriptionForecastItem(
                    name="Boundary streaming",
                    amount_minor=4_321,
                    currency="USD",
                    next_charge=today + timedelta(days=1),
                )
            ],
            total=Qualified.complete(Money(4_321, "USD")),
            detection=Qualified.complete(None),
        )

    def savings(*_args, today, **_kwargs):
        savings_calls.append(today)
        return finance_service.CommittedSavingsForecast(
            total=Qualified.complete(Money(7_654, "USD")),
            items=[
                (
                    "Boundary 529",
                    Money(7_654, "USD"),
                    today + timedelta(days=2),
                )
            ],
            detection=Qualified.complete(None),
        )

    monkeypatch.setattr(
        repository,
        "list_authoritative_statement_balance_candidates",
        statements,
    )
    monkeypatch.setattr(finance_service, "subscription_forecast", subscriptions)
    monkeypatch.setattr(finance_service, "committed_savings_in_window", savings)

    response = await demo_client.get(
        "/api/v1/household", headers=_headers(demo_token)
    )

    assert response.status_code == 200, response.text
    safe = response.json()["safe_to_spend"]
    assert statement_calls == [
        boundary + timedelta(days=finance_service.SAFE_TO_SPEND_HORIZON_DAYS)
    ]
    assert subscription_calls == [boundary]
    assert savings_calls == [boundary]
    assert sum(item["amount"]["amount_minor"] for item in safe["credit_card_items"]) == (
        safe["credit_card_payments"]["value"]["amount_minor"]
    )
    assert sum(
        item["amount"]["amount_minor"]
        for item in safe["subscription_forecast_items"]
    ) == safe["subscription_forecast"]["value"]["amount_minor"]
    assert sum(
        item["amount"]["amount_minor"]
        for item in safe["committed_savings_items"]
    ) == safe["committed_savings"]["value"]["amount_minor"]


def test_purchase_impact_refuses_incomplete_expense_dependency(
    _master_key, demo_engine, monkeypatch
) -> None:
    transaction = _transaction(
        demo_engine,
        amount_minor=-12_345,
        occurred_at=date.today().replace(day=1) - timedelta(days=10),
    )
    _damage_cell(demo_engine, "transactions", transaction, "amount_minor")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("purchase calculator must not run")

    monkeypatch.setattr(finance_service, "calculate_purchase_impact", forbidden)
    with pytest.raises(household_crypto.SealedAmountUnreadableError):
        finance_service.compute_purchase_impact(
            demo_engine, HH, "USD", Money(10_000, "USD")
        )


def test_incomplete_safe_to_spend_skips_engine_and_persists_one_private_attempt(
    _master_key, demo_engine, monkeypatch, caplog
) -> None:
    today = date.today()
    _card_id, _older_id, newest_id = _card_with_statements(demo_engine, today=today)
    ciphertext = _damage_cell(
        demo_engine, "card_statements", newest_id, "statement_balance_minor"
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("financial engine must not run on incomplete inputs")

    monkeypatch.setattr(finance_service, "calculate_safe_to_spend", forbidden)
    before = 0
    with demo_engine.connect() as conn:
        before = conn.execute(
            select(func.count()).select_from(models.financial_calculations)
        ).scalar_one()

    attempt, calculation_id = finance_service.compute_safe_to_spend(
        demo_engine, HH, "USD", today=today
    )

    assert attempt.engine_invoked is False
    assert attempt.version == finance_service.QUALIFIED_ATTEMPT_VERSION
    assert attempt.response.safe_to_spend is None
    assert attempt.response.committed_total is None
    assert attempt.response.credit_card_payments.incomplete_count == 1
    with demo_engine.connect() as conn:
        rows = conn.execute(
            select(models.financial_calculations).where(
                models.financial_calculations.c.id == calculation_id
            )
        ).mappings().all()
        after = conn.execute(
            select(func.count()).select_from(models.financial_calculations)
        ).scalar_one()
    assert after == before + 1
    assert len(rows) == 1
    row = rows[0]
    assert row["version"] == finance_service.QUALIFIED_ATTEMPT_VERSION
    assert row["inputs_json"]["engine_invoked"] is False
    assert row["inputs_json"]["incomplete_amount_count"] == 1
    assert row["outputs_json"]["credit_card_payments"]["incomplete_count"] == 1

    persisted = json.dumps(
        {
            "inputs": row["inputs_json"],
            "outputs": row["outputs_json"],
            "warnings": row["warnings_json"],
        },
        sort_keys=True,
    )
    assert newest_id not in persisted
    assert "card_statements" not in persisted
    assert "statement_balance_minor" not in persisted
    assert ciphertext not in persisted
    assert newest_id not in caplog.text
    assert "card_statements" not in caplog.text

    tool = ai_tools.build_executor(demo_engine, HH, "USD")
    payload = tool("get_safe_to_spend", {})
    assert payload["error"] == "incomplete_data"
    assert payload["incomplete_count"] == 1
    assert payload["outputs"]["safe_to_spend"] is None
    assert payload["outputs"]["committed_total"] is None
    credit_cards = payload["outputs"]["credit_card_payments"]
    assert credit_cards["incomplete_count"] == 1
    assert credit_cards["partial"] is True
    assert "row_id" not in json.dumps(payload)
    assert "card_statements" not in json.dumps(payload)
