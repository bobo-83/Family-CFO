"""M40: net-worth snapshots, upsert idempotency, ordering, and the context series."""

from datetime import date, timedelta

import pytest

from family_cfo_api import fixtures, net_worth_history, repository

_HH = fixtures.DEMO_HOUSEHOLD_ID


def test_snapshot_is_idempotent_per_day(demo_engine) -> None:
    today = date(2026, 7, 9)
    repository.record_net_worth_snapshot(demo_engine, _HH, today, 100_000, "USD")
    repository.record_net_worth_snapshot(demo_engine, _HH, today, 250_000, "USD")

    snapshots = repository.list_net_worth_snapshots(demo_engine, _HH)
    same_day = [s for s in snapshots if s.as_of == today]
    assert len(same_day) == 1
    # Latest value for the day wins.
    assert same_day[0].net_worth_minor == 250_000


def test_snapshots_returned_oldest_first(demo_engine) -> None:
    base = date(2026, 7, 1)
    for offset, amount in [(2, 300), (0, 100), (1, 200)]:
        repository.record_net_worth_snapshot(
            demo_engine, _HH, base + timedelta(days=offset), amount, "USD"
        )

    snapshots = repository.list_net_worth_snapshots(demo_engine, _HH)
    assert [s.as_of for s in snapshots] == [base, base + timedelta(days=1), base + timedelta(days=2)]
    assert [s.net_worth_minor for s in snapshots] == [100, 200, 300]


def test_limit_keeps_the_most_recent(demo_engine) -> None:
    base = date(2026, 1, 1)
    for offset in range(5):
        repository.record_net_worth_snapshot(
            demo_engine, _HH, base + timedelta(days=offset), offset, "USD"
        )
    snapshots = repository.list_net_worth_snapshots(demo_engine, _HH, limit=2)
    # The two most recent, still oldest-first.
    assert [s.as_of for s in snapshots] == [
        base + timedelta(days=3),
        base + timedelta(days=4),
    ]


def test_record_snapshot_once_captures_every_household(demo_engine) -> None:
    captured = net_worth_history.record_snapshot_once(demo_engine, today=date(2026, 7, 9))
    assert captured >= 1
    snapshots = repository.list_net_worth_snapshots(demo_engine, _HH)
    assert len(snapshots) == 1
    assert snapshots[0].currency == "USD"


@pytest.mark.anyio
async def test_context_returns_the_history_series(demo_client, demo_token, demo_engine) -> None:
    base = date.today() - timedelta(days=2)
    repository.record_net_worth_snapshot(demo_engine, _HH, base, 1_000_00, "USD")
    repository.record_net_worth_snapshot(
        demo_engine, _HH, base + timedelta(days=1), 1_100_00, "USD"
    )

    context = (
        await demo_client.get(
            "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
        )
    ).json()
    history = context["net_worth_history"]
    assert len(history) == 2
    assert history[0]["as_of"] == base.isoformat()
    assert history[0]["net_worth"]["amount_minor"] == 100_000
    assert history[1]["net_worth"]["amount_minor"] == 110_000
