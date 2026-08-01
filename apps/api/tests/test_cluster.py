"""ADR 0071: paired-second-box detection, combined budget, and the toggle."""

import pytest

from family_cfo_api import ai_catalog


def test_no_peer_declared_means_no_cluster(monkeypatch) -> None:
    monkeypatch.delenv("FAMILY_CFO_CLUSTER_PEER_HOST", raising=False)
    profile = ai_catalog.hardware_profile()
    assert profile["cluster_peer_host"] is None
    assert profile["cluster_peer_reachable"] is False
    assert profile["cluster_memory_gb"] is None


def test_reachable_peer_doubles_the_budget(monkeypatch) -> None:
    monkeypatch.setenv("FAMILY_CFO_CLUSTER_PEER_HOST", "spark2")
    monkeypatch.setenv("FAMILY_CFO_GPU_MEMORY_GB", "119")
    monkeypatch.setattr(ai_catalog, "cluster_peer_status", lambda: ("spark2", True))
    profile = ai_catalog.hardware_profile()
    assert profile["cluster_peer_reachable"] is True
    assert profile["cluster_memory_gb"] == 238.0


def test_unreachable_peer_reports_host_but_no_budget(monkeypatch) -> None:
    monkeypatch.setattr(ai_catalog, "cluster_peer_status", lambda: ("spark2", False))
    profile = ai_catalog.hardware_profile()
    assert profile["cluster_peer_host"] == "spark2"
    assert profile["cluster_peer_reachable"] is False
    assert profile["cluster_memory_gb"] is None


def test_catalog_has_a_cluster_tier() -> None:
    cluster = [m for m in ai_catalog.MODEL_CATALOG if m.min_nodes >= 2]
    assert cluster, "cluster-tier entries missing"
    # Every cluster entry genuinely exceeds one Spark's ~85GB usable budget.
    assert all(m.est_memory_gb > 85 for m in cluster)
    # And single-node entries stay min_nodes=1 so existing pickers are untouched.
    assert any(m.min_nodes == 1 for m in ai_catalog.MODEL_CATALOG)


@pytest.mark.anyio
async def test_cluster_toggle_round_trips_and_survives_model_apply(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    saved = await demo_client.put(
        "/api/v1/ai/runtime",
        headers=headers,
        json={
            "provider": "vllm",
            "base_url": "http://vllm:8000",
            "model": "unsloth/Qwen3.6-35B-A3B-NVFP4",
            "enabled": True,
            "cluster_enabled": True,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["cluster_enabled"] is True

    fetched = await demo_client.get("/api/v1/ai/runtime", headers=headers)
    assert fetched.json()["cluster_enabled"] is True


@pytest.mark.anyio
async def test_hardware_profile_exposes_cluster_fields(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    profile = (await demo_client.get("/api/v1/ai/hardware", headers=headers)).json()
    assert "cluster_peer_reachable" in profile
    assert "cluster_memory_gb" in profile
