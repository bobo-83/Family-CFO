"""#181: per-household fairness — chat quota, backup cooldown, usage rollup,
and background-job isolation for locked households."""


import pytest

from family_cfo_api import household_crypto, net_worth_history, repository
from family_cfo_api.config import get_settings
from family_cfo_api.ratelimit import HouseholdQuotaLimiter


def test_quota_limiter_slides_and_disables_at_zero() -> None:
    limiter = HouseholdQuotaLimiter(max_per_hour=2)
    assert limiter.check_and_record("hh", now=0.0) is None
    assert limiter.check_and_record("hh", now=1.0) is None
    wait = limiter.check_and_record("hh", now=2.0)
    assert wait is not None and wait > 0
    # Another household is unaffected; the window slides free after an hour.
    assert limiter.check_and_record("other", now=2.0) is None
    assert limiter.check_and_record("hh", now=3601.5) is None
    assert HouseholdQuotaLimiter(max_per_hour=0).check_and_record("hh") is None


@pytest.mark.anyio
async def test_chat_quota_429s_with_retry_after(
    demo_app, demo_client, demo_token, demo_settings
) -> None:
    from dataclasses import replace

    from family_cfo_api.deps import get_app_settings

    limited = replace(demo_settings, chat_hourly_limit=1)

    async def _limited_settings():
        return limited

    demo_app.dependency_overrides[get_app_settings] = _limited_settings
    try:
        headers = {"Authorization": f"Bearer {demo_token}"}
        first = await demo_client.post(
            "/api/v1/chat/messages", headers=headers, json={"message": "how am I doing?"}
        )
        assert first.status_code in (200, 502, 503)  # quota passed; runtime may be stubbed
        second = await demo_client.post(
            "/api/v1/chat/messages", headers=headers, json={"message": "and now?"}
        )
        assert second.status_code == 429
        assert "Retry-After" in second.headers
    finally:
        demo_app.dependency_overrides.pop(get_app_settings, None)


@pytest.mark.anyio
async def test_manual_backup_cooldown(demo_file_client, demo_file_token) -> None:
    headers = {"Authorization": f"Bearer {demo_file_token}"}
    first = await demo_file_client.post("/api/v1/backups", headers=headers)
    assert first.status_code == 201, first.text
    second = await demo_file_client.post("/api/v1/backups", headers=headers)
    assert second.status_code == 429
    assert "Retry-After" in second.headers


@pytest.mark.anyio
async def test_usage_rollup_shape(demo_client, demo_token, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    repository.create_recommendation(
        demo_engine, hh, None, "an answer", [], [], [], [], 0.9, [], [], "llm",
        model_version="test-model", answer_ms=1200,
    )
    headers = {"Authorization": f"Bearer {demo_token}"}
    usage = await demo_client.get("/api/v1/ai/usage", headers=headers)
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["chat_hourly_limit"] == 0
    entry = next(h for h in body["households"] if h["household_id"] == hh)
    assert entry["chats_7d"] >= 1
    assert entry["median_answer_ms"] == 1200
    assert entry["storage_bytes"] >= 0


def test_snapshot_job_skips_locked_household(demo_engine, monkeypatch) -> None:
    """One sealed+locked household must not stall the snapshot pass (#181)."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    try:
        hh = repository.list_households(demo_engine)[0]
        member = repository.list_members(demo_engine, hh)[0]
        # A SEALED account name in the snapshot's read path — the demo
        # fixture's legacy plaintext reads fine even when locked (by design),
        # so the lock must bite on genuinely sealed content.
        account = repository.create_account(demo_engine, hh, "Sealed savings", "savings", "USD")
        with demo_engine.begin() as conn:
            from sqlalchemy import insert as sql_insert

            from family_cfo_api import models

            conn.execute(
                sql_insert(models.account_balances).values(
                    id=repository.new_id(),
                    account_id=account.id,
                    balance_minor=100000,
                    as_of=repository.utcnow(),
                    created_at=repository.utcnow(),
                )
            )
        household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "pw")
        assert household_crypto.generate_recovery_key(demo_engine, hh)
        assert household_crypto.seal_household(demo_engine, hh) is None
        household_crypto.reset_cache_for_tests()

        # Locked: the pass completes without raising; the locked household is
        # simply skipped (captured == 0 here since it's the only household).
        captured = net_worth_history.record_snapshot_once(demo_engine)
        assert captured == 0
    finally:
        get_settings.cache_clear()
        household_crypto.reset_cache_for_tests()
