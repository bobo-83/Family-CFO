"""Manual bill-payment links: "I already paid this — here's the transaction."

Auto-matching pairs a bill with its charge by merchant + due-window within a
±30% amount tolerance, which variable-amount bills can miss. The link is the
user's explicit receipt: it wins over the matcher, flips the timeline row to
paid with the ACTUAL amount, and releases the occurrence's claim on cash.
"""

from datetime import date, timedelta

import pytest


async def _make_checking(demo_client, headers) -> str:
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Bill pay checking", "type": "checking", "currency": "USD"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _make_bill(demo_client, headers, due: date) -> str:
    created = await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": "Metro Power",
            "amount": {"amount_minor": 200_00, "currency": "USD"},
            "frequency": "monthly",
            "next_due_date": due.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _make_charge(demo_client, headers, account_id: str, when: date, amount_minor: int) -> str:
    created = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "occurred_at": when.isoformat(),
            "amount": {"amount_minor": amount_minor, "currency": "USD"},
            # Named nothing like the bill, and far outside the ±30% tolerance —
            # exactly the charge the auto-matcher cannot pair.
            "merchant": "ACH WITHDRAWAL 0042",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _timeline_item(body: dict, bill_id: str) -> dict:
    return next(item for item in body["items"] if item["id"] == bill_id)


@pytest.mark.anyio
async def test_link_marks_occurrence_paid_and_releases_the_reserve(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    due = date.today() + timedelta(days=5)
    account_id = await _make_checking(demo_client, headers)
    bill_id = await _make_bill(demo_client, headers, due)
    txn_id = await _make_charge(
        demo_client, headers, account_id, date.today() - timedelta(days=1), -55_00
    )

    timeline = (await demo_client.get("/api/v1/bills/timeline", headers=headers)).json()
    assert _timeline_item(timeline, bill_id)["status"] == "due_soon"

    candidates = (
        await demo_client.get(
            f"/api/v1/bills/{bill_id}/payment-candidates?due_date={due.isoformat()}",
            headers=headers,
        )
    ).json()
    assert any(t["id"] == txn_id for t in candidates["transactions"])

    linked = await demo_client.post(
        f"/api/v1/bills/{bill_id}/payment-link",
        headers=headers,
        json={"transaction_id": txn_id, "due_date": due.isoformat()},
    )
    assert linked.status_code == 201, linked.text
    link_id = linked.json()["id"]

    timeline = (await demo_client.get("/api/v1/bills/timeline", headers=headers)).json()
    item = _timeline_item(timeline, bill_id)
    assert item["status"] == "paid"
    assert item["paid_with"]["source"] == "linked"
    assert item["paid_with"]["link_id"] == link_id
    assert item["paid_with"]["transaction_id"] == txn_id
    # The receipt shows the ACTUAL charge, not the bill's estimate.
    assert item["paid_with"]["amount"]["amount_minor"] == 55_00

    # The settled occurrence no longer claims cash: it leaves the overview's
    # due-soon list and safe-to-spend's bills_due reserve.
    context = (await demo_client.get("/api/v1/household", headers=headers)).json()
    assert all(b["id"] != bill_id for b in context["upcoming_bills"])
    assert all(b["name"] != "Metro Power" for b in context["safe_to_spend"]["bill_items"])

    # Unlinking puts the claim back.
    gone = await demo_client.delete(
        f"/api/v1/bills/{bill_id}/payment-link/{link_id}", headers=headers
    )
    assert gone.status_code == 204, gone.text
    timeline = (await demo_client.get("/api/v1/bills/timeline", headers=headers)).json()
    assert _timeline_item(timeline, bill_id)["status"] == "due_soon"


@pytest.mark.anyio
async def test_link_conflicts_and_validation(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    due = date.today() + timedelta(days=5)
    account_id = await _make_checking(demo_client, headers)
    bill_id = await _make_bill(demo_client, headers, due)
    txn_id = await _make_charge(
        demo_client, headers, account_id, date.today() - timedelta(days=1), -55_00
    )

    # An inflow can't pay a bill.
    deposit = await _make_charge(demo_client, headers, account_id, date.today(), -1)
    inflow = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "occurred_at": date.today().isoformat(),
            "amount": {"amount_minor": 55_00, "currency": "USD"},
            "merchant": "Refund",
        },
    )
    rejected = await demo_client.post(
        f"/api/v1/bills/{bill_id}/payment-link",
        headers=headers,
        json={"transaction_id": inflow.json()["id"], "due_date": due.isoformat()},
    )
    assert rejected.status_code == 400

    first = await demo_client.post(
        f"/api/v1/bills/{bill_id}/payment-link",
        headers=headers,
        json={"transaction_id": txn_id, "due_date": due.isoformat()},
    )
    assert first.status_code == 201

    # Same occurrence again → 409; same transaction against another bill → 409.
    duplicate = await demo_client.post(
        f"/api/v1/bills/{bill_id}/payment-link",
        headers=headers,
        json={"transaction_id": deposit, "due_date": due.isoformat()},
    )
    assert duplicate.status_code == 409
    other_bill = await _make_bill(demo_client, headers, due + timedelta(days=1))
    reused = await demo_client.post(
        f"/api/v1/bills/{other_bill}/payment-link",
        headers=headers,
        json={"transaction_id": txn_id, "due_date": (due + timedelta(days=1)).isoformat()},
    )
    assert reused.status_code == 409


@pytest.mark.anyio
async def test_linking_and_unlinking_are_undoable(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    due = date.today() + timedelta(days=5)
    account_id = await _make_checking(demo_client, headers)
    bill_id = await _make_bill(demo_client, headers, due)
    txn_id = await _make_charge(
        demo_client, headers, account_id, date.today() - timedelta(days=1), -55_00
    )

    linked = await demo_client.post(
        f"/api/v1/bills/{bill_id}/payment-link",
        headers=headers,
        json={"transaction_id": txn_id, "due_date": due.isoformat()},
    )
    assert linked.status_code == 201
    link_id = linked.json()["id"]

    events = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    link_event = next(e for e in events if e["action"] == "bill.payment_linked")
    undone = await demo_client.post(f"/api/v1/audit/{link_event['id']}/undo", headers=headers)
    assert undone.status_code == 200, undone.text
    timeline = (await demo_client.get("/api/v1/bills/timeline", headers=headers)).json()
    assert _timeline_item(timeline, bill_id)["status"] == "due_soon"

    # Re-link, unlink, then undo the unlink: the SAME link id comes back.
    relinked = await demo_client.post(
        f"/api/v1/bills/{bill_id}/payment-link",
        headers=headers,
        json={"transaction_id": txn_id, "due_date": due.isoformat()},
    )
    link_id = relinked.json()["id"]
    await demo_client.delete(f"/api/v1/bills/{bill_id}/payment-link/{link_id}", headers=headers)
    events = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    unlink_event = next(e for e in events if e["action"] == "bill.payment_unlinked")
    undone = await demo_client.post(f"/api/v1/audit/{unlink_event['id']}/undo", headers=headers)
    assert undone.status_code == 200, undone.text
    timeline = (await demo_client.get("/api/v1/bills/timeline", headers=headers)).json()
    item = _timeline_item(timeline, bill_id)
    assert item["status"] == "paid"
    assert item["paid_with"]["link_id"] == link_id
