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


@pytest.mark.anyio
async def test_linking_a_contribution_to_a_goal(demo_client, demo_token):
    """#4: the link, its suggestion, the goal's funding line, and undo."""
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, auth_headers, "Checking", "checking")
    college = await _make_account(demo_client, auth_headers, "College 529", "529")
    goal = await demo_client.post(
        "/api/v1/goals",
        headers=auth_headers,
        json={
            "name": "College fund",
            "type": "college",
            "target": {"amount_minor": 5_000_000, "currency": "USD"},
            "target_date": "2038-09-01",
        },
    )
    assert goal.status_code == 201, goal.text
    goal_id = goal.json()["id"]

    contribution_id = (await _declare(demo_client, auth_headers, checking, college)).json()[
        "contribution_id"
    ]

    # The 529 destination + a single college goal -> a one-tap suggestion.
    context = await demo_client.get("/api/v1/household", headers=auth_headers)
    mine = next(
        c
        for c in context.json()["savings_contributions"]
        if c.get("contribution_id") == contribution_id
    )
    assert mine["suggested_goal_id"] == goal_id
    assert mine["goal_id"] is None

    linked = await demo_client.patch(
        f"/api/v1/savings/contributions/{contribution_id}",
        headers=auth_headers,
        json={"goal_id": goal_id},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["goal_id"] == goal_id

    # The goal now reports its funding: $500/mo, on track for 2038.
    goals = (await demo_client.get("/api/v1/goals", headers=auth_headers)).json()["goals"]
    funded = next(g for g in goals if g["id"] == goal_id)
    assert funded["funding"]["monthly_equivalent"]["amount_minor"] == 500_00
    assert funded["funding"]["status"] == "on_track"
    assert funded["funding"]["funded_by"][0]["contribution_id"] == contribution_id
    assert funded["funding"]["projected_completion"] is not None

    # A linked contribution stops being suggested.
    context = await demo_client.get("/api/v1/household", headers=auth_headers)
    mine = next(
        c
        for c in context.json()["savings_contributions"]
        if c.get("contribution_id") == contribution_id
    )
    assert mine["goal_id"] == goal_id
    assert mine["suggested_goal_id"] is None

    # Undoing the link restores the previous (unlinked) state.
    audit_rows = (await demo_client.get("/api/v1/audit", headers=auth_headers)).json()["events"]
    event = next(a for a in audit_rows if a["action"] == "savings_contribution.linked")
    undone = await demo_client.post(f"/api/v1/audit/{event['id']}/undo", headers=auth_headers)
    assert undone.status_code in (200, 204), undone.text
    goals = (await demo_client.get("/api/v1/goals", headers=auth_headers)).json()["goals"]
    assert next(g for g in goals if g["id"] == goal_id)["funding"]["status"] == "unfunded"


@pytest.mark.anyio
async def test_unfunded_goal_says_so_and_dismiss_undo_works(demo_client, demo_token):
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    goal = await demo_client.post(
        "/api/v1/goals",
        headers=auth_headers,
        json={
            "name": "New roof",
            "type": "renovation",
            "target": {"amount_minor": 2_000_000, "currency": "USD"},
        },
    )
    goal_id = goal.json()["id"]
    goals = (await demo_client.get("/api/v1/goals", headers=auth_headers)).json()["goals"]
    fresh = next(g for g in goals if g["id"] == goal_id)
    assert fresh["funding"]["status"] == "unfunded"
    assert fresh["funding"]["funded_by"] == []

    # #203 latent fix: undoing a route dismissal must actually undismiss.
    checking = await _make_account(demo_client, auth_headers, "C2", "checking")
    college = await _make_account(demo_client, auth_headers, "5292", "529")
    dismissed = await demo_client.post(
        "/api/v1/savings/contributions/dismiss",
        headers=auth_headers,
        json={"source_account_id": checking, "destination_account_id": college},
    )
    assert dismissed.status_code == 204
    audit_rows = (await demo_client.get("/api/v1/audit", headers=auth_headers)).json()["events"]
    event = next(a for a in audit_rows if a["action"] == "savings_contribution.dismissed")
    undone = await demo_client.post(f"/api/v1/audit/{event['id']}/undo", headers=auth_headers)
    assert undone.status_code in (200, 204), undone.text


@pytest.mark.anyio
async def test_linking_to_a_missing_goal_is_rejected(demo_client, demo_token):
    auth_headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, auth_headers, "C3", "checking")
    college = await _make_account(demo_client, auth_headers, "5293", "529")
    contribution_id = (await _declare(demo_client, auth_headers, checking, college)).json()[
        "contribution_id"
    ]
    rejected = await demo_client.patch(
        f"/api/v1/savings/contributions/{contribution_id}",
        headers=auth_headers,
        json={"goal_id": "nope"},
    )
    assert rejected.status_code == 404


async def _make_college_goal(demo_client, headers):
    r = await demo_client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "name": "College",
            "type": "college",
            "target": {"amount_minor": 5_000_000, "currency": "USD"},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.anyio
async def test_committed_savings_shows_beside_safe_to_spend(demo_client, demo_token):
    """#5: a declared monthly 529 contribution appears as committed savings on
    the overview's safe-to-spend, informational by default (not subtracted)."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    checking = await _make_account(demo_client, headers, "Checking", "checking")
    college = await _make_account(demo_client, headers, "529", "529")
    await _declare(demo_client, headers, checking, college)

    context = (await demo_client.get("/api/v1/household", headers=headers)).json()
    s2s = context["safe_to_spend"]
    assert s2s is not None
    # Informational by default: an amount and a drill-down, NOT reserved.
    assert s2s["committed_savings"]["amount_minor"] == 500_00
    assert s2s["committed_savings_reserved"] is False
    assert s2s["committed_savings_items"], "expected a labelled committed-savings line"
    baseline_committed = s2s["committed_total"]["amount_minor"]

    # Flip the household to reserve it: safe-to-spend shrinks by exactly $500.
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"reserve_committed_savings": True}
    )
    context = (await demo_client.get("/api/v1/household", headers=headers)).json()
    s2s2 = context["safe_to_spend"]
    assert s2s2["committed_savings_reserved"] is True
    assert s2s2["committed_total"]["amount_minor"] == baseline_committed + 500_00
    assert (
        s2s2["safe_to_spend"]["amount_minor"]
        == s2s["safe_to_spend"]["amount_minor"] - 500_00
    )
