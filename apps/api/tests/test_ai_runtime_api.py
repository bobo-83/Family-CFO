import pytest

from family_cfo_api import fixtures, repository
from family_cfo_api.api import ai_runtime as ai_runtime_module
from family_cfo_api.main import create_app
from tests._test_keys import TEST_FERNET_KEY
from tests.test_ai_apply_api import _owner_client_token, _settings

_KEY = TEST_FERNET_KEY


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
        "cluster_enabled": False,
    }


@pytest.fixture(autouse=True)
def _reset_deployment_state_cache():
    # The guard's state cache is process-global with a 1 s TTL; tests must not
    # read a previous test's answer.
    ai_runtime_module._deployment_state_cache = None
    yield
    ai_runtime_module._deployment_state_cache = None


async def _put(client, token, payload):
    return await client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


_MANAGED_PUT = {
    "provider": "vllm",
    "base_url": "http://vllm:8000",
    "model": "Qwen/Qwen2.5-14B-Instruct",
    "enabled": True,
}


@pytest.mark.anyio
async def test_put_of_the_managed_selection_is_refused_while_a_swap_is_in_flight(
    demo_engine, fake_manager, monkeypatch
) -> None:
    fake_manager.seed_job(state="verifying", verified_at=None)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _put(client, token, _MANAGED_PUT)
        assert resp.status_code == 409
        assert "replacement is in progress" in resp.json()["error"]["message"]
        config = await client.get(
            "/api/v1/ai/runtime", headers={"Authorization": f"Bearer {token}"}
        )
        assert config.json()["model"] != "Qwen/Qwen2.5-14B-Instruct"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_put_fails_closed_when_the_manager_state_is_unknown(
    demo_engine, fake_manager
) -> None:
    fake_manager.unreachable = True
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _put(client, token, _MANAGED_PUT)
        assert resp.status_code == 503
        assert "fail closed" in resp.json()["error"]["message"]
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_put_of_a_non_managed_runtime_is_not_gated(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """A different provider/base_url and no consent change is the household's
    own business — the in-flight replacement does not lock it."""
    fake_manager.seed_job(state="verifying")
    settings = _settings(ai_allowed_base_urls=("http://vllm:8000", "http://ollama.local:11434"))
    app = create_app(settings, engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _put(
            client,
            token,
            {
                "provider": "ollama",
                "base_url": "http://ollama.local:11434",
                "model": "b",
                "enabled": True,
            },
        )
        assert resp.status_code == 200
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_cluster_consent_change_is_gated_even_off_the_managed_url(
    demo_engine, fake_manager
) -> None:
    """The toggle is deployment-wide (ADR 0071): flipping it mid-swap would
    change what a concurrently resolving plan may use."""
    repository.upsert_ai_runtime_config(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        provider="ollama",
        base_url="http://ollama.local:11434",
        model="b",
        enabled=True,
        cluster_enabled=False,
    )
    fake_manager.seed_job(state="verifying")
    settings = _settings(ai_allowed_base_urls=("http://vllm:8000", "http://ollama.local:11434"))
    app = create_app(settings, engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _put(
            client,
            token,
            {
                "provider": "ollama",
                "base_url": "http://ollama.local:11434",
                "model": "b",
                "enabled": True,
                "cluster_enabled": True,
            },
        )
        assert resp.status_code == 409
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_put_proceeds_when_no_replacement_is_in_flight(demo_engine, fake_manager) -> None:
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _put(client, token, _MANAGED_PUT)
        assert resp.status_code == 200
        assert resp.json()["model"] == "Qwen/Qwen2.5-14B-Instruct"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_every_config_put_takes_the_deployment_lock(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """Even ungated PUTs serialize with the receipt commit on the SAME fixed
    advisory key — a stale full-object overwrite must be impossible."""
    from unittest.mock import Mock

    spy = Mock(wraps=repository.upsert_ai_runtime_config)
    monkeypatch.setattr(repository, "upsert_ai_runtime_config", spy)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _put(client, token, _MANAGED_PUT)
        assert resp.status_code == 200
        assert spy.call_args.kwargs["deployment_locked"] is True
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_the_guard_state_is_cached_for_one_second(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """The <=1 s cache is deliberate (bounded added latency); a test-injected
    clock proves the boundary without sleeping."""
    now = {"value": 1000.0}
    monkeypatch.setattr(ai_runtime_module, "monotonic", lambda: now["value"])
    job_id = fake_manager.seed_job(state="verifying")
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        busy = await _put(client, token, _MANAGED_PUT)
        assert busy.status_code == 409

        # The swap finishes, but the cache still says busy within its TTL.
        fake_manager.jobs.pop(job_id)
        fake_manager.order.remove(job_id)
        now["value"] = 1000.5
        assert (await _put(client, token, _MANAGED_PUT)).status_code == 409

        # Past the TTL the guard asks the manager again and lets the PUT pass.
        now["value"] = 1001.5
        assert (await _put(client, token, _MANAGED_PUT)).status_code == 200
    finally:
        await client.aclose()
