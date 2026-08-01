"""M-rsu-grants: grants derive an editable vest schedule, valued at a live quote."""

from datetime import date

import pytest

from family_cfo_api import rsu_service


def test_derive_quarterly_two_year_schedule() -> None:
    events = rsu_service.derive_vest_schedule(800, date(2026, 1, 15), 2, "quarterly")

    assert len(events) == 8
    assert events[0] == (date(2026, 4, 15), 100)
    assert events[-1] == (date(2028, 1, 15), 100)
    # Quarterly spacing throughout.
    assert events[1][0] == date(2026, 7, 15)


def test_derive_spreads_the_remainder_across_earliest_tranches() -> None:
    events = rsu_service.derive_vest_schedule(803, date(2026, 1, 15), 2, "quarterly")

    assert [units for _, units in events] == [101, 101, 101, 100, 100, 100, 100, 100]
    assert sum(units for _, units in events) == 803


def test_derive_rejects_nonsense() -> None:
    assert rsu_service.derive_vest_schedule(0, date(2026, 1, 1), 2, "quarterly") == []
    assert rsu_service.derive_vest_schedule(100, date(2026, 1, 1), 2, "hourly") == []


@pytest.fixture(autouse=True)
def _fake_quote(monkeypatch):
    """No network in tests: XYZ is always $3,500.00."""
    monkeypatch.setattr(rsu_service, "fetch_quote", lambda ticker, settings=None: (350_000, "test"))


async def _make_earner(demo_client, headers) -> str:
    created = await demo_client.post(
        "/api/v1/income/profile/earners",
        headers=headers,
        json={"label": "Lead earner", "base_salary_minor": 30_000_000},
    )
    assert created.status_code == 201, created.text
    analysis = (await demo_client.get("/api/v1/income/analysis", headers=headers)).json()
    return analysis["profile"]["earners"][0]["id"]


@pytest.mark.anyio
async def test_grant_round_trips_with_schedule_quote_and_derived_annual(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    earner_id = await _make_earner(demo_client, headers)

    created = await demo_client.post(
        "/api/v1/income/rsu-grants",
        headers=headers,
        json={
            "earner_id": earner_id,
            "ticker": "acme",
            "units": 800,
            "grant_date": date.today().isoformat(),
            "vest_years": 2,
            "frequency": "quarterly",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    grant = body["grants"][0]
    assert grant["ticker"] == "ACME"  # normalized
    assert len(grant["events"]) == 8
    assert grant["events"][0]["units"] == 100
    # Each tranche is valued at the (mocked) live quote.
    assert grant["events"][0]["value"]["amount_minor"] == 100 * 350_000
    assert body["quotes"][0]["ticker"] == "ACME"
    # Next 12 months = 4 quarterly vests of 100 shares at $3,500.
    assert body["derived_annual"]["amount_minor"] == 400 * 350_000


@pytest.mark.anyio
async def test_grant_valuation_replaces_flat_rsu_figure_in_income_and_tax(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    earner_id = await _make_earner(demo_client, headers)

    before = (await demo_client.get("/api/v1/income/analysis", headers=headers)).json()
    assert before["profile"]["earners"][0]["rsu_annual"]["amount_minor"] == 0

    created = await demo_client.post(
        "/api/v1/income/rsu-grants",
        headers=headers,
        json={
            "earner_id": earner_id,
            "ticker": "ACME",
            "units": 800,
            "grant_date": date.today().isoformat(),
        },
    )
    assert created.status_code == 201

    after = (await demo_client.get("/api/v1/income/analysis", headers=headers)).json()
    earner = after["profile"]["earners"][0]
    # 4 upcoming quarterly vests × 100 sh × $3,500 replaces the flat figure.
    assert earner["rsu_annual"]["amount_minor"] == 400 * 350_000
    # The expected events are the REAL schedule rows, labeled with share counts.
    vest_events = [
        e for e in after["profile"]["expected_events"] if "RSU vest" in e["label"]
    ]
    assert vest_events and "(100 sh)" in vest_events[0]["label"]
    assert vest_events[0]["amount"]["amount_minor"] == 100 * 350_000
    # And the annual gross (tax input) includes the live-priced RSU value.
    assert (
        after["profile"]["expected_annual_gross"]["amount_minor"]
        == 30_000_000 + 400 * 350_000
    )


@pytest.mark.anyio
async def test_vest_events_are_editable_and_deletable(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    earner_id = await _make_earner(demo_client, headers)
    created = await demo_client.post(
        "/api/v1/income/rsu-grants",
        headers=headers,
        json={
            "earner_id": earner_id,
            "ticker": "ACME",
            "units": 800,
            "grant_date": "2026-01-15",
        },
    )
    event = created.json()["grants"][0]["events"][0]

    edited = await demo_client.patch(
        f"/api/v1/income/rsu-vest-events/{event['id']}",
        headers=headers,
        json={"units": 120, "vest_date": "2026-05-01"},
    )
    assert edited.status_code == 200
    assert edited.json()["units"] == 120
    assert edited.json()["vest_date"] == "2026-05-01"

    removed = await demo_client.delete(
        f"/api/v1/income/rsu-vest-events/{event['id']}", headers=headers
    )
    assert removed.status_code == 204

    listed = (await demo_client.get("/api/v1/income/rsu-grants", headers=headers)).json()
    assert len(listed["grants"][0]["events"]) == 7


@pytest.mark.anyio
async def test_deleting_a_grant_is_undoable_with_its_edited_schedule(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    earner_id = await _make_earner(demo_client, headers)
    created = await demo_client.post(
        "/api/v1/income/rsu-grants",
        headers=headers,
        json={
            "earner_id": earner_id,
            "ticker": "ACME",
            "units": 800,
            "grant_date": "2026-01-15",
        },
    )
    grant = created.json()["grants"][0]
    # Edit one tranche so the undo must restore the EDITED schedule.
    await demo_client.patch(
        f"/api/v1/income/rsu-vest-events/{grant['events'][0]['id']}",
        headers=headers,
        json={"units": 120},
    )

    deleted = await demo_client.delete(
        f"/api/v1/income/rsu-grants/{grant['id']}", headers=headers
    )
    assert deleted.status_code == 204
    assert (await demo_client.get("/api/v1/income/rsu-grants", headers=headers)).json()[
        "grants"
    ] == []

    events = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    entry = next(a for a in events if a["action"] == "rsu_grant.deleted")
    undone = await demo_client.post(f"/api/v1/audit/{entry['id']}/undo", headers=headers)
    assert undone.status_code in (200, 204)

    restored = (await demo_client.get("/api/v1/income/rsu-grants", headers=headers)).json()
    units = sorted(e["units"] for e in restored["grants"][0]["events"])
    assert len(units) == 8
    assert units[-1] == 120  # the edit survived the delete/undo round trip


@pytest.mark.anyio
async def test_grant_requires_a_real_earner(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    missing = await demo_client.post(
        "/api/v1/income/rsu-grants",
        headers=headers,
        json={
            "earner_id": "00000000-0000-0000-0000-000000000000",
            "ticker": "ACME",
            "units": 800,
            "grant_date": "2026-01-15",
        },
    )
    assert missing.status_code == 404
