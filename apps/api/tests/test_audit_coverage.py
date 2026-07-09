"""M32: sensitive mutations beyond M9's own writes leave audit trails."""

import pytest
from sqlalchemy import select

from family_cfo_api import fixtures, models


async def _actions(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(select(models.audit_events.c.action)).all()
    return {row[0] for row in rows}


@pytest.mark.anyio
async def test_login_and_runtime_change_are_audited(demo_client, demo_engine, demo_token) -> None:
    # demo_token fixture performed a login already.
    await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm:8000",
            "model": "Qwen/Qwen2.5-32B-Instruct",
            "enabled": True,
        },
    )
    actions = await _actions(demo_engine)
    assert "auth.login" in actions
    assert "ai_runtime.updated" in actions


@pytest.mark.anyio
async def test_import_apply_is_audited(demo_client, demo_engine, demo_token) -> None:
    created = await demo_client.post(
        "/api/v1/imports",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"source_type": "csv", "filename": "x.csv"},
    )
    import_id = created.json()["id"]
    await demo_client.post(
        f"/api/v1/imports/{import_id}/apply", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert "import.applied" in await _actions(demo_engine)


@pytest.mark.anyio
async def test_audit_summaries_carry_no_secrets(demo_client, demo_engine, demo_token) -> None:
    with demo_engine.connect() as conn:
        rows = conn.execute(select(models.audit_events.c.summary)).all()
    blob = " ".join(r[0] for r in rows)
    assert fixtures.DEMO_USER_PASSWORD not in blob
    assert "Bearer" not in blob
