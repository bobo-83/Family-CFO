"""Current API against the prior model-manager wire contract (ADR 0074)."""

import json
from pathlib import Path

import httpx
import pytest

from family_cfo_api.api import ai_runtime as ai_runtime_module
from family_cfo_api.config import Settings
from family_cfo_api.main import create_app
from tests._test_keys import TEST_FERNET_KEY

_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "shared/schemas/model-manager-v1-contract.json").read_text()
)["operations"]


def _settings() -> Settings:
    return Settings(
        health_check_database=False,
        backup_encryption_key=TEST_FERNET_KEY,
        model_manager_url="http://model-manager:8000",
    )


async def _owner_client_token(app):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
    login = await client.post(
        "/api/v1/auth/sessions",
        json={"email": "demo@family-cfo.local", "password": "demo-password-123"},
    )
    return client, login.json()["access_token"]


@pytest.mark.anyio
async def test_current_api_accepts_previous_model_manager_contract(
    demo_engine, monkeypatch
) -> None:
    """Requests stay old-manager-safe and old responses still satisfy this API."""

    def fake_post(url, json=None, timeout=None):
        operation = _FIXTURE["swap"]
        assert url.endswith(operation["path"])
        assert json == operation["request"]
        return httpx.Response(
            202,
            json=operation["response"],
            request=httpx.Request(operation["method"], url),
        )

    def fake_get(url, params=None, timeout=None):
        name = "logs" if url.endswith(_FIXTURE["logs"]["path"]) else "status"
        operation = _FIXTURE[name]
        assert url.endswith(operation["path"])
        if "query" in operation:
            assert params == operation["query"]
        return httpx.Response(
            200,
            json=operation["response"],
            request=httpx.Request(operation["method"], url),
        )

    monkeypatch.setattr(ai_runtime_module.httpx, "post", fake_post)
    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda _hub, _model: True)
    # This test covers the API/sidecar wire, not host sizing. Linux CI has much
    # less system memory than the macOS runner and would otherwise reject the
    # synthetic vision model before the request reaches the mocked manager.
    monkeypatch.setattr(ai_runtime_module, "_vision_slot_gb", lambda _settings: None)

    settings = _settings()
    assert ai_runtime_module._loading_status_from_manager(settings) is not None

    app = create_app(settings, engine=demo_engine)
    client, token = await _owner_client_token(app)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        apply_response = await client.post(
            "/api/v1/ai/runtime/apply",
            headers=headers,
            json=_FIXTURE["swap"]["request"],
        )
        assert apply_response.status_code == 202
        assert apply_response.json() == _FIXTURE["swap"]["response"]

        status_response = await client.get("/api/v1/ai/runtime/apply/status", headers=headers)
        assert status_response.status_code == 200
        assert status_response.json() == _FIXTURE["status"]["response"]
    finally:
        await client.aclose()
