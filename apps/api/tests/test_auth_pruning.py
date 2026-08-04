"""Issue #48/#3: the worker prunes dead auth sessions and long-revoked devices
so they stop growing without bound (the 20+ stale pairings a real box grew)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select

from family_cfo_api import models, repository


def _session(engine, hh, user_id, *, token, expires_at, revoked_at=None, device_id=None):
    with engine.begin() as conn:
        conn.execute(
            insert(models.auth_sessions).values(
                id=repository.new_id(),
                user_id=user_id,
                household_id=hh,
                device_id=device_id,
                token_hash=token,
                created_at=datetime.now(UTC) - timedelta(days=30),
                expires_at=expires_at,
                revoked_at=revoked_at,
            )
        )


def test_prune_dead_sessions_keeps_the_living(demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    user_id = repository.list_members(demo_engine, hh)[0].user_id
    now = datetime.now(UTC)

    _session(demo_engine, hh, user_id, token="expired-old", expires_at=now - timedelta(days=10))
    _session(
        demo_engine, hh, user_id, token="revoked-old",
        expires_at=now + timedelta(days=1), revoked_at=now - timedelta(days=10),
    )
    _session(demo_engine, hh, user_id, token="alive", expires_at=now + timedelta(days=1))
    _session(
        demo_engine, hh, user_id, token="expired-recent", expires_at=now - timedelta(hours=1)
    )

    deleted = repository.prune_dead_auth_sessions(demo_engine, older_than=now - timedelta(days=7))
    assert deleted == 2  # the two >7-day-old dead ones

    with demo_engine.connect() as conn:
        remaining = {
            row[0] for row in conn.execute(select(models.auth_sessions.c.token_hash))
        }
    assert "alive" in remaining
    assert "expired-recent" in remaining  # within retention
    assert "expired-old" not in remaining
    assert "revoked-old" not in remaining


def test_prune_revoked_devices_clears_their_sessions_first(demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    user_id = repository.list_members(demo_engine, hh)[0].user_id
    now = datetime.now(UTC)

    with demo_engine.begin() as conn:
        conn.execute(
            insert(models.paired_devices).values(
                id="dev-old", household_id=hh, user_id=user_id, name="Old iPad",
                public_key="k", created_at=now - timedelta(days=200),
                revoked_at=now - timedelta(days=120),
            )
        )
        conn.execute(
            insert(models.paired_devices).values(
                id="dev-recent", household_id=hh, user_id=user_id, name="Spare phone",
                public_key="k", created_at=now - timedelta(days=10),
                revoked_at=now - timedelta(days=3),
            )
        )
    # A session still pointing at the old device (the FK the delete must clear).
    _session(
        demo_engine, hh, user_id, token="on-old-device",
        expires_at=now - timedelta(days=200), device_id="dev-old",
    )

    deleted = repository.prune_revoked_devices(demo_engine, older_than=now - timedelta(days=90))
    assert deleted == 1

    with demo_engine.connect() as conn:
        devices = {row[0] for row in conn.execute(select(models.paired_devices.c.id))}
        sessions = {
            row[0] for row in conn.execute(select(models.auth_sessions.c.token_hash))
        }
    assert "dev-old" not in devices  # revoked >90d → gone
    assert "dev-recent" in devices  # revoked only 3d ago → kept
    assert "on-old-device" not in sessions  # cleared to satisfy the FK
