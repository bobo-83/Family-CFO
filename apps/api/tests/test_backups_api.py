import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from family_cfo_api import banksync, fixtures, repository, security, smb_backup
from family_cfo_api.backup_operation_lock import acquire_backup_operation_lock

NEWCOMER_EMAIL = "newcomer@example.com"
NEWCOMER_PASSWORD = "newcomer-password-123"


def _enforce_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys unless asked; Postgres — what the box actually
    runs — never does. #68 is a foreign-key failure on `audit_events.actor_user_id`,
    so the tests below turn the constraint on and see the database the operator has.
    """

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    engine.dispose()  # pooled connections opened before the listener predate the pragma


async def _member_who_joined_after_the_snapshot(
    client: httpx.AsyncClient, engine: Engine
) -> tuple[str, str]:
    """Add a member and log them in. Created AFTER a snapshot was taken, they are
    exactly the person #68 is about: no `users` row inside that snapshot.

    Returns (user_id, access_token).
    """
    member = repository.create_member(
        engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        email=NEWCOMER_EMAIL,
        password_hash=security.hash_password(NEWCOMER_PASSWORD),
        display_name="Late Joiner",
        role="owner",
    )
    # backups.manage is a box right (ADR 0065): it comes off the system-admin
    # roster, not the household role.
    repository.grant_system_admin(engine, member.user_id, fixtures.DEMO_USER_ID)
    response = await client.post(
        "/api/v1/auth/sessions",
        json={"email": NEWCOMER_EMAIL, "password": NEWCOMER_PASSWORD},
    )
    assert response.status_code == 201
    return member.user_id, response.json()["access_token"]


@pytest.mark.anyio
async def test_create_backup_requires_authentication(demo_file_client) -> None:
    response = await demo_file_client.post("/api/v1/backups")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_manual_backup_returns_conflict_while_global_operation_is_owned(
    demo_file_client, demo_file_token, demo_file_engine: Engine
) -> None:
    headers = {"Authorization": f"Bearer {demo_file_token}"}
    lease = acquire_backup_operation_lock(demo_file_engine)
    try:
        response = await demo_file_client.post("/api/v1/backups", headers=headers)
    finally:
        lease.release()
    assert response.status_code == 409

    # A lock conflict must release its anti-hammer reservation.
    retry = await demo_file_client.post("/api/v1/backups", headers=headers)
    assert retry.status_code == 201


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
async def test_restore_of_a_snapshot_older_than_the_actor_succeeds_with_no_actor(
    demo_file_client, demo_file_token, demo_file_engine: Engine
) -> None:
    """#68: a member who joined after the snapshot restores it. Their `users` row
    is not in that snapshot, so the `backup.restored` row written AFTER the replace
    (#62) cannot point at them — `audit_events.actor_user_id` is a foreign key.

    Before the fix this raises an integrity error and the caller sees a 500 for a
    restore that actually worked. The record must survive; only the attribution is
    genuinely gone, and the summary has to say so.
    """
    _enforce_foreign_keys(demo_file_engine)
    owner_headers = {"Authorization": f"Bearer {demo_file_token}"}

    create_response = await demo_file_client.post("/api/v1/backups", headers=owner_headers)
    assert create_response.status_code == 201
    backup = create_response.json()

    newcomer_id, newcomer_token = await _member_who_joined_after_the_snapshot(
        demo_file_client, demo_file_engine
    )

    restore_response = await demo_file_client.post(
        f"/api/v1/backups/{backup['id']}/restore",
        headers={"Authorization": f"Bearer {newcomer_token}"},
    )
    assert restore_response.status_code == 200

    # The restore really did roll the newcomer's account away with everything else.
    assert repository.get_member(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID, newcomer_id) is None

    events = repository.list_audit_events(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID)
    restored = [e for e in events if e.action == "backup.restored"]
    assert len(restored) == 1
    assert restored[0].entity_id == backup["id"]
    assert restored[0].actor_user_id is None
    # …and it says WHY there is no actor, on top of the #62 boundary it already states.
    assert "not present in this snapshot" in restored[0].summary
    assert "snapshot" in restored[0].summary and "audit event(s)" in restored[0].summary


@pytest.mark.anyio
async def test_restore_by_a_member_in_the_snapshot_still_records_the_real_actor(
    demo_file_client, demo_file_token, demo_file_engine: Engine
) -> None:
    """The ordinary case, pinned so the #68 fix cannot null the actor unconditionally:
    the demo owner predates every snapshot, so their restore is attributed to them."""
    _enforce_foreign_keys(demo_file_engine)
    headers = {"Authorization": f"Bearer {demo_file_token}"}

    create_response = await demo_file_client.post("/api/v1/backups", headers=headers)
    assert create_response.status_code == 201
    backup = create_response.json()

    restore_response = await demo_file_client.post(
        f"/api/v1/backups/{backup['id']}/restore", headers=headers
    )
    assert restore_response.status_code == 200

    events = repository.list_audit_events(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID)
    restored = [e for e in events if e.action == "backup.restored"]
    assert len(restored) == 1
    assert restored[0].actor_user_id == fixtures.DEMO_USER_ID
    assert "not present in this snapshot" not in restored[0].summary


@pytest.mark.anyio
async def test_remote_restore_of_a_snapshot_older_than_the_actor_records_no_actor(
    demo_file_client, demo_file_token, demo_file_engine: Engine, demo_file_settings, monkeypatch
) -> None:
    """#68 again on the off-box path: `backup.restored_remote` shares the mechanism,
    so it shares the failure and the fix.

    Driven over HTTP since #75: the route used to be shadowed by the earlier-declared
    `POST /backups/{backup_id}/restore`, so this had to call the handler directly.
    """
    _enforce_foreign_keys(demo_file_engine)
    owner_headers = {"Authorization": f"Bearer {demo_file_token}"}

    create_response = await demo_file_client.post("/api/v1/backups", headers=owner_headers)
    assert create_response.status_code == 201
    backup = create_response.json()
    # The archive the share would hand back is the one the box just wrote. The
    # destination is configured after the backup so nothing tries a real upload.
    archive = (Path(demo_file_settings.backup_dir) / f"{backup['id']}.enc").read_bytes()
    monkeypatch.setattr(
        smb_backup, "list_inventory", lambda target: smb_backup.SmbInventory((), ())
    )
    monkeypatch.setattr(
        smb_backup,
        "query_capacity",
        lambda target: smb_backup.SmbCapacity(10_000_000, 9_000_000, 9_000_000),
    )
    config_response = await demo_file_client.put(
        "/api/v1/backups/config",
        headers=owner_headers,
        json={
            "frequency": "daily",
            "smb_host": "nas.invalid",
            "smb_share": "backups",
            "smb_username": "backup-user",
            "smb_password": "not-a-real-password",
        },
    )
    assert config_response.status_code == 200

    filename = f"{backup['id']}.enc"
    monkeypatch.setattr(
        smb_backup,
        "list_backups",
        lambda target: [
            {"filename": filename, "size_bytes": len(archive), "modified_at": time.time()}
        ],
    )
    monkeypatch.setattr(smb_backup, "download", lambda target, name: archive)

    _, newcomer_token = await _member_who_joined_after_the_snapshot(
        demo_file_client, demo_file_engine
    )

    restore_response = await demo_file_client.post(
        "/api/v1/backups/remote/restore",
        headers={"Authorization": f"Bearer {newcomer_token}"},
        json={"filename": filename},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["writable"] is True

    events = repository.list_audit_events(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID)
    restored = [e for e in events if e.action == "backup.restored_remote"]
    assert len(restored) == 1
    assert restored[0].actor_user_id is None
    assert "not present in this snapshot" in restored[0].summary


@pytest.mark.anyio
async def test_remote_restore_route_is_not_shadowed_by_the_backup_id_route(
    demo_file_client, demo_file_token
) -> None:
    """#75: `POST /backups/remote/restore` has to be declared BEFORE
    `POST /backups/{backup_id}/restore`, or Starlette matches the parameterised route
    first with backup_id="remote" and every caller gets "Backup not found".

    This is the box-rebuild path — the restore you reach for when there are no local
    backup rows at all — so it can only be proved over HTTP. Calling the handler
    directly passes whatever the routing table says.

    No destination is configured here, so the remote handler answers 400 "No Synology
    backup destination is configured". That 400 is the assertion: it can only come
    from the handler this request was meant to reach.
    """
    response = await demo_file_client.post(
        "/api/v1/backups/remote/restore",
        headers={"Authorization": f"Bearer {demo_file_token}"},
        json={"filename": "does-not-matter.enc"},
    )

    assert response.status_code != 404, "shadowed by /backups/{backup_id}/restore"
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "No Synology backup destination is configured"


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


@pytest.mark.anyio
async def test_backup_config_exposes_global_policy_and_password_write_semantics(
    demo_file_client,
    demo_file_token,
    demo_file_engine: Engine,
) -> None:
    headers = {"Authorization": f"Bearer {demo_file_token}"}

    initial = await demo_file_client.get("/api/v1/backups/config", headers=headers)
    assert initial.status_code == 200
    body = initial.json()
    assert body["local_retention"]["mode"] == "tiered"
    assert body["offbox_retention"]["mode"] == "keep_all"
    assert body["offbox_retention"]["weekly_until_days"] is None
    assert body["max_bytes"] is None
    assert body["updated_at"]
    assert "smb_password" not in body

    supplied = await demo_file_client.put(
        "/api/v1/backups/config", headers=headers, json={"smb_password": "top-secret"}
    )
    assert supplied.status_code == 200
    assert supplied.json()["has_password"] is True

    preserved = await demo_file_client.put(
        "/api/v1/backups/config", headers=headers, json={"smb_password": None}
    )
    assert preserved.status_code == 200
    assert preserved.json()["has_password"] is True

    cleared = await demo_file_client.put(
        "/api/v1/backups/config", headers=headers, json={"smb_password": ""}
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_password"] is False

    events = repository.list_audit_events(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID)
    summaries = " ".join(event.summary for event in events)
    assert "top-secret" not in summaries


@pytest.mark.anyio
async def test_backup_config_activation_is_cas_guarded_and_alias_compatible(
    demo_file_client, demo_file_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_file_token}"}
    legacy = await demo_file_client.put(
        "/api/v1/backups/config", headers=headers, json={"max_bytes": 123456}
    )
    assert legacy.status_code == 200
    assert legacy.json()["local_max_bytes"] == 123456
    assert legacy.json()["offbox_max_bytes"] == 123456
    assert legacy.json()["max_bytes"] == 123456

    token = legacy.json()["updated_at"]
    activated = await demo_file_client.put(
        "/api/v1/backups/config",
        headers=headers,
        json={
            "local_retention": {
                "mode": "tiered",
                "keep_all_days": 2,
                "daily_until_days": 10,
                "weekly_until_days": 60,
            },
            "local_max_bytes": 222222,
            "expected_updated_at": token,
            "confirm_retention_policy": True,
        },
    )
    assert activated.status_code == 200
    assert activated.json()["retention_review_required"] is False
    assert activated.json()["retention_activated_at"] is not None
    assert activated.json()["max_bytes"] is None

    stale = await demo_file_client.put(
        "/api/v1/backups/config",
        headers=headers,
        json={
            "frequency": "weekly",
            "expected_updated_at": token,
        },
    )
    assert stale.status_code == 409
    current = await demo_file_client.get("/api/v1/backups/config", headers=headers)
    assert current.json()["frequency"] != "weekly"


@pytest.mark.anyio
async def test_backup_status_is_one_qualified_snapshot_and_requires_box_right(
    demo_file_client, demo_file_token
) -> None:
    unauthorized = await demo_file_client.get("/api/v1/backups/status")
    assert unauthorized.status_code == 401
    viewer_login = await demo_file_client.post(
        "/api/v1/auth/sessions",
        json={
            "email": fixtures.DEMO_VIEWER_EMAIL,
            "password": fixtures.DEMO_VIEWER_PASSWORD,
        },
    )
    assert viewer_login.status_code == 201
    viewer_token = viewer_login.json()["access_token"]
    forbidden = await demo_file_client.get(
        "/api/v1/backups/status",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert forbidden.status_code == 403

    response = await demo_file_client.get(
        "/api/v1/backups/status",
        headers={"Authorization": f"Bearer {demo_file_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verification_scope"] == "inventory_read_probe"
    assert body["local"]["as_of"] == body["as_of"]
    assert body["offbox"]["as_of"] == body["as_of"]
    assert body["offbox"]["status"] == "not_configured"
    assert body["offbox"]["probe_status"] == "unavailable"
    assert body["offbox"]["readable_archive_count"] is None


@pytest.mark.anyio
async def test_remote_list_distinguishes_unavailable_from_empty(
    demo_file_client,
    demo_file_token,
    demo_file_engine: Engine,
    demo_file_settings,
    monkeypatch,
) -> None:
    headers = {"Authorization": f"Bearer {demo_file_token}"}
    encrypted = banksync.encrypt_credential(demo_file_settings, "not-a-real-password")
    repository.update_backup_settings(
        demo_file_engine,
        {
            "smb_host": "nas.invalid",
            "smb_share": "backups",
            "smb_username": "backup-user",
            "smb_password_encrypted": encrypted,
        },
        expected_updated_at=None,
    )
    monkeypatch.setattr(
        smb_backup,
        "list_inventory",
        lambda target: (_ for _ in ()).throw(
            smb_backup.SmbInventoryError(
                "destination_unreachable", "The Synology inventory is unavailable."
            )
        ),
    )

    response = await demo_file_client.get("/api/v1/backups/remote", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["backups"] == []
    assert response.json()["reason"] == "The Synology inventory is unavailable."
    datetime.fromisoformat(response.json()["as_of"])


@pytest.mark.anyio
async def test_destination_check_reports_capacity(
    demo_file_client, demo_file_token, monkeypatch
) -> None:
    monkeypatch.setattr(smb_backup, "verify", lambda target: (True, None))
    monkeypatch.setattr(
        smb_backup,
        "query_capacity",
        lambda target: smb_backup.SmbCapacity(20_000_000_000, 10_000_000_000, 10_000_000_000),
    )
    response = await demo_file_client.post(
        "/api/v1/backups/destination-check",
        headers={"Authorization": f"Bearer {demo_file_token}"},
        json={
            "smb_host": "nas.invalid",
            "smb_share": "backups",
            "smb_username": "backup-user",
            "smb_password": "not-a-real-password",
        },
    )

    assert response.status_code == 200
    assert response.json()["writable"] is True
    assert response.json()["capacity"]["status"] in ("ok", "warning")
    assert response.json()["capacity"]["available_bytes"] == 10_000_000_000
