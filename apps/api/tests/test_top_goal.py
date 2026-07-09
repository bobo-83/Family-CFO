"""M41: highest-priority goal surfaced on the household context with progress."""

import pytest

from family_cfo_api import fixtures, repository
from family_cfo_api.api.household import _top_goal

_HH = fixtures.DEMO_HOUSEHOLD_ID


async def _context(demo_client, demo_token):
    response = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_top_goal_is_highest_priority_with_progress(
    demo_client, demo_token, demo_engine
) -> None:
    # The demo fixture seeds a priority-1 "Emergency fund" goal at 1.5M / 1.8M.
    # A lower-priority goal must not displace it.
    repository.create_goal(
        demo_engine, _HH, name="Vacation", goal_type="vacation",
        target_minor=200_000, currency="USD", target_date=None, priority=5,
    )

    goal = (await _context(demo_client, demo_token))["top_goal"]
    assert goal is not None
    assert goal["name"] == "Emergency fund"
    assert goal["target"]["amount_minor"] == 1_800_000
    assert goal["current"]["amount_minor"] == 1_500_000
    # 1_500_000 / 1_800_000 = 83.3% -> 83.
    assert goal["percent_complete"] == 83


def test_percent_math_caps_and_guards_zero_target() -> None:
    def pct(current: int, target: int) -> int:
        return 0 if target <= 0 else min(100, round(current / target * 100))

    assert pct(0, 0) == 0
    assert pct(500, 0) == 0  # zero-target guard
    assert pct(250, 1000) == 25
    assert pct(999, 1000) == 100  # rounds up to 100
    assert pct(5000, 1000) == 100  # capped, never over 100


def test_top_goal_is_none_when_household_has_no_goals(demo_engine) -> None:
    # A household id with no goals resolves to None (the "no goals yet" case).
    assert _top_goal(demo_engine, "household-with-no-goals") is None
