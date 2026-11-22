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


@pytest.mark.anyio
async def test_runtime_status_observes_serving_not_the_apply_transaction(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """The two statuses answer different questions: `/ai/runtime/status` says
    whether the runtime SERVES; apply durability lives behind
    `/ai/runtime/apply/status`. An in-flight swap must not blur them."""
    from family_cfo_api.main import create_app
    from tests.test_ai_apply_api import _owner_client_token, _settings

    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        # With the manager free, the owner can still set the serving runtime.
        put = await client.put(
            "/api/v1/ai/runtime",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "provider": "vllm",
                "base_url": "http://vllm:8000",
                "model": "Qwen/Qwen2.5-32B-Instruct",
                "enabled": True,
            },
        )
        assert put.status_code == 200

        # A replacement is admitted and verifying: the apply lane is busy...
        monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
        accepted = await client.post(
            "/api/v1/ai/runtime/apply",
            headers={"Authorization": f"Bearer {token}"},
            json={"main_model": "Qwen/Qwen2.5-14B-Instruct"},
        )
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        fake_manager.set_state(job_id, "verifying")

        # ...but the runtime STILL SERVES the old model, and that is what this
        # endpoint reports — the apply transaction never leaks into it.
        monkeypatch.setattr(
            ai_runtime_module,
            "_probe_served_model",
            lambda base_url: (True, "Qwen/Qwen2.5-32B-Instruct"),
        )
        resp = await client.get(
            "/api/v1/ai/runtime/status", headers={"Authorization": f"Bearer {token}"}
        )
        body = resp.json()
        assert body["ready"] is True
        assert body["served_model"] == "Qwen/Qwen2.5-32B-Instruct"

        # The durable apply state is a different, read-only surface.
        apply_status = await client.get(
            "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert apply_status.json()["state"] == "running"
        assert apply_status.json()["lifecycle_state"] == "verifying"
    finally:
        await client.aclose()
