"""The "vested RSUs, ready to sell" line beside safe-to-spend.

The user tags the brokerage account holding vested shares; the tagged
account's provider-synced balance rides on the safe-to-spend payload as
informational context — the headline number must never move, because
shares aren't cash until sold.
"""

import pytest


async def _overview_safe_to_spend(demo_client, headers) -> dict:
    context = (await demo_client.get("/api/v1/household", headers=headers)).json()
    assert context["safe_to_spend"] is not None
    return context["safe_to_spend"]


async def _make_brokerage(demo_client, headers, balance_minor: int) -> str:
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Employer stock plan", "type": "brokerage", "currency": "USD"},
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    recorded = await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": balance_minor, "currency": "USD"}},
    )
    assert recorded.status_code == 201, recorded.text
    return account_id


@pytest.mark.anyio
async def test_tagged_account_rides_beside_safe_to_spend_without_moving_it(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    account_id = await _make_brokerage(demo_client, headers, 84_000_00)

    before = await _overview_safe_to_spend(demo_client, headers)
    assert before.get("ready_to_sell") is None  # nothing tagged yet

    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"rsu_ready_to_sell": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["rsu_ready_to_sell"] is True

    after = await _overview_safe_to_spend(demo_client, headers)
    ready = after["ready_to_sell"]
    assert ready is not None
    assert ready["value"]["amount_minor"] == 84_000_00
    assert ready["accounts"] == [
        {"name": "Employer stock plan", "amount": {"amount_minor": 84_000_00, "currency": "USD"}}
    ]
    assert ready["sale_notice_business_days"] == 4

    # Informational only: the headline and its inputs are untouched (a
    # brokerage account was never part of liquid cash).
    assert after["safe_to_spend"] == before["safe_to_spend"]
    assert after["liquid_balance"] == before["liquid_balance"]
    assert after["committed_total"] == before["committed_total"]

    # Untagging removes the line again.
    untagged = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"rsu_ready_to_sell": False},
    )
    assert untagged.status_code == 200, untagged.text
    assert untagged.json()["rsu_ready_to_sell"] is False
    final = await _overview_safe_to_spend(demo_client, headers)
    assert final.get("ready_to_sell") is None


@pytest.mark.anyio
async def test_tagging_is_undoable(demo_client, demo_token) -> None:
    """ADR 0023: the tag rides the account-update mutation, so undoing the
    update must restore the previous tag state."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    account_id = await _make_brokerage(demo_client, headers, 10_000_00)

    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"rsu_ready_to_sell": True},
    )
    assert updated.status_code == 200, updated.text

    events = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    update_event = next(e for e in events if e["action"] == "account.updated")
    undone = await demo_client.post(
        f"/api/v1/audit/{update_event['id']}/undo", headers=headers
    )
    assert undone.status_code == 200, undone.text

    accounts = (await demo_client.get("/api/v1/accounts", headers=headers)).json()["accounts"]
    account = next(a for a in accounts if a["id"] == account_id)
    assert account["rsu_ready_to_sell"] is False
