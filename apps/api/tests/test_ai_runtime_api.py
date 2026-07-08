import pytest


@pytest.mark.anyio
async def test_get_ai_runtime_config_requires_authentication(demo_client) -> None:
    response = await demo_client.get("/api/v1/ai/runtime")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_ai_runtime_config_returns_disabled_default_when_unset(
    demo_client, demo_token
) -> None:
    response = await demo_client.get(
        "/api/v1/ai/runtime", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False


@pytest.mark.anyio
async def test_owner_can_update_ai_runtime_config(demo_client, demo_token) -> None:
    response = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm.local:8000",
            "model": "llama-3-8b-instruct",
            "enabled": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["base_url"] == "http://vllm.local:8000"
    assert body["enabled"] is True

    follow_up = await demo_client.get(
        "/api/v1/ai/runtime", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert follow_up.json()["model"] == "llama-3-8b-instruct"


@pytest.mark.anyio
async def test_viewer_cannot_update_ai_runtime_config(demo_client, demo_viewer_token) -> None:
    response = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm.local:8000",
            "model": "x",
            "enabled": True,
        },
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_update_ai_runtime_config_requires_authentication(demo_client) -> None:
    response = await demo_client.put(
        "/api/v1/ai/runtime",
        json={
            "provider": "vllm",
            "base_url": "http://vllm.local:8000",
            "model": "x",
            "enabled": True,
        },
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_put_upserts_existing_config(demo_client, demo_token) -> None:
    first = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm.local:8000",
            "model": "a",
            "enabled": True,
        },
    )
    second = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "ollama",
            "base_url": "http://ollama.local:11434",
            "model": "b",
            "enabled": False,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {
        "provider": "ollama",
        "base_url": "http://ollama.local:11434",
        "model": "b",
        "enabled": False,
    }
