import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository


@pytest.mark.anyio
async def test_create_backup_requires_authentication(demo_file_client) -> None:
    response = await demo_file_client.post("/api/v1/backups")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_create_list_and_restore_backup(demo_file_client, demo_file_token) -> None:
    create_response = await demo_file_client.post(
        "/api/v1/backups", headers={"Authorization": f"Bearer {demo_file_token}"}
    )
    assert create_response.status_code == 201
    backup = create_response.json()
    assert backup["status"] == "completed"
    assert backup["size_bytes"] > 0
    assert "storage_path" not in backup

    list_response = await demo_file_client.get(
        "/api/v1/backups", headers={"Authorization": f"Bearer {demo_file_token}"}
    )
    assert list_response.status_code == 200
    assert any(b["id"] == backup["id"] for b in list_response.json()["backups"])

    restore_response = await demo_file_client.post(
        f"/api/v1/backups/{backup['id']}/restore",
        headers={"Authorization": f"Bearer {demo_file_token}"},
    )
    assert restore_response.status_code == 200
    # A full-database restore rolls back backup_jobs too, to its state at dump time
    # ("running", set just before the dump -- "completed" was written after). That
    # data-level round trip is covered by test_backup_processing.py; here we only
    # assert the restore endpoint completed without error.
    assert restore_response.json()["id"] == backup["id"]


@pytest.mark.anyio
async def test_restore_audit_row_survives_the_restore_and_states_the_gap(
    demo_file_client, demo_file_token, demo_file_engine: Engine
) -> None:
    """#62: a restore replaces the whole database, `audit_events` included, so the
    trail is rolled back to the snapshot. The one row that must survive is the
    `backup.restored` row itself — written AFTER the replace — and it has to say how
    much history the restore discarded, or the gap is invisible.

    This test is the point of the exercise: if the row is written before the replace
    (or the count is taken after it), the assertions below fail.
    """
    headers = {"Authorization": f"Bearer {demo_file_token}"}

    create_response = await demo_file_client.post("/api/v1/backups", headers=headers)
    assert create_response.status_code == 201
    backup = create_response.json()

    # Audited work that happens AFTER the snapshot: the `backup.created` row for the
    # backup above (written once the dump was already taken) plus three key reveals.
    for _ in range(3):
        reveal = await demo_file_client.get("/api/v1/backups/encryption-key", headers=headers)
        assert reveal.status_code == 200

    job = repository.get_backup_job(demo_file_engine, backup["id"])
    assert job is not None
    doomed = repository.count_audit_events_since(
        demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID, job.started_at
    )
    assert doomed == 4  # backup.created + 3 × backup.key_revealed

    restore_response = await demo_file_client.post(
        f"/api/v1/backups/{backup['id']}/restore", headers=headers
    )
    assert restore_response.status_code == 200

    events = repository.list_audit_events(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID)
    # The restore really did roll the trail back — none of the discarded rows are left.
    assert not [e for e in events if e.action == "backup.key_revealed"]
    # …and the row describing the restore is still here, on the other side of it.
    restored = [e for e in events if e.action == "backup.restored"]
    assert len(restored) == 1
    assert restored[0].entity_id == backup["id"]
    assert "4 audit event(s)" in restored[0].summary
    assert "snapshot" in restored[0].summary


@pytest.mark.anyio
async def test_restore_unknown_backup_returns_404(demo_file_client, demo_file_token) -> None:
    response = await demo_file_client.post(
        "/api/v1/backups/00000000-0000-0000-0000-000000000000/restore",
        headers={"Authorization": f"Bearer {demo_file_token}"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_viewer_cannot_create_or_list_backups(demo_client, demo_viewer_token) -> None:
    create_response = await demo_client.post(
        "/api/v1/backups", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert create_response.status_code == 403

    list_response = await demo_client.get(
        "/api/v1/backups", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert list_response.status_code == 403
