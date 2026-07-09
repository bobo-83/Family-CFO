import pytest

from family_cfo_api.api import ai_runtime as ai_runtime_module


@pytest.mark.anyio
async def test_status_reports_disabled_by_default(demo_client, demo_token) -> None:
    resp = await demo_client.get(
        "/api/v1/ai/runtime/status", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["ready"] is False
    assert "deterministic" in body["detail"]


@pytest.mark.anyio
async def test_status_reports_ready_when_runtime_serves(
    demo_client, demo_token, monkeypatch
) -> None:
    # Enable a runtime for the household (base_url is in the test allowlist).
    put = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm:8000",
            "model": "Qwen/Qwen2.5-32B-Instruct",
            "enabled": True,
        },
    )
    assert put.status_code == 200

    monkeypatch.setattr(
        ai_runtime_module,
        "_probe_served_model",
        lambda base_url: (True, "Qwen/Qwen2.5-32B-Instruct"),
    )

    resp = await demo_client.get(
        "/api/v1/ai/runtime/status", headers={"Authorization": f"Bearer {demo_token}"}
    )
    body = resp.json()
    assert body["enabled"] is True
    assert body["ready"] is True
    assert body["served_model"] == "Qwen/Qwen2.5-32B-Instruct"


@pytest.mark.anyio
async def test_status_reports_loading_when_runtime_unreachable(
    demo_client, demo_token, monkeypatch
) -> None:
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
    monkeypatch.setattr(ai_runtime_module, "_probe_served_model", lambda base_url: (False, None))

    resp = await demo_client.get(
        "/api/v1/ai/runtime/status", headers={"Authorization": f"Bearer {demo_token}"}
    )
    body = resp.json()
    assert body["enabled"] is True
    assert body["ready"] is False
    assert "starting up" in body["detail"]
