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
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
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
async def test_apply_rejects_nonexistent_hub_model(demo_engine, monkeypatch) -> None:
    """M66: a typo'd id is refused BEFORE any container is touched."""
    monkeypatch.setattr(
        ai_runtime_module, "_hf_model_exists", lambda hub, mid: mid != "Qwen/Qwen2.5-VL-8B-Instruct"
    )
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    resp = await client.post(
        "/api/v1/ai/runtime/apply",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "main_model": "Qwen/Qwen3-Next-80B-A3B-Instruct-FP8",
            "vision_model": "Qwen/Qwen2.5-VL-8B-Instruct",
        },
    )
    assert resp.status_code == 422
    assert "Qwen/Qwen2.5-VL-8B-Instruct" in resp.json()["error"]["message"]
    await client.aclose()


@pytest.mark.anyio
async def test_apply_proceeds_when_hub_unreachable(demo_engine, monkeypatch) -> None:
    """Offline-tolerant: an unreachable hub must not block the swap."""

    def fake_post(url, json=None, timeout=None):
        return httpx.Response(
            202,
            json={"state": "running", "main_model": json["main_model"], "vision_model": None},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(ai_runtime_module.httpx, "post", fake_post)
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: None)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    resp = await client.post(
        "/api/v1/ai/runtime/apply",
        headers={"Authorization": f"Bearer {token}"},
        json={"main_model": "Qwen/Qwen2.5-14B-Instruct"},
    )
    assert resp.status_code == 202
    await client.aclose()


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


# --- M48: live quick-filter lists (optional q, pipeline, limit) -----------------


