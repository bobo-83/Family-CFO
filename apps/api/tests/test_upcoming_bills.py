"""M39: bill due-date surfacing, roll-forward, and the upcoming-bills window."""

from datetime import date, timedelta

import pytest

from family_cfo_api import finance_service as fs


def test_next_occurrence_returns_future_dates_unchanged() -> None:
    future = date(2026, 8, 1)
    assert fs.next_bill_occurrence(future, "monthly", date(2026, 7, 9)) == future


def test_next_occurrence_rolls_stale_dates_forward() -> None:
    today = date(2026, 7, 9)
    assert fs.next_bill_occurrence(date(2026, 1, 15), "monthly", today) == date(2026, 7, 15)
    assert fs.next_bill_occurrence(date(2026, 1, 10), "quarterly", today) == date(2026, 7, 10)
    # Annual: March 9 anchored in 2020 lands on the first March 9 >= today.
    assert fs.next_bill_occurrence(date(2020, 3, 9), "annual", today) == date(2027, 3, 9)
    assert fs.next_bill_occurrence(date(2026, 7, 1), "weekly", today) == date(2026, 7, 15)
    assert fs.next_bill_occurrence(date(2026, 7, 1), "biweekly", today) == date(2026, 7, 15)


def test_next_occurrence_clamps_end_of_month() -> None:
    # Jan 31 monthly, evaluated mid-February 2026 -> Feb 28 (2026 is not a leap year).
    assert fs.next_bill_occurrence(date(2026, 1, 31), "monthly", date(2026, 2, 15)) == date(
        2026, 2, 28
    )


@pytest.mark.anyio
async def test_bill_due_date_round_trips_through_the_api(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    due = date.today() + timedelta(days=5)
    created = await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": "Water",
            "amount": {"amount_minor": 6000, "currency": "USD"},
            "frequency": "monthly",
            "next_due_date": due.isoformat(),
        },
    )
    assert created.status_code == 201
    # The latent drop is fixed: the due date survives the round trip.
    assert created.json()["next_due_date"] == due.isoformat()

    listed = (await demo_client.get("/api/v1/bills", headers=headers)).json()["bills"]
    water = next(b for b in listed if b["name"] == "Water")
    assert water["next_due_date"] == due.isoformat()


@pytest.mark.anyio
async def test_overview_lists_only_bills_due_within_the_window(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    soon = date.today() + timedelta(days=3)
    far = date.today() + timedelta(days=40)
    await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": "Due soon",
            "amount": {"amount_minor": 1000, "currency": "USD"},
            "frequency": "annual",
            "next_due_date": soon.isoformat(),
        },
    )
    await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": "Due later",
            "amount": {"amount_minor": 1000, "currency": "USD"},
            "frequency": "annual",
            "next_due_date": far.isoformat(),
        },
    )

    context = (await demo_client.get("/api/v1/household", headers=headers)).json()
    names = [b["name"] for b in context["upcoming_bills"]]
    assert "Due soon" in names
    assert "Due later" not in names
    entry = next(b for b in context["upcoming_bills"] if b["name"] == "Due soon")
    assert entry["days_until"] == 3
    # Sorted ascending by due date.
    days = [b["days_until"] for b in context["upcoming_bills"]]
    assert days == sorted(days)
