import httpx
import pytest

from family_cfo_api.api import ai_runtime as ai_runtime_module
from family_cfo_api.config import Settings
from family_cfo_api.main import create_app

_KEY = "jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY="


def _settings(**overrides) -> Settings:
    base = dict(
        version="0.1.0",
        health_check_database=False,
        backup_encryption_key=_KEY,
        model_manager_url="http://model-manager:8000",
    )
    base.update(overrides)
    return Settings(**base)


async def _owner_client_token(app):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    login = await client.post(
        "/api/v1/auth/sessions",
        json={"email": "demo@family-cfo.local", "password": "demo-password-123"},
    )
    return client, login.json()["access_token"]


@pytest.mark.anyio
async def test_search_maps_hf_results_with_estimates(demo_client, demo_token, monkeypatch) -> None:
    def fake_get(url, params=None, timeout=None):
        payload = (
            [{"modelId": "Qwen/Qwen2.5-14B-Instruct", "gated": False}]
            if params["pipeline_tag"] == "text-generation"
            else [{"modelId": "Qwen/Qwen2.5-VL-7B-Instruct", "gated": False}]
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/search?q=qwen", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert resp.status_code == 200
    models = {m["id"]: m for m in resp.json()["models"]}
    main = models["Qwen/Qwen2.5-14B-Instruct"]
    assert main["parameters_b"] == 14 and main["est_memory_gb"] == 29
    assert main["supports_vision"] is False
    vl = models["Qwen/Qwen2.5-VL-7B-Instruct"]
    assert vl["supports_vision"] is True
    assert "Estimated" in main["notes"]


@pytest.mark.anyio
async def test_search_returns_503_when_hf_unreachable(demo_client, demo_token, monkeypatch) -> None:
    def fake_get(url, params=None, timeout=None):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/search?q=qwen", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_apply_forwards_to_manager_and_updates_config(demo_engine, monkeypatch) -> None:
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"], calls["json"] = url, json
        return httpx.Response(
            202,
            json={"state": "running", "main_model": json["main_model"], "vision_model": None},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(ai_runtime_module.httpx, "post", fake_post)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    resp = await client.post(
        "/api/v1/ai/runtime/apply",
        headers={"Authorization": f"Bearer {token}"},
        json={"main_model": "Qwen/Qwen2.5-14B-Instruct", "vision_model": None},
    )
    assert resp.status_code == 202
    assert resp.json()["state"] == "running"
    assert calls["url"] == "http://model-manager:8000/swap"

    # Household config now targets the new model (mismatch/status stay honest).
    config = await client.get("/api/v1/ai/runtime", headers={"Authorization": f"Bearer {token}"})
    assert config.json()["model"] == "Qwen/Qwen2.5-14B-Instruct"
    await client.aclose()


@pytest.mark.anyio
async def test_apply_rejects_bad_ids_and_missing_manager(demo_engine, monkeypatch) -> None:
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    bad = await client.post(
        "/api/v1/ai/runtime/apply",
        headers={"Authorization": f"Bearer {token}"},
        json={"main_model": "not-a-repo-id; rm -rf /"},
    )
    assert bad.status_code == 422
    await client.aclose()

    app2 = create_app(_settings(model_manager_url=""), engine=demo_engine)
    client2, token2 = await _owner_client_token(app2)
    resp = await client2.post(
        "/api/v1/ai/runtime/apply",
        headers={"Authorization": f"Bearer {token2}"},
        json={"main_model": "Qwen/Qwen2.5-14B-Instruct"},
    )
    assert resp.status_code == 503
    await client2.aclose()


@pytest.mark.anyio
async def test_apply_requires_owner(demo_client, demo_viewer_token) -> None:
    resp = await demo_client.post(
        "/api/v1/ai/runtime/apply",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
        json={"main_model": "Qwen/Qwen2.5-14B-Instruct"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_apply_status_relays_manager_state(demo_engine, monkeypatch) -> None:
    def fake_get(url, timeout=None):
        return httpx.Response(
            200,
            json={"state": "succeeded", "main_model": "Qwen/Qwen2.5-14B-Instruct", "log_tail": "ok"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    resp = await client.get(
        "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.json()["state"] == "succeeded"
    await client.aclose()