@pytest.mark.anyio
async def test_search_without_q_fetches_top_downloads(demo_client, demo_token, monkeypatch) -> None:
    captured: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        captured.append(dict(params))
        return httpx.Response(
            200,
            json=[{"modelId": "Qwen/Qwen2.5-7B-Instruct", "gated": False}],
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/search?pipeline=text-generation&limit=25",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 200
    # Only the requested pipeline is queried; no search term is sent.
    assert len(captured) == 1
    assert captured[0]["pipeline_tag"] == "text-generation"
    assert captured[0]["limit"] == 25
    assert "search" not in captured[0]


@pytest.mark.anyio
async def test_search_pipeline_validation_and_limit_clamp(
    demo_client, demo_token, monkeypatch
) -> None:
    captured: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        captured.append(dict(params))
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)

    bad = await demo_client.get(
        "/api/v1/ai/models/search?pipeline=bogus",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert bad.status_code == 422

    clamped = await demo_client.get(
        "/api/v1/ai/models/search?pipeline=image-text-to-text&limit=999",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert clamped.status_code == 200
    assert captured[-1]["limit"] == 30  # clamped to the max


# --- M49: quantization-aware size estimates -------------------------------------


@pytest.mark.anyio
async def test_search_estimates_respect_quant_markers(demo_client, demo_token, monkeypatch) -> None:
    def fake_get(url, params=None, timeout=None):
        payload = (
            [
                {"modelId": "Qwen/Qwen3.6-35B-A3B-FP8", "gated": False},
                {"modelId": "Qwen/Qwen2.5-32B-Instruct-AWQ", "gated": False},
                {"modelId": "Qwen/Qwen2.5-14B-Instruct", "gated": False},
            ]
            if params["pipeline_tag"] == "text-generation"
            else []
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/search?q=qwen", headers={"Authorization": f"Bearer {demo_token}"}
    )
    models = {m["id"]: m for m in resp.json()["models"]}

    # FP8 ~= 1.1 GB/B, not the bf16 2.1 that doubled the screenshot's estimate.
    assert models["Qwen/Qwen3.6-35B-A3B-FP8"]["est_memory_gb"] == round(35 * 1.1)
    assert "8-bit" in models["Qwen/Qwen3.6-35B-A3B-FP8"]["notes"]
    # AWQ ~= 0.65 GB/B.
    assert models["Qwen/Qwen2.5-32B-Instruct-AWQ"]["est_memory_gb"] == round(32 * 0.65)
    assert "4-bit" in models["Qwen/Qwen2.5-32B-Instruct-AWQ"]["notes"]
    # Unquantized stays bf16.
    assert models["Qwen/Qwen2.5-14B-Instruct"]["est_memory_gb"] == round(14 * 2.1)
    assert "bf16" in models["Qwen/Qwen2.5-14B-Instruct"]["notes"]


@pytest.mark.anyio
async def test_catalog_includes_the_72b_vision_options(demo_client, demo_token) -> None:
    resp = await demo_client.get(
        "/api/v1/ai/models", headers={"Authorization": f"Bearer {demo_token}"}
    )
    models = {m["id"]: m for m in resp.json()["models"]}
    assert models["Qwen/Qwen2.5-VL-72B-Instruct"]["supports_vision"] is True
    awq = models["Qwen/Qwen2.5-VL-72B-Instruct-AWQ"]
    assert awq["supports_vision"] is True
    assert awq["est_memory_gb"] == 45  # fits ~120GB unified boxes


@pytest.mark.anyio
async def test_apply_collapses_duplicate_main_and_vision(demo_engine, monkeypatch) -> None:
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["json"] = json
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
        json={
            "main_model": "Qwen/Qwen2.5-VL-32B-Instruct",
            "vision_model": "Qwen/Qwen2.5-VL-32B-Instruct",
        },
    )
    assert resp.status_code == 202
    # One instance, not two: the duplicate vision id was dropped.
    assert calls["json"] == {
        "main_model": "Qwen/Qwen2.5-VL-32B-Instruct",
        "vision_model": None,
    }
    await client.aclose()


@pytest.mark.anyio
async def test_catalog_offers_verified_dual_capable_models(demo_client, demo_token) -> None:
    """M52 follow-up: Qwen3-VL renders tools (verified) — the all-in-one filter
    must have real content: vision AND tool calling in one entry."""
    resp = await demo_client.get(
        "/api/v1/ai/models", headers={"Authorization": f"Bearer {demo_token}"}
    )
    duals = [
        m for m in resp.json()["models"] if m["supports_vision"] and m["tool_parser"]
    ]
    assert any(m["id"] == "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8" for m in duals)
    assert all("Qwen2.5-VL" not in m["id"] for m in duals)  # 2.5-VL stays tool-less


@pytest.mark.anyio
async def test_deep_search_fans_out_hinted_queries(demo_client, demo_token, monkeypatch) -> None:
    """M53: deep=true issues size/quant-hinted queries so big-but-unpopular
    models enter the pool (HF cannot sort by parameter count)."""
    captured: list[tuple[str, str]] = []

    def fake_get(url, params=None, timeout=None):
        captured.append((params["pipeline_tag"], params.get("search", "")))
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/search?pipeline=text-generation&deep=true&q=qwen",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 200
    searches = {q for _pipe, q in captured}
    # The user's query plus every hint, one pipeline only.
    assert searches == {"qwen", "", "AWQ", "FP8", "70B", "72B", "90B", "110B", "A22B"}
    assert all(pipe == "text-generation" for pipe, _q in captured)


@pytest.mark.anyio
async def test_search_drops_unservable_formats(demo_client, demo_token, monkeypatch) -> None:
    """M54: MLX/GGUF/bnb repos cannot run on vLLM — never enter the pool."""

    def fake_get(url, params=None, timeout=None):
        payload = [
            {"modelId": "mlx-community/Qwen1.5-110B-Chat-4bit", "gated": False},
            {"modelId": "unsloth/Llama-3.2-90B-Vision-bnb-4bit", "gated": False},
            {"modelId": "bartowski/Some-72B-GGUF", "gated": False},
            {"modelId": "Qwen/Qwen2.5-72B-Instruct-AWQ", "gated": False},
        ]
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/search?q=72b&pipeline=text-generation",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    ids = [m["id"] for m in resp.json()["models"]]
    assert ids == ["Qwen/Qwen2.5-72B-Instruct-AWQ"]


@pytest.mark.anyio
async def test_catalog_lists_the_text_72b_awq(demo_client, demo_token) -> None:
    resp = await demo_client.get(
        "/api/v1/ai/models", headers={"Authorization": f"Bearer {demo_token}"}
    )
    models = {m["id"]: m for m in resp.json()["models"]}
    entry = models["Qwen/Qwen2.5-72B-Instruct-AWQ"]
    assert entry["role"] == "main" and entry["tool_parser"] == "hermes"
    assert entry["est_memory_gb"] == 47
