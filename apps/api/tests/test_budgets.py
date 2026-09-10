"""M46: budget envelopes — monthly per-category limits with threshold status."""

from datetime import date, timedelta

import pytest

from family_cfo_api import fixtures, repository
from family_cfo_api.api import budgets as budgets_api
from family_cfo_api.api import household as household_api
from family_cfo_api.qualified_amounts import (
    CategorySpendingTotals,
    Qualified,
    UnreadableAmountSource,
)

_HH = fixtures.DEMO_HOUSEHOLD_ID


def _qminor(value: dict) -> int:
    assert value["incomplete_count"] == 0
    return value["value"]["amount_minor"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_category(demo_client, headers, name: str) -> str:
    created = await demo_client.post("/api/v1/categories", headers=headers, json={"name": name})
    assert created.status_code == 201
    return created.json()["id"]


def _spend(demo_engine, category_id: str, occurred: date, amount_minor: int) -> None:
    account_id = repository.list_account_balances(demo_engine, _HH)[0].account_id
    repository.create_transaction(
        demo_engine,
        household_id=_HH,
        account_id=account_id,
        occurred_at=occurred,
        amount_minor=amount_minor,
        currency="USD",
        merchant="Store",
        description=None,
        import_source=None,
        import_id=None,
        review_state="reviewed",
        category_id=category_id,
    )


@pytest.mark.anyio
async def test_budget_crud_and_one_per_category(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    category_id = await _make_category(demo_client, headers, "Dining")

    created = await demo_client.post(
        "/api/v1/budgets",
        headers=headers,
        json={"category_id": category_id, "limit": {"amount_minor": 50_000, "currency": "USD"}},
    )
    assert created.status_code == 201
    budget = created.json()
    assert budget["category_name"] == "Dining"
    assert budget["limit"]["amount_minor"] == 50_000
    assert budget["status"] == "under"

    # One envelope per category.
    dupe = await demo_client.post(
        "/api/v1/budgets",
        headers=headers,
        json={"category_id": category_id, "limit": {"amount_minor": 1_000, "currency": "USD"}},
    )
    assert dupe.status_code == 409

    # Change the limit.
    updated = await demo_client.patch(
        f"/api/v1/budgets/{budget['id']}",
        headers=headers,
        json={"limit": {"amount_minor": 80_000, "currency": "USD"}},
    )
    assert updated.json()["limit"]["amount_minor"] == 80_000

    deleted = await demo_client.delete(f"/api/v1/budgets/{budget['id']}", headers=headers)
    assert deleted.status_code == 204


@pytest.mark.anyio
async def test_status_thresholds_track_current_month_spend(
    demo_client, demo_token, demo_engine
) -> None:
    headers = _headers(demo_token)
    category_id = await _make_category(demo_client, headers, "Groceries3")
    await demo_client.post(
        "/api/v1/budgets",
        headers=headers,
        json={"category_id": category_id, "limit": {"amount_minor": 10_000, "currency": "USD"}},
    )
    today = date.today()

    # Last month's spend must not count against this month's envelope.
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    _spend(demo_engine, category_id, last_month, -9_999)

    async def status() -> dict:
        budgets = (await demo_client.get("/api/v1/budgets", headers=headers)).json()["budgets"]
        return next(b for b in budgets if b["category_id"] == category_id)

    assert (await status())["status"] == "under"
    assert _qminor((await status())["spent"]) == 0

    _spend(demo_engine, category_id, today, -8_000)  # 80% -> warning
    entry = await status()
    assert entry["status"] == "warning"
    assert entry["percent_used"] == 80
    assert _qminor(entry["remaining"]) == 2_000

    _spend(demo_engine, category_id, today, -4_000)  # 120% -> over
    entry = await status()
    assert entry["status"] == "over"
    assert entry["percent_used"] == 120
    assert _qminor(entry["remaining"]) == -2_000


@pytest.mark.anyio
async def test_budget_summary_on_household_context(demo_client, demo_token, demo_engine) -> None:
    headers = _headers(demo_token)

    # No budgets -> summary absent.
    context = (await demo_client.get("/api/v1/household", headers=headers)).json()
    assert context["budget_summary"] is None

    over_cat = await _make_category(demo_client, headers, "OverCat")
    ok_cat = await _make_category(demo_client, headers, "OkCat")
    for cid, limit in ((over_cat, 5_000), (ok_cat, 100_000)):
        await demo_client.post(
            "/api/v1/budgets",
            headers=headers,
            json={"category_id": cid, "limit": {"amount_minor": limit, "currency": "USD"}},
        )
    _spend(demo_engine, over_cat, date.today(), -6_000)

    summary = (await demo_client.get("/api/v1/household", headers=headers)).json()[
        "budget_summary"
    ]
    assert summary["envelope_count"] == 2
    assert summary["over_count"] == 1
    assert summary["warning_count"] == 0
    assert summary["total_budgeted"]["amount_minor"] == 105_000
    assert _qminor(summary["total_spent"]) == 6_000


def test_empty_budget_response_remains_exact_despite_unrelated_spending(
    demo_engine, monkeypatch
) -> None:
    source = UnreadableAmountSource(_HH, "transactions", "unrelated", "amount_minor")
    partial = Qualified(9_999, frozenset({source}))
    totals = CategorySpendingTotals(
        by_category={"unselected": partial},
        categorized_total=partial,
        uncategorized=Qualified.complete(0),
        overall=partial,
    )
    monkeypatch.setattr(
        repository, "category_spending_totals", lambda *_args, **_kwargs: totals
    )

    response = budgets_api.assemble_budget_response(demo_engine, _HH, "USD")

    assert response.budgets == []
    assert response.summary.envelope_count == 0
    assert response.summary.total_spent.value.amount_minor == 0
    assert response.summary.total_spent.incomplete_count == 0


def test_budget_list_and_household_summary_share_one_spending_snapshot(
    demo_engine, monkeypatch
) -> None:
    first_category = repository.create_category(demo_engine, _HH, "Snapshot A")
    second_category = repository.create_category(demo_engine, _HH, "Snapshot B")
    repository.create_budget(demo_engine, _HH, first_category.id, 5_000, "USD")
    repository.create_budget(demo_engine, _HH, second_category.id, 10_000, "USD")
    snapshots = [
        CategorySpendingTotals(
            by_category={
                first_category.id: Qualified.complete(6_000),
                second_category.id: Qualified.complete(1_000),
            },
            categorized_total=Qualified.complete(7_000),
            uncategorized=Qualified.complete(0),
            overall=Qualified.complete(7_000),
        ),
        CategorySpendingTotals(
            by_category={},
            categorized_total=Qualified.complete(0),
            uncategorized=Qualified.complete(0),
            overall=Qualified.complete(0),
        ),
    ]
    calls: list[int] = []

    def alternating(*_args, **_kwargs):
        calls.append(1)
        return snapshots[min(len(calls) - 1, 1)]

    monkeypatch.setattr(repository, "category_spending_totals", alternating)

    response = budgets_api.assemble_budget_response(demo_engine, _HH, "USD")

    assert len(calls) == 1
    assert [budget.spent.value.amount_minor for budget in response.budgets] == [6_000, 1_000]
    assert response.summary.total_spent.value.amount_minor == 7_000
    assert response.summary.over_count == 1

    calls.clear()
    summary = household_api._budget_summary(demo_engine, _HH, "USD")

    assert len(calls) == 1
    assert summary is not None
    assert summary.total_spent.value.amount_minor == 7_000
    assert summary.over_count == 1


@pytest.mark.anyio
async def test_unknown_category_404_and_nonpositive_limit_400(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    missing = await demo_client.post(
        "/api/v1/budgets",
        headers=headers,
        json={"category_id": "nope", "limit": {"amount_minor": 1_000, "currency": "USD"}},
    )
    assert missing.status_code == 404

    category_id = await _make_category(demo_client, headers, "ZeroCat")
    zero = await demo_client.post(
        "/api/v1/budgets",
        headers=headers,
        json={"category_id": category_id, "limit": {"amount_minor": 0, "currency": "USD"}},
    )
    assert zero.status_code == 400


@pytest.mark.anyio
async def test_deleting_category_removes_its_budget(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    category_id = await _make_category(demo_client, headers, "Doomed")
    await demo_client.post(
        "/api/v1/budgets",
        headers=headers,
        json={"category_id": category_id, "limit": {"amount_minor": 2_000, "currency": "USD"}},
    )
    assert (
        await demo_client.delete(f"/api/v1/categories/{category_id}", headers=headers)
    ).status_code == 204
    budgets = (await demo_client.get("/api/v1/budgets", headers=headers)).json()["budgets"]
    assert all(b["category_id"] != category_id for b in budgets)


@pytest.mark.anyio
async def test_viewer_cannot_create_budget(demo_client, demo_token, demo_viewer_token) -> None:
    category_id = await _make_category(demo_client, _headers(demo_token), "ViewerCat")
    response = await demo_client.post(
        "/api/v1/budgets",
        headers=_headers(demo_viewer_token),
        json={"category_id": category_id, "limit": {"amount_minor": 1_000, "currency": "USD"}},
    )
    assert response.status_code == 403
