"""M43: per-household configurable emergency-fund target."""

import pytest


async def _context(demo_client, token):
    r = await demo_client.get("/api/v1/household", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


@pytest.mark.anyio
async def test_default_target_is_six(demo_client, demo_token) -> None:
    fund = (await _context(demo_client, demo_token))["emergency_fund"]
    assert fund["target_months_recommended"] == 6


@pytest.mark.anyio
async def test_setting_target_recomputes_summary(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    updated = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"emergency_fund_target_months": 3}
    )
    assert updated.status_code == 200
    fund = updated.json()["emergency_fund"]
    assert fund["target_months_recommended"] == 3
    # Gap now measures against 3 months of expenses, not 6.
    monthly = fund["monthly_expenses"]["amount_minor"]
    reserved = fund["reserved"]["amount_minor"]
    assert fund["gap_to_recommended"]["amount_minor"] == max(0, 3 * monthly - reserved)


@pytest.mark.anyio
async def test_sub_three_target_lowers_the_getting_started_floor(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    fund = (
        await demo_client.patch(
            "/api/v1/household", headers=headers, json={"emergency_fund_target_months": 2}
        )
    ).json()["emergency_fund"]
    # The min threshold never exceeds the target.
    assert fund["target_months_min"] == 2
    assert fund["target_months_recommended"] == 2


@pytest.mark.anyio
async def test_clear_resets_to_default(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"emergency_fund_target_months": 12}
    )
    assert (await _context(demo_client, demo_token))["emergency_fund"][
        "target_months_recommended"
    ] == 12

    cleared = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"clear_emergency_fund_target": True}
    )
    assert cleared.json()["emergency_fund"]["target_months_recommended"] == 6


@pytest.mark.anyio
async def test_out_of_range_target_is_rejected(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    too_big = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"emergency_fund_target_months": 100}
    )
    assert too_big.status_code == 422
    too_small = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"emergency_fund_target_months": 0}
    )
    assert too_small.status_code == 422


@pytest.mark.anyio
async def test_viewer_cannot_change_target(demo_client, demo_viewer_token) -> None:
    headers = {"Authorization": f"Bearer {demo_viewer_token}"}
    response = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"emergency_fund_target_months": 4}
    )
    assert response.status_code == 403
