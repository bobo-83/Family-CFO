import logging

import pytest

from family_cfo_api import fixtures, import_processing


@pytest.mark.anyio
async def test_create_import_requires_authentication(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/imports", json={"source_type": "csv", "filename": "statement.csv"}
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_full_csv_import_lifecycle(demo_client, demo_token, demo_engine, demo_settings) -> None:
    create_response = await demo_client.post(
        "/api/v1/imports",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "source_type": "csv",
            "filename": "statement.csv",
            "account_id": fixtures.DEMO_CHECKING_ACCOUNT_ID,
        },
    )
    assert create_response.status_code == 201
    import_id = create_response.json()["id"]
    assert create_response.json()["status"] == "pending"

    csv_content = b"date,amount,description\n2026-02-01,-15.00,Bookstore\n"
    upload_response = await demo_client.post(
        f"/api/v1/imports/{import_id}/file",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("statement.csv", csv_content, "text/csv")},
    )
    assert upload_response.status_code == 202

    # No scheduler runs in tests; invoke the job function directly.
    processed = import_processing.run_pending_imports_once(demo_engine, demo_settings.import_staging_dir)
    assert processed == 1

    list_response = await demo_client.get(
        "/api/v1/imports", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert list_response.status_code == 200
    updated_record = next(r for r in list_response.json()["imports"] if r["id"] == import_id)
    assert updated_record["status"] == "needs_review"

    apply_response = await demo_client.post(
        f"/api/v1/imports/{import_id}/apply", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert apply_response.status_code == 200
    assert apply_response.json()["status"] == "completed"

    transactions_response = await demo_client.get(
        "/api/v1/transactions", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert any(t["merchant"] == "Bookstore" for t in transactions_response.json()["transactions"])


@pytest.mark.anyio
async def test_discard_import_removes_pending_transactions(
    demo_client, demo_token, demo_engine, demo_settings
) -> None:
    create_response = await demo_client.post(
        "/api/v1/imports",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "source_type": "csv",
            "filename": "statement.csv",
            "account_id": fixtures.DEMO_CHECKING_ACCOUNT_ID,
        },
    )
    import_id = create_response.json()["id"]

    csv_content = b"date,amount,description\n2026-02-02,-5.00,Snack Shop\n"
    await demo_client.post(
        f"/api/v1/imports/{import_id}/file",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("statement.csv", csv_content, "text/csv")},
    )
    import_processing.run_pending_imports_once(demo_engine, demo_settings.import_staging_dir)

    discard_response = await demo_client.post(
        f"/api/v1/imports/{import_id}/discard", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert discard_response.status_code == 200
    assert discard_response.json()["status"] == "discarded"

    transactions_response = await demo_client.get(
        "/api/v1/transactions", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert not any(t["merchant"] == "Snack Shop" for t in transactions_response.json()["transactions"])


@pytest.mark.anyio
async def test_viewer_cannot_apply_or_discard_imports(demo_client, demo_token, demo_viewer_token) -> None:
    create_response = await demo_client.post(
        "/api/v1/imports",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"source_type": "csv", "filename": "statement.csv"},
    )
    import_id = create_response.json()["id"]

    apply_response = await demo_client.post(
        f"/api/v1/imports/{import_id}/apply", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert apply_response.status_code == 403

    discard_response = await demo_client.post(
        f"/api/v1/imports/{import_id}/discard", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert discard_response.status_code == 403


@pytest.mark.anyio
async def test_upload_file_for_unknown_import_returns_404(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/imports/00000000-0000-0000-0000-000000000000/file",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("statement.csv", b"date,amount\n", "text/csv")},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_upload_empty_file_returns_400(demo_client, demo_token) -> None:
    create_response = await demo_client.post(
        "/api/v1/imports",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"source_type": "csv", "filename": "statement.csv"},
    )
    import_id = create_response.json()["id"]

    response = await demo_client.post(
        f"/api/v1/imports/{import_id}/file",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("statement.csv", b"", "text/csv")},
    )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_import_upload_never_logs_file_contents(demo_client, demo_token, caplog) -> None:
    unique_marker = "very-unique-merchant-xyz-do-not-log-me"
    csv_content = f"date,amount,description\n2026-03-01,-1.00,{unique_marker}\n".encode()

    create_response = await demo_client.post(
        "/api/v1/imports",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"source_type": "csv", "filename": "statement.csv"},
    )
    import_id = create_response.json()["id"]

    with caplog.at_level(logging.DEBUG):
        response = await demo_client.post(
            f"/api/v1/imports/{import_id}/file",
            headers={"Authorization": f"Bearer {demo_token}"},
            files={"file": ("statement.csv", csv_content, "text/csv")},
        )

    assert response.status_code == 202
    assert unique_marker not in caplog.text
