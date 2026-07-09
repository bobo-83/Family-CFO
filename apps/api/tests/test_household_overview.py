"""M38: enriched household context (emergency-fund target, cash flow, assets, debt)."""

import pytest


async def _context(demo_client, demo_token):
    response = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_context_includes_enriched_summary(demo_client, demo_token) -> None:
    body = await _context(demo_client, demo_token)

    # Cash flow from the demo fixtures: $6,000 income − ($2,000 + $80) bills.
    cash_flow = body["monthly_cash_flow"]
    assert cash_flow["income"]["amount_minor"] == 600_000
    assert cash_flow["bills"]["amount_minor"] == 208_000
    assert cash_flow["net"]["amount_minor"] == 392_000

    # Emergency fund: no designations in fixtures → all-liquid fallback,
    # and the reported months must equal reserved / monthly expenses.
    fund = body["emergency_fund"]
    assert fund["using_designations"] is False
    assert fund["monthly_expenses"]["amount_minor"] == 208_000
    assert fund["target_months_min"] == 3
    assert fund["target_months_recommended"] == 6
    assert fund["months"] == pytest.approx(fund["reserved"]["amount_minor"] / 208_000)
    assert fund["gap_to_recommended"]["amount_minor"] == max(
        0, 6 * 208_000 - fund["reserved"]["amount_minor"]
    )
    assert fund["status"] in {"no_fund", "getting_started", "on_track", "fully_funded"}

    # Assets are grouped in spendability order; debt is a positive total.
    categories = [entry["category"] for entry in body["asset_breakdown"]]
    order = ["liquid", "investments", "retirement", "education", "property"]
    assert categories == [c for c in order if c in categories]
    assert body["total_debt"]["amount_minor"] >= 0


@pytest.mark.anyio
async def test_designation_drives_status_and_gap(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    # A dedicated savings account holding exactly one month of expenses.
    account_id = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "EF Savings", "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": 208_000, "currency": "USD"}},
    )
    await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_amount": {"amount_minor": 208_000, "currency": "USD"}},
    )

    fund = (await _context(demo_client, demo_token))["emergency_fund"]
    assert fund["using_designations"] is True
    assert fund["reserved"]["amount_minor"] == 208_000
    assert fund["months"] == pytest.approx(1.0)
    assert fund["status"] == "getting_started"
    assert fund["gap_to_recommended"]["amount_minor"] == 5 * 208_000


@pytest.mark.anyio
async def test_fully_funded_has_zero_gap(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    account_id = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Big EF", "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": 2_000_000, "currency": "USD"}},
    )
    await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_percent": 100},
    )

    fund = (await _context(demo_client, demo_token))["emergency_fund"]
    assert fund["status"] == "fully_funded"
    assert fund["gap_to_recommended"]["amount_minor"] == 0
    assert fund["months"] >= 6


@pytest.mark.anyio
async def test_no_bills_yields_no_bills_status(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    bills = (await demo_client.get("/api/v1/bills", headers=headers)).json()["bills"]
    for bill in bills:
        deleted = await demo_client.delete(f"/api/v1/bills/{bill['id']}", headers=headers)
        assert deleted.status_code == 204

    body = await _context(demo_client, demo_token)
    fund = body["emergency_fund"]
    assert fund["status"] == "no_bills"
    assert fund["months"] is None
    assert fund["gap_to_recommended"] is None
    assert body["emergency_fund_months"] is None
