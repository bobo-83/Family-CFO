import pytest


@pytest.mark.anyio
async def test_generate_report_requires_authentication(demo_client) -> None:
    response = await demo_client.post("/api/v1/reports/generate", json={"report_type": "weekly"})

    assert response.status_code == 401


@pytest.mark.anyio
async def test_generate_and_list_and_get_report(demo_client, demo_token) -> None:
    generate_response = await demo_client.post(
        "/api/v1/reports/generate",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"report_type": "weekly"},
    )
    assert generate_response.status_code == 201
    report = generate_response.json()
    assert report["report_type"] == "weekly"
    assert report["explanation_source"] == "deterministic_stub"
    assert "net_cash_flow" in report["summary"]

    list_response = await demo_client.get(
        "/api/v1/reports", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert list_response.status_code == 200
    assert any(r["id"] == report["id"] for r in list_response.json()["reports"])

    get_response = await demo_client.get(
        f"/api/v1/reports/{report['id']}", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == report["id"]


@pytest.mark.anyio
async def test_generate_report_is_idempotent_via_api(demo_client, demo_token) -> None:
    first = await demo_client.post(
        "/api/v1/reports/generate",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"report_type": "monthly"},
    )
    second = await demo_client.post(
        "/api/v1/reports/generate",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"report_type": "monthly"},
    )

    assert first.json()["id"] == second.json()["id"]

    list_response = await demo_client.get(
        "/api/v1/reports", headers={"Authorization": f"Bearer {demo_token}"}
    )
    monthly_reports = [r for r in list_response.json()["reports"] if r["report_type"] == "monthly"]
    assert len(monthly_reports) == 1


@pytest.mark.anyio
async def test_viewer_cannot_generate_report(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/reports/generate",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
        json={"report_type": "weekly"},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_viewer_can_list_and_read_reports(demo_client, demo_token, demo_viewer_token) -> None:
    generate_response = await demo_client.post(
        "/api/v1/reports/generate",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"report_type": "weekly"},
    )
    report_id = generate_response.json()["id"]

    list_response = await demo_client.get(
        "/api/v1/reports", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert list_response.status_code == 200

    get_response = await demo_client.get(
        f"/api/v1/reports/{report_id}", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert get_response.status_code == 200


@pytest.mark.anyio
async def test_get_unknown_report_returns_404(demo_client, demo_token) -> None:
    response = await demo_client.get(
        "/api/v1/reports/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {demo_token}"},
    )

    assert response.status_code == 404
