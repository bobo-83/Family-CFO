"""Declaring a savings contribution (#203).

Two detector iterations found nothing on a household with a known $500/month
529 transfer: the destination never syncs, so neither leg reaches the ledger.
Detection can only report what the data shows, so the household gets to say
it outright — and a declaration outranks anything inferred on the same route.
"""

import pytest


async def _make_account(demo_client, headers, name: str, account_type: str) -> str:
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": name, "type": account_type, "currency": "USD"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _declare(demo_client, headers, source: str, destination: str, minor: int = 500_00):
    return await demo_client.post(
        "/api/v1/savings/contributions",
        headers=headers,
        json={
            "source_account_id": source,
            "destination_account_id": destination,
            "amount": {"amount_minor": minor, "currency": "USD"},
            "frequency": "monthly",
        },
    )


@pytest.mark.anyio
async def test_declared_contribution_appears_in_household_context(demo_client, demo_token):
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, auth_headers, "Everyday checking", "checking")
    college = await _make_account(demo_client, auth_headers, "Kid 529", "529")

    created = await _declare(demo_client, auth_headers, checking, college)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["declared"] is True
    assert body["destination_name"] == "Kid 529"
    assert body["monthly_equivalent"]["amount_minor"] == 500_00
    assert body["contribution_id"]

    context = await demo_client.get("/api/v1/household", headers=auth_headers)
    assert context.status_code == 200, context.text
    declared = [
        c for c in context.json()["savings_contributions"] if c.get("declared")
    ]
    assert any(c["destination_name"] == "Kid 529" for c in declared)


@pytest.mark.anyio
async def test_declaring_an_unknown_account_is_rejected(demo_client, demo_token):
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, auth_headers, "Checking", "checking")
    response = await _declare(demo_client, auth_headers, checking, "not-an-account")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_deleting_stops_the_tracking(demo_client, demo_token):
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, auth_headers, "Checking", "checking")
    college = await _make_account(demo_client, auth_headers, "529", "529")
    contribution_id = (await _declare(demo_client, auth_headers, checking, college)).json()[
        "contribution_id"
    ]

    deleted = await demo_client.delete(
        f"/api/v1/savings/contributions/{contribution_id}", headers=auth_headers
    )
    assert deleted.status_code == 204

    context = await demo_client.get("/api/v1/household", headers=auth_headers)
    assert not [
        c
        for c in context.json()["savings_contributions"]
        if c.get("contribution_id") == contribution_id
    ]

    again = await demo_client.delete(
        f"/api/v1/savings/contributions/{contribution_id}", headers=auth_headers
    )
    assert again.status_code == 404


@pytest.mark.anyio
async def test_deleting_is_undoable(demo_client, demo_token):
    """ADR 0023: every mutation is classified, and this one is reversible."""
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, auth_headers, "Checking", "checking")
    college = await _make_account(demo_client, auth_headers, "529", "529")
    contribution_id = (await _declare(demo_client, auth_headers, checking, college)).json()[
        "contribution_id"
    ]
    await demo_client.delete(
        f"/api/v1/savings/contributions/{contribution_id}", headers=auth_headers
    )

    audit_rows = (await demo_client.get("/api/v1/audit", headers=auth_headers)).json()[
        "events"
    ]
    event = next(a for a in audit_rows if a["action"] == "savings_contribution.deleted")

    undone = await demo_client.post(
        f"/api/v1/audit/{event['id']}/undo", headers=auth_headers
    )
    assert undone.status_code in (200, 204), undone.text

    context = await demo_client.get("/api/v1/household", headers=auth_headers)
    assert [
        c
        for c in context.json()["savings_contributions"]
        if c.get("contribution_id") == contribution_id
    ]


@pytest.mark.anyio
async def test_dismissing_a_route_is_accepted(demo_client, demo_token):
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, auth_headers, "Checking", "checking")
    college = await _make_account(demo_client, auth_headers, "529", "529")
    response = await demo_client.post(
        "/api/v1/savings/contributions/dismiss",
        headers=auth_headers,
        json={"source_account_id": checking, "destination_account_id": college},
    )
    assert response.status_code == 204
    # Idempotent: dismissing twice is not an error.
    again = await demo_client.post(
        "/api/v1/savings/contributions/dismiss",
        headers=auth_headers,
        json={"source_account_id": checking, "destination_account_id": college},
    )
    assert again.status_code == 204
