"""M46: budget envelopes — monthly per-category limits with threshold status."""

from datetime import date, timedelta

import pytest

from family_cfo_api import fixtures, repository

_HH = fixtures.DEMO_HOUSEHOLD_ID


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
    assert (await status())["spent"]["amount_minor"] == 0

    _spend(demo_engine, category_id, today, -8_000)  # 80% -> warning
    entry = await status()
    assert entry["status"] == "warning"
    assert entry["percent_used"] == 80
    assert entry["remaining"]["amount_minor"] == 2_000

    _spend(demo_engine, category_id, today, -4_000)  # 120% -> over
    entry = await status()
    assert entry["status"] == "over"
    assert entry["percent_used"] == 120
    assert entry["remaining"]["amount_minor"] == -2_000


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
    assert summary["total_spent"]["amount_minor"] == 6_000


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
