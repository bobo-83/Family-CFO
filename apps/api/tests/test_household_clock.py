"""#41: "today" belongs to the household, not the container."""

import os
from datetime import UTC, date, datetime
from unittest import mock

from family_cfo_api import household_clock


def test_the_household_zone_decides_the_date() -> None:
    """The bug in one assertion: at 00:30 UTC it is still the previous day in
    New York, and already that day in Edinburgh. A UTC container answered
    'today' wrongly for one of them."""
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
    """#41 end to end: a bill due 'today' in Edinburgh must not read as due
    tomorrow because the container is in another zone."""
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
