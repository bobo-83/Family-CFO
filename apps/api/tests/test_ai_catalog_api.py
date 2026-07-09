import pytest


@pytest.mark.anyio
async def test_model_catalog_lists_main_and_vision_options(demo_client, demo_token) -> None:
    resp = await demo_client.get(
        "/api/v1/ai/models", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert resp.status_code == 200
    models = resp.json()["models"]
    roles = {m["role"] for m in models}
    assert {"main", "vision", "both"} <= roles
    default = next(m for m in models if m["id"] == "Qwen/Qwen2.5-32B-Instruct")
    assert default["supports_vision"] is False
    assert default["est_memory_gb"] > 0 and default["est_disk_gb"] > 0
    vl_main = next(m for m in models if m["role"] == "both")
    assert vl_main["supports_vision"] is True


@pytest.mark.anyio
async def test_hardware_profile_reports_disk_and_memory(demo_client, demo_token) -> None:
    resp = await demo_client.get(
        "/api/v1/ai/hardware", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["disk_free_gb"] > 0
    assert body["system_memory_gb"] is None or body["system_memory_gb"] > 0
    # No env override in tests -> GPU memory unknown.
    assert body["gpu_memory_gb"] is None
    assert body["source"] == "system"


@pytest.mark.anyio
async def test_hardware_profile_uses_env_gpu_value(demo_client, demo_token, monkeypatch) -> None:
    monkeypatch.setenv("FAMILY_CFO_GPU_MEMORY_GB", "128")
    resp = await demo_client.get(
        "/api/v1/ai/hardware", headers={"Authorization": f"Bearer {demo_token}"}
    )
    body = resp.json()
    assert body["gpu_memory_gb"] == 128.0
    assert body["source"] == "env"


@pytest.mark.anyio
async def test_endpoints_require_auth(demo_client) -> None:
    assert (await demo_client.get("/api/v1/ai/models")).status_code == 401
    assert (await demo_client.get("/api/v1/ai/hardware")).status_code == 401


@pytest.mark.anyio
async def test_status_reports_vision_enabled_flag(demo_client, demo_token) -> None:
    resp = await demo_client.get(
        "/api/v1/ai/runtime/status", headers={"Authorization": f"Bearer {demo_token}"}
    )
    # Default test settings: no vision configured.
    assert resp.json()["vision_enabled"] is False
