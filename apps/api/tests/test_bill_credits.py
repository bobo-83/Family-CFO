"""M-credits: statement credits recorded against a bill, rolled up per month/year."""

from datetime import date

import pytest


async def _make_bill(demo_client, headers, name="Electric") -> str:
    created = await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": name,
            "amount": {"amount_minor": 0, "currency": "USD"},
            "frequency": "monthly",
        },
    )
    assert created.status_code == 201
    return created.json()["id"]


@pytest.mark.anyio
async def test_credit_round_trips_with_monthly_and_yearly_rollups(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    bill_id = await _make_bill(demo_client, headers)

    for statement_date, amount in (("2026-06-15", 8_775), ("2026-07-15", 12_340)):
        recorded = await demo_client.post(
            f"/api/v1/bills/{bill_id}/credits",
            headers=headers,
            json={
                "amount": {"amount_minor": amount, "currency": "USD"},
                "statement_date": statement_date,
            },
        )
        assert recorded.status_code == 201
        assert recorded.json()["amount"]["amount_minor"] == amount

    listed = (await demo_client.get("/api/v1/bills/credits", headers=headers)).json()
    group = next(g for g in listed["bills"] if g["bill_id"] == bill_id)
    assert group["name"] == "Electric"
    assert group["total"]["amount_minor"] == 21_115
    # Newest statement first.
    assert [c["statement_date"] for c in group["credits"]] == ["2026-07-15", "2026-06-15"]

    months = {m["month"]: m["total"]["amount_minor"] for m in listed["monthly"]}
    assert months["2026-07"] == 12_340
    assert months["2026-06"] == 8_775
    years = {y["year"]: y["total"]["amount_minor"] for y in listed["yearly"]}
    assert years[2026] == 21_115


@pytest.mark.anyio
async def test_credit_statement_date_defaults_to_today(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    bill_id = await _make_bill(demo_client, headers, name="Gas")

    recorded = await demo_client.post(
        f"/api/v1/bills/{bill_id}/credits",
        headers=headers,
        json={"amount": {"amount_minor": 500, "currency": "USD"}},
    )
    assert recorded.status_code == 201
    assert recorded.json()["statement_date"] == date.today().isoformat()


@pytest.mark.anyio
async def test_credit_rejects_unknown_bill_and_nonpositive_amounts(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    missing = await demo_client.post(
        "/api/v1/bills/00000000-0000-0000-0000-000000000000/credits",
        headers=headers,
        json={"amount": {"amount_minor": 500, "currency": "USD"}},
    )
    assert missing.status_code == 404

    bill_id = await _make_bill(demo_client, headers, name="Trash")
    zero = await demo_client.post(
        f"/api/v1/bills/{bill_id}/credits",
        headers=headers,
        json={"amount": {"amount_minor": 0, "currency": "USD"}},
    )
    assert zero.status_code == 422


@pytest.mark.anyio
async def test_deleting_a_bill_removes_its_credit_history(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    bill_id = await _make_bill(demo_client, headers, name="Sewer")
    recorded = await demo_client.post(
        f"/api/v1/bills/{bill_id}/credits",
        headers=headers,
        json={"amount": {"amount_minor": 700, "currency": "USD"}},
    )
    assert recorded.status_code == 201

    deleted = await demo_client.delete(f"/api/v1/bills/{bill_id}", headers=headers)
    assert deleted.status_code == 204

    listed = (await demo_client.get("/api/v1/bills/credits", headers=headers)).json()
    assert all(g["bill_id"] != bill_id for g in listed["bills"])


@pytest.mark.anyio
async def test_recording_a_credit_is_undoable(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    bill_id = await _make_bill(demo_client, headers, name="Power")
    recorded = await demo_client.post(
        f"/api/v1/bills/{bill_id}/credits",
        headers=headers,
        json={"amount": {"amount_minor": 1_234, "currency": "USD"}},
    )
    assert recorded.status_code == 201

    audit_rows = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    entry = next(a for a in audit_rows if a["action"] == "bill_credit.recorded")
    undone = await demo_client.post(
        f"/api/v1/audit/{entry['id']}/undo", headers=headers
    )
    assert undone.status_code in (200, 204)

    listed = (await demo_client.get("/api/v1/bills/credits", headers=headers)).json()
    assert all(g["bill_id"] != bill_id for g in listed["bills"])
