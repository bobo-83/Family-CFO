import pytest


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
