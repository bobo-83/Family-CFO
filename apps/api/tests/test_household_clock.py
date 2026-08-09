"""#41: "today" belongs to the household, not the container."""

import os
from datetime import UTC, date, datetime
from unittest import mock

from family_cfo_api import household_clock


def test_the_household_zone_decides_the_date() -> None:
    """The bug in one assertion: at 00:30 UTC a household west of UTC is still
    on the previous day while one east of it has already turned over. A UTC
    container answered 'today' wrongly for one of them."""
    at_0030_utc = datetime(2026, 8, 9, 0, 30, tzinfo=UTC)
    with mock.patch("family_cfo_api.household_clock.datetime") as clock:
        clock.now.side_effect = lambda tz=None: at_0030_utc.astimezone(tz)
        assert household_clock.today_for("America/New_York") == date(2026, 8, 8)
        assert household_clock.today_for("Europe/London") == date(2026, 8, 9)


def test_box_default_applies_when_the_household_has_no_zone() -> None:
    with mock.patch.dict(
        os.environ, {household_clock.DEFAULT_TIMEZONE_ENV: "Europe/London"}
    ):
        assert household_clock.resolve_zone(None) is not None
        # The household's own zone still wins over the box default.
        assert str(household_clock.resolve_zone("America/New_York")) == "America/New_York"


def test_an_unknown_zone_falls_back_instead_of_raising() -> None:
    """A typo in a setting must not take the Overview down."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(household_clock.DEFAULT_TIMEZONE_ENV, None)
        assert household_clock.resolve_zone("Mars/Olympus_Mons") is None
        # And the date still resolves, using process-local time.
        assert isinstance(household_clock.today_for("Mars/Olympus_Mons"), date)


def test_no_zone_anywhere_keeps_todays_behaviour() -> None:
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(household_clock.DEFAULT_TIMEZONE_ENV, None)
        assert household_clock.today_for(None) == date.today()


# --- end to end: the household's zone reaches the date math -----------------

import pytest


@pytest.mark.anyio
async def test_timezone_round_trips_and_is_validated(demo_client, demo_token):
    headers = {"Authorization": f"Bearer {demo_token}"}
    updated = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": "Europe/London"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["timezone"] == "Europe/London"

    rejected = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": "Mars/Olympus_Mons"}
    )
    assert rejected.status_code == 422
    assert "timezone" in rejected.json()["error"]["message"].lower()


@pytest.mark.anyio
async def test_date_math_follows_the_household_zone(demo_client, demo_token, demo_engine):
    """#41 end to end: a bill due 'today' in the household's own zone must not
    read as due tomorrow because the container is in another zone."""
    from family_cfo_api import finance_service, household_clock, repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": "Pacific/Kiritimati"}
    )
    from family_cfo_api import fixtures

    household = repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    # Kiritimati is UTC+14 — the furthest zone from UTC there is, so if the
    # household's zone is ignored this assertion fails on most of the clock.
    assert household_clock.today_for(household.timezone) == household_clock.today_for(
        "Pacific/Kiritimati"
    )
    # And the service layer picks it up without being told.
    resolved = finance_service.upcoming_bills(
        demo_engine, household.id, household.base_currency
    )
    assert isinstance(resolved, list)


# --- #43: getting back to the box's own zone --------------------------------


@pytest.mark.anyio
async def test_clearing_the_zone_puts_the_column_back_to_null(
    demo_client, demo_token, demo_engine
):
    """A null `timezone` in the payload is indistinguishable from an omitted
    one, so before #43 the inherit state was one-way: reachable only by never
    having chosen a zone."""
    from family_cfo_api import fixtures, repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": "Europe/London"}
    )

    # The old spelling still means "leave unchanged" — that is the bug.
    noop = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": None}
    )
    assert noop.status_code == 200, noop.text
    assert noop.json()["timezone"] == "Europe/London"

    cleared = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"clear_timezone": True}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["timezone"] is None
    assert repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone is None


@pytest.mark.anyio
async def test_a_cleared_household_falls_back_to_the_box_default(
    demo_client, demo_token, demo_engine
):
    """The point of clearing: the box's own zone decides "today" again, so a
    household that moves stays correct without anyone touching its settings."""
    from family_cfo_api import fixtures, repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": "Pacific/Kiritimati"}
    )
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"clear_timezone": True}
    )

    household = repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    with mock.patch.dict(
        os.environ, {household_clock.DEFAULT_TIMEZONE_ENV: "America/New_York"}
    ):
        # UTC+14 vs New York: the assertion fails on most of the clock if the
        # cleared column still won.
        assert str(household_clock.resolve_zone(household.timezone)) == "America/New_York"
        assert household_clock.today_for(household.timezone) == household_clock.today_for(
            "America/New_York"
        )


@pytest.mark.anyio
async def test_clearing_and_naming_a_zone_at_once_is_rejected(
    demo_client, demo_token, demo_engine
):
    """Contradictory input: one asks to follow the box, the other names a zone.
    Every date in the app comes off this column, so the request is refused
    rather than resolved — and nothing is written."""
    from family_cfo_api import fixtures, repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": "Europe/London"}
    )

    rejected = await demo_client.patch(
        "/api/v1/household",
        headers=headers,
        json={"timezone": "America/New_York", "clear_timezone": True},
    )
    assert rejected.status_code == 422
    assert "clear_timezone" in rejected.json()["error"]["message"]
    household = repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert household.timezone == "Europe/London"


@pytest.mark.anyio
async def test_clearing_the_zone_is_undoable(demo_client, demo_token, demo_engine):
    """ADR 0023: household.updated is UNDOABLE, so the undo token has to carry
    the zone that was there before the clear."""
    from family_cfo_api import fixtures, repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"timezone": "Europe/London"}
    )
    await demo_client.patch(
        "/api/v1/household", headers=headers, json={"clear_timezone": True}
    )

    events = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    clear_event = next(e for e in events if "box default" in e["summary"])
    undone = await demo_client.post(
        f"/api/v1/audit/{clear_event['id']}/undo", headers=headers
    )
    assert undone.status_code in (200, 204), undone.text
    assert (
        repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone
        == "Europe/London"
    )
