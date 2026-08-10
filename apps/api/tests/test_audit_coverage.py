"""M32: sensitive mutations beyond M9's own writes leave audit trails."""

import pytest
from sqlalchemy import select

from family_cfo_api import fixtures, models, repository


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


# --- #61: the undo path audits itself ----------------------------------------


async def _undoable_bill_event(client, headers) -> dict:
    """Create a bill and return its (undoable) audit event."""
    created = await client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": "Audited Bill",
            "amount": {"amount_minor": 4500, "currency": "USD"},
            "frequency": "monthly",
        },
    )
    assert created.status_code == 201, created.text
    events = (await client.get("/api/v1/audit", headers=headers)).json()["events"]
    event = next(e for e in events if e["action"] == "bill.created")
    assert event["undoable"] is True
    return event


@pytest.mark.anyio
async def test_undoing_an_action_is_itself_an_audit_event(demo_client, demo_token) -> None:
    """Reading the log top to bottom must show the reversal, not just a flag on
    the row it reversed."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    event = await _undoable_bill_event(demo_client, headers)

    undone = await demo_client.post(f"/api/v1/audit/{event['id']}/undo", headers=headers)
    assert undone.status_code == 200, undone.text

    events = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    reversal = next(e for e in events if e["action"] == "audit.reverted")
    # Attributed to whoever pressed it, pointing at the event it reversed, and
    # naming the original action so the entry stands on its own.
    assert reversal["actor_user_id"] == fixtures.DEMO_USER_ID
    assert reversal["entity_id"] == event["id"]
    assert event["action"] in reversal["summary"]


@pytest.mark.anyio
async def test_undo_records_who_reverted_the_event(demo_client, demo_engine, demo_token) -> None:
    """The flag is redundant with the event on purpose: it makes "is this
    reverted, and by whom?" answerable without scanning the timeline."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    event = await _undoable_bill_event(demo_client, headers)
    before = repository.get_audit_event(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, event["id"])
    assert before is not None and before.reverted_by is None

    await demo_client.post(f"/api/v1/audit/{event['id']}/undo", headers=headers)

    after = repository.get_audit_event(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, event["id"])
    assert after is not None
    assert after.reverted_at is not None
    assert after.reverted_by == fixtures.DEMO_USER_ID


@pytest.mark.anyio
async def test_an_undo_cannot_itself_be_undone(demo_client, demo_token) -> None:
    """ADR 0023: undoing an undo is a redo — perform the action again rather
    than nest reversals, so the reversal event carries no undo token."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    event = await _undoable_bill_event(demo_client, headers)
    await demo_client.post(f"/api/v1/audit/{event['id']}/undo", headers=headers)

    events = (await demo_client.get("/api/v1/audit", headers=headers)).json()["events"]
    reversal = next(e for e in events if e["action"] == "audit.reverted")
    assert reversal["undoable"] is False

    refused = await demo_client.post(f"/api/v1/audit/{reversal['id']}/undo", headers=headers)
    assert refused.status_code == 400
