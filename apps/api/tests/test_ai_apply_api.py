import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from family_cfo_api import fixtures, models, repository
from family_cfo_api.api import ai_runtime as ai_runtime_module
from family_cfo_api.config import Settings
from family_cfo_api.main import create_app
from tests._fake_manager import DEFAULT_CREDENTIAL
from tests._test_keys import TEST_FERNET_KEY

_KEY = TEST_FERNET_KEY


def _settings(**overrides) -> Settings:
    base = dict(
        version="0.1.0",
        health_check_database=False,
        backup_encryption_key=_KEY,
        model_manager_url="http://model-manager:8000",
        # The fake manager enforces the deployment credential like the real one.
        model_manager_credential=DEFAULT_CREDENTIAL,
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


def _snapshot(engine):
    """Every config row + audit row: refusal paths must not write a single one."""
    with engine.connect() as conn:
        return (
            sorted(
                (dict(r) for r in conn.execute(select(models.ai_runtime_configs)).mappings()),
                key=lambda r: r["household_id"],
            ),
            sorted(
                (dict(r) for r in conn.execute(select(models.audit_events)).mappings()),
                key=lambda r: r["id"],
            ),
        )


def _seed_config(engine, **overrides):
    kwargs = dict(
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        provider="vllm",
        base_url=_settings().ai_default_base_url,
        model="Qwen/Old-Model",
        enabled=True,
    )
    kwargs.update(overrides)
    return repository.upsert_ai_runtime_config(engine, **kwargs)


def _apply(client, token, payload):
    return client.post(
        "/api/v1/ai/runtime/apply",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


def _fresh_iso(offset_seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


_PLAN = {
    "main_model": "Qwen/Qwen2.5-14B-Instruct",
    "main_revision": "8b5e6c1f",
    "vision_model": "Qwen/Qwen2.5-VL-3B-Instruct",
    "vision_revision": "4a2d9e07",
    "photo_mode": "separate",
    "topology": "single",
    "runtime_version": "0.32.0",
    "image_digest": "sha256:" + "0" * 64,
    "context_limit": 8192,
    "concurrency_limit": 1,
    "node_requirements": [
        {
            "node": "primary",
            "memory_gb": 34.5,
            "memory_evidence": "estimated",
            "disk_gb": 29.5,
            "disk_evidence": "verified",
        }
    ],
    "warnings": [],
}


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


# --- one-shot apply admission (issue #170, ADR 0081) ----------------------------
#
# The API admits a validated launch through the manager's durable v2 surface
# and writes NO household settings: the reconciler commits exactly once after
# verified readiness. Tests drive the REAL ModelManagerClient against an
# in-process manager v2 (`fake_manager`), so the credential header, error
# envelope parsing, and 404 semantics are exercised, not mocked away.


@pytest.mark.anyio
async def test_apply_admits_via_manager_v2_and_writes_no_household_settings(
    demo_engine, fake_manager, monkeypatch
) -> None:
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct", "vision_model": None}
        )
        assert resp.status_code == 202
        body = resp.json()
        # The coarse legacy projection rides on top of the full lifecycle.
        assert body["state"] == "running"
        assert body["lifecycle_state"] == "accepted"
        assert body["requested_selection"]["main_model"] == "Qwen/Qwen2.5-14B-Instruct"
        assert body["job_id"] and body["request_id"]

        # One v2 admission with the normalized selection and frozen identity.
        assert len(fake_manager.admission_calls) == 1
        admission = fake_manager.admission_calls[0]
        assert admission["main_model"] == "Qwen/Qwen2.5-14B-Instruct"
        assert admission["vision_model"] is None
        assert admission["photo_mode"] == "auto"
        assert admission["topology"] == "auto"
        assert admission["cluster_consent"] is False
        assert admission["origin"] == "api"
        assert admission["runtime_key"] == "vllm+http://vllm:8000"
        assert admission["initiator_household_id"] == fixtures.DEMO_HOUSEHOLD_ID
        assert admission["initiator_user_id"] == fixtures.DEMO_USER_ID
        assert admission["request_id"] == body["request_id"]

        # One idempotent operational audit, attached to the initiating household.
        with demo_engine.connect() as conn:
            audits = (
                conn.execute(
                    select(models.audit_events).where(
                        models.audit_events.c.action == "ai_runtime.model_apply_started"
                    )
                )
                .mappings()
                .all()
            )
        assert len(audits) == 1
        assert audits[0]["entity_id"] == body["job_id"]
        assert audits[0]["household_id"] == fixtures.DEMO_HOUSEHOLD_ID
        assert audits[0]["actor_user_id"] == fixtures.DEMO_USER_ID

        # NO household settings change on acceptance: a failed candidate must
        # never repoint — the reconciler commits exactly once, after verified
        # readiness (ADR 0081 S3).
        with demo_engine.connect() as conn:
            assert conn.execute(select(models.ai_runtime_configs)).first() is None
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_synthesizes_a_stable_request_id_for_legacy_bodies(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """Shipped clients send no request_id. The server derives the idempotency
    identity from the NORMALIZED request, so a replayed selection maps to the
    SAME job — the manager answers it as a read, never a second launch."""
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    payload = {
        "main_model": "Qwen/Qwen2.5-14B-Instruct",
        "vision_model": "Qwen/Qwen2.5-VL-3B-Instruct",
    }
    try:
        first = await _apply(client, token, payload)
        assert first.status_code == 202
        second = await _apply(client, token, payload)
        assert second.status_code == 202
        assert second.json()["job_id"] == first.json()["job_id"]

        identity = first.json()["request_id"]
        expected = ai_runtime_module._synthesize_request_id(
            ai_runtime_module._NormalizedSelection(
                main_model="Qwen/Qwen2.5-14B-Instruct",
                vision_model="Qwen/Qwen2.5-VL-3B-Instruct",
                photo_mode="auto",
                topology="auto",
            )
        )
        assert identity == str(expected)
        assert len(fake_manager.admission_calls) == 2
        assert all(call["request_id"] == identity for call in fake_manager.admission_calls)
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_forwards_an_explicit_request_id(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """New clients send one UUID per user confirmation; it rides through."""
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    request_id = str(uuid.uuid4())
    try:
        resp = await _apply(
            client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct", "request_id": request_id}
        )
        assert resp.status_code == 202
        assert resp.json()["request_id"] == request_id
        assert fake_manager.admission_calls[0]["request_id"] == request_id
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("refusal", "payload", "expected_status", "expected_code"),
    [
        pytest.param(
            (409, "swap_in_progress", "another apply owns the deployment lock"),
            {"main_model": "Qwen/Qwen2.5-14B-Instruct"},
            409,
            "swap_in_progress",
            id="manager_swap_in_progress",
        ),
        pytest.param(
            (422, "model_not_found", "'Qwen/Gone-Model' was not found"),
            {"main_model": "Qwen/Qwen2.5-14B-Instruct"},
            422,
            "model_not_found",
            id="manager_model_not_found",
        ),
        pytest.param(
            None,
            {"main_model": "not-a-repo-id; rm -rf /"},
            422,
            "invalid_model_id",
            id="preflight_invalid_id",
        ),
    ],
)
async def test_apply_rejection_leaves_configs_and_audit_untouched(
    demo_engine, fake_manager, monkeypatch, refusal, payload, expected_status, expected_code
) -> None:
    """Issues #167/#170: no refusal path may write a single row."""
    if refusal is not None:
        fake_manager.admission_refusal = refusal
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    _seed_config(demo_engine, cluster_enabled=True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        # Login has its own audit record; compare only after authentication.
        before = _snapshot(demo_engine)
        resp = await _apply(client, token, payload)
        assert resp.status_code == expected_status
        assert resp.json()["error"]["code"] == expected_code
        assert _snapshot(demo_engine) == before
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_fails_closed_without_a_manager_or_credential(
    demo_engine, fake_manager
) -> None:
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    bad = await _apply(client, token, {"main_model": "not-a-repo-id; rm -rf /"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_model_id"
    await client.aclose()

    # No manager configured: nothing may mutate, apply says so (spec 04).
    app2 = create_app(_settings(model_manager_url=""), engine=demo_engine)
    client2, token2 = await _owner_client_token(app2)
    resp = await _apply(client2, token2, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "manager_unavailable"
    await client2.aclose()

    # No deployment credential configured: v2 mutators are impossible, so
    # apply fails closed instead of admitting an unacknowledgeable job.
    app3 = create_app(_settings(model_manager_credential=""), engine=demo_engine)
    client3, token3 = await _owner_client_token(app3)
    resp = await _apply(client3, token3, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "manager_unavailable"
    assert fake_manager.calls == []  # the manager was never contacted
    await client3.aclose()


@pytest.mark.anyio
async def test_apply_rejects_nonexistent_hub_model(demo_engine, fake_manager, monkeypatch) -> None:
    """M66: a typo'd id is refused BEFORE any container is touched."""
    monkeypatch.setattr(
        ai_runtime_module, "_hf_model_exists", lambda hub, mid: mid != "Qwen/Qwen2.5-VL-8B-Instruct"
    )
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client,
            token,
            {
                "main_model": "Qwen/Qwen3-Next-80B-A3B-Instruct-FP8",
                "vision_model": "Qwen/Qwen2.5-VL-8B-Instruct",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "model_not_found"
        assert "Qwen/Qwen2.5-VL-8B-Instruct" in resp.json()["error"]["message"]
        assert fake_manager.calls == []  # refused before any manager contact
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_proceeds_when_hub_unreachable(demo_engine, fake_manager, monkeypatch) -> None:
    """Offline-tolerant: an unreachable hub must not block the swap — the
    manager resolves pinned metadata and refuses with model_not_found itself."""
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: None)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
        assert resp.status_code == 202
    finally:
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
@pytest.mark.parametrize(
    ("manager_status", "expected_status"),
    [(404, 404), (409, 409), (422, 422), (500, 503)],
    ids=["not_found", "conflict", "invalid", "internal_becomes_unavailable"],
)
async def test_apply_surfaces_manager_refusal_codes(
    demo_engine, fake_manager, monkeypatch, manager_status, expected_status
) -> None:
    """The manager's stable code and message pass through the public envelope;
    only the HTTP class is normalized (unexpected failures read 503)."""
    fake_manager.admission_refusal = (manager_status, "model_not_found", "refused by the manager")
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
        assert resp.status_code == expected_status
        assert resp.json()["error"]["code"] == "model_not_found"
        assert resp.json()["error"]["message"] == "refused by the manager"
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("protocol_major", [None, 1], ids=["no_v2_routes", "older_major"])
async def test_apply_refuses_a_pre_v2_manager_without_mutation(
    demo_engine, fake_manager, monkeypatch, protocol_major
) -> None:
    """A mixed protocol pair fails safely: no admission, no audit, no writes."""
    fake_manager.protocol_major = protocol_major
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    _seed_config(demo_engine)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        before = _snapshot(demo_engine)
        resp = await _apply(client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "manager_upgrade_required"
        assert fake_manager.admission_calls == []
        assert _snapshot(demo_engine) == before
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_collapses_duplicate_main_and_vision(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """M51: never run two instances of the same model — one dual-capable
    instance with photos routed to it."""
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client,
            token,
            {
                "main_model": "Qwen/Qwen2.5-VL-32B-Instruct",
                "vision_model": "Qwen/Qwen2.5-VL-32B-Instruct",
            },
        )
        assert resp.status_code == 202
        admission = fake_manager.admission_calls[0]
        assert admission["main_model"] == "Qwen/Qwen2.5-VL-32B-Instruct"
        assert admission["vision_model"] is None
        assert admission["photo_mode"] == "main"
        assert resp.json()["requested_selection"]["photo_mode"] == "main"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_surfaces_the_manager_insufficient_memory_refusal(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """A describer bigger than the vision memory share crash-loops silently —
    but memory admission is the MANAGER's evidence-based call (ADR 0081), so
    the refusal arrives from its planning, never from API-host hardware math
    (the API host may not be a GPU box). The 422 envelope preserves the
    manager's stable reason."""
    fake_manager.admission_refusal = (
        422,
        "insufficient_memory",
        "'Qwen/Qwen2.5-VL-32B-Instruct' cannot fit the photo-model slot",
    )
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client,
            token,
            {
                "main_model": "zai-org/GLM-4.5-Air-FP8",
                "vision_model": "Qwen/Qwen2.5-VL-32B-Instruct",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "insufficient_memory"
        assert "photo-model slot" in resp.json()["error"]["message"]
        # The refusal is the manager's: admission was attempted and answered.
        assert len(fake_manager.admission_calls) == 1
    finally:
        await client.aclose()


# --- cluster topology (ADR 0071/0081): consent is stored, never requested -------


@pytest.mark.anyio
async def test_cluster_apply_requires_the_stored_consent(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """Cluster PERMISSION never comes from the request body (ADR 0071): a
    forged `cluster: true` cannot opt a household into two-node serving."""
    monkeypatch.setattr(ai_runtime_module, "cluster_peer_status", lambda: ("spark2", True))
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client, token, {"main_model": "Qwen/Qwen2.5-72B-Instruct", "cluster": True}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "cluster_consent_required"
        assert fake_manager.calls == []  # never admitted
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_cluster_apply_refused_while_peer_unreachable(
    demo_engine, fake_manager, monkeypatch
) -> None:
    """ADR 0071: a TP=2 apply with the second box down would crash-loop."""
    monkeypatch.setattr(ai_runtime_module, "cluster_peer_status", lambda: ("spark2", False))
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    _seed_config(demo_engine, cluster_enabled=True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client, token, {"main_model": "Qwen/Qwen2.5-72B-Instruct", "cluster": True}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "cluster_peer_unreachable"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_cluster_apply_forwards_consent_and_topology(
    demo_engine, fake_manager, monkeypatch
) -> None:
    monkeypatch.setattr(ai_runtime_module, "cluster_peer_status", lambda: ("spark2", True))
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    _seed_config(demo_engine, cluster_enabled=True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client, token, {"main_model": "Qwen/Qwen2.5-72B-Instruct", "cluster": True}
        )
        assert resp.status_code == 202
        admission = fake_manager.admission_calls[0]
        assert admission["topology"] == "cluster"
        # Consent rides along as authorization evidence the manager may only
        # use for a VALID two-node plan.
        assert admission["cluster_consent"] is True
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_cluster_alias_contradiction_is_a_request_shape_error(
    demo_engine, fake_manager
) -> None:
    """`cluster: true` + `topology: single` disagree about the serving shape:
    422 validation, not a preflight refusal — the manager is never asked."""
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await _apply(
            client,
            token,
            {"main_model": "Qwen/Qwen2.5-72B-Instruct", "cluster": True, "topology": "single"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"
        assert fake_manager.calls == []
    finally:
        await client.aclose()


# --- read-only status -------------------------------------------------------------


@pytest.mark.anyio
async def test_apply_status_is_idle_without_a_job(demo_engine, fake_manager) -> None:
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await client.get(
            "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        # `idle` is a read representation: the identity fields are null exactly
        # when no job has been admitted.
        assert body["state"] == "idle"
        assert body["job_id"] is None
        assert body["request_id"] is None
        assert body["lifecycle_state"] is None
        assert body["reason_code"] is None
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_status_requires_a_session(demo_engine, fake_manager) -> None:
    app = create_app(_settings(), engine=demo_engine)
    client, _token = await _owner_client_token(app)
    try:
        resp = await client.get("/api/v1/ai/runtime/apply/status")
        assert resp.status_code == 401
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_status_projects_the_current_job(
    demo_engine, fake_manager, monkeypatch
) -> None:
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        accepted = await _apply(
            client,
            token,
            {
                "main_model": "Qwen/Qwen2.5-14B-Instruct",
                "vision_model": "Qwen/Qwen2.5-VL-3B-Instruct",
            },
        )
        job_id = accepted.json()["job_id"]
        fake_manager.set_state(
            job_id,
            "commit_pending",
            decision="commit",
            verified_at=_fresh_iso(),
            deadline_at=_fresh_iso(600),
            resolved_plan=dict(_PLAN),
        )

        resp = await client.get(
            "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        # Commit-pending keeps the deployment transaction open: coarse running.
        assert body["state"] == "running"
        assert body["lifecycle_state"] == "commit_pending"
        assert body["main_model"] == "Qwen/Qwen2.5-14B-Instruct"
        assert body["requested_selection"]["vision_model"] == "Qwen/Qwen2.5-VL-3B-Instruct"
        plan = body["resolved_plan"]
        assert plan["main_revision"] == "8b5e6c1f"
        assert plan["image_digest"].startswith("sha256:")
        assert plan["node_requirements"] == [
            {
                "node": "primary",
                "memory_gb": 34.5,
                "memory_evidence": "estimated",
                "disk_gb": 29.5,
                "disk_evidence": "verified",
            }
        ]
        assert body["deadline_at"] is not None
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_status_with_a_job_id_reports_that_job(
    demo_engine, fake_manager, monkeypatch
) -> None:
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        accepted = await _apply(client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
        job_id = accepted.json()["job_id"]
        fake_manager.set_state(
            job_id,
            "failed",
            decision="restore",
            reason_code="verification_failed",
            message="the target never served the frozen identity",
            recovery_available=True,
        )

        resp = await client.get(
            f"/api/v1/ai/runtime/apply/status?job_id={job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "failed"
        assert body["lifecycle_state"] == "failed"
        assert body["reason_code"] == "verification_failed"
        assert body["recovery_available"] is True
        assert body["job_id"] == job_id
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_status_unknown_job_id_is_404(demo_engine, fake_manager) -> None:
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await client.get(
            f"/api/v1/ai/runtime/apply/status?job_id={uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "unknown_job"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_status_degrades_loudly_without_a_v2_manager(demo_engine, fake_manager) -> None:
    """A poll against a pre-v2 manager reads `unavailable` + the stable reason
    instead of erroring the client's poll loop (spec 04)."""
    fake_manager.protocol_major = None
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await client.get(
            "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "unavailable"
        assert body["reason_code"] == "manager_upgrade_required"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_status_reports_unreachable_manager(demo_engine, fake_manager) -> None:
    fake_manager.unreachable = True
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await client.get(
            "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "unavailable"
        assert body["message"]  # the cause is named, nothing is invented
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_apply_status_is_read_only(demo_engine, fake_manager, monkeypatch) -> None:
    """A status poll never commits settings, acknowledges, recovers, or
    creates rows (ADR 0081 S3: reads are pure)."""
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    _seed_config(demo_engine)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        accepted = await _apply(client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
        job_id = accepted.json()["job_id"]
        fake_manager.set_state(job_id, "verifying", verified_at=_fresh_iso())
        before = _snapshot(demo_engine)

        for _ in range(3):
            current = await client.get(
                "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
            )
            assert current.status_code == 200
            by_id = await client.get(
                f"/api/v1/ai/runtime/apply/status?job_id={job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert by_id.status_code == 200
        assert _snapshot(demo_engine) == before
    finally:
        await client.aclose()


# --- explicit recovery (ADR 0081: never automatic) --------------------------------


@pytest.mark.anyio
async def test_retry_recovery_reruns_the_frozen_direction(
    demo_engine, fake_manager, monkeypatch
) -> None:
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        accepted = await _apply(client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
        job_id = accepted.json()["job_id"]
        fake_manager.set_state(
            job_id, "recovery_failed", reason_code="restore_failed", recovery_available=True
        )

        resp = await client.post(
            f"/api/v1/ai/runtime/apply/{job_id}/retry-recovery",
            headers={"Authorization": f"Bearer {token}"},
            json={"request_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["lifecycle_state"] == "restoring"
        assert any(
            method == "POST" and url.endswith(f"/v2/jobs/{job_id}/recover")
            for method, url, _body in fake_manager.calls
        )
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_retry_recovery_unknown_job_is_404(demo_engine, fake_manager) -> None:
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await client.post(
            f"/api/v1/ai/runtime/apply/{uuid.uuid4()}/retry-recovery",
            headers={"Authorization": f"Bearer {token}"},
            json={"request_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "unknown_job"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_retry_recovery_refuses_when_not_recoverable(
    demo_engine, fake_manager, monkeypatch
) -> None:
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda hub, mid: True)
    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        accepted = await _apply(client, token, {"main_model": "Qwen/Qwen2.5-14B-Instruct"})
        job_id = accepted.json()["job_id"]
        fake_manager.set_state(job_id, "failed")  # terminal but not recoverable
        fake_manager.recovery_refusal = (
            409,
            "request_id_conflict",
            "recovery applies to recovery_failed or commit_repair_required",
        )
        resp = await client.post(
            f"/api/v1/ai/runtime/apply/{job_id}/retry-recovery",
            headers={"Authorization": f"Bearer {token}"},
            json={"request_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "request_id_conflict"
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_retry_recovery_requires_owner(demo_client, demo_viewer_token) -> None:
    resp = await demo_client.post(
        f"/api/v1/ai/runtime/apply/{uuid.uuid4()}/retry-recovery",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
        json={"request_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_retry_recovery_fails_closed_without_a_manager(demo_engine) -> None:
    app = create_app(_settings(model_manager_url=""), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await client.post(
            f"/api/v1/ai/runtime/apply/{uuid.uuid4()}/retry-recovery",
            headers={"Authorization": f"Bearer {token}"},
            json={"request_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "manager_unavailable"
    finally:
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
async def test_catalog_offers_verified_dual_capable_models(demo_client, demo_token) -> None:
    """M52 follow-up: Qwen3-VL renders tools (verified) — the all-in-one filter
    must have real content: vision AND tool calling in one entry."""
    resp = await demo_client.get(
        "/api/v1/ai/models", headers={"Authorization": f"Bearer {demo_token}"}
    )
    duals = [m for m in resp.json()["models"] if m["supports_vision"] and m["tool_parser"]]
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


@pytest.mark.anyio
async def test_model_detail_merges_estimates_with_hub_stats(
    demo_client, demo_token, monkeypatch
) -> None:
    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/api/models/Qwen/Qwen3.6-35B-A3B-FP8")
        return httpx.Response(
            200,
            json={
                "id": "Qwen/Qwen3.6-35B-A3B-FP8",
                "pipeline_tag": "text-generation",
                "downloads": 123456,
                "likes": 789,
                "lastModified": "2026-06-30T00:00:00.000Z",
                "tags": ["qwen3_5_moe", "fp8", "conversational"],
                "cardData": {"license": "apache-2.0"},
                "gated": False,
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/detail?id=Qwen/Qwen3.6-35B-A3B-FP8",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["downloads"] == 123456
    assert body["likes"] == 789
    assert body["license"] == "apache-2.0"
    assert "fp8" in body["tags"]
    assert body["info"]["id"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert body["info"]["parameters_b"] > 0


@pytest.mark.anyio
async def test_model_detail_falls_back_to_curated_when_hub_is_down(
    demo_client, demo_token, monkeypatch
) -> None:
    from family_cfo_api.ai_catalog import MODEL_CATALOG

    curated_id = MODEL_CATALOG[0].id

    def fake_get(url, params=None, timeout=None):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        f"/api/v1/ai/models/detail?id={curated_id}",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["info"]["id"] == curated_id
    assert body["downloads"] is None  # hub stats simply absent, not fabricated


@pytest.mark.anyio
async def test_model_detail_rejects_a_malformed_id(demo_client, demo_token) -> None:
    resp = await demo_client.get(
        "/api/v1/ai/models/detail?id=../etc/passwd",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_search_estimates_nvfp4_as_4bit(demo_client, demo_token, monkeypatch) -> None:
    # unsloth/Qwen3.6-35B-A3B-NVFP4 was estimated at bf16 weight (74 GB) when
    # its 4-bit weights are ~23 GB — fp4-family markers count as 4-bit.
    def fake_get(url, params=None, timeout=None):
        payload = (
            [{"modelId": "unsloth/Qwen3.6-35B-A3B-NVFP4", "gated": False}]
            if params["pipeline_tag"] == "text-generation"
            else []
        )
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    resp = await demo_client.get(
        "/api/v1/ai/models/search?q=nvfp4", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert resp.status_code == 200
    model = {m["id"]: m for m in resp.json()["models"]}["unsloth/Qwen3.6-35B-A3B-NVFP4"]
    assert model["est_memory_gb"] == 23  # 35 * 0.65, not 35 * 2.1


@pytest.mark.anyio
async def test_apply_repoints_every_household_on_the_shared_runtime(
    demo_engine, monkeypatch
) -> None:
    # One vLLM serves the whole box: a swap that only updated the initiating
    # household left every other household requesting the old model and
    # silently falling back to deterministic answers (found 2026-07-22).
    from family_cfo_api import repository
    from family_cfo_api.config import get_settings

    settings = get_settings()
    other = repository.create_household_with_owner(
        demo_engine,
        display_name="Other",
        base_currency="USD",
        owner_email="other@family-cfo.local",
        owner_password_hash="x",
        owner_display_name="Other Owner",
    )
    repository.upsert_ai_runtime_config(
        demo_engine,
        household_id=other.household_id,
        provider="vllm",
        base_url=settings.ai_default_base_url,
        model="Qwen/Old-Model",
        enabled=True,
    )

    changed = repository.repoint_ai_runtime_configs(
        demo_engine,
        provider="vllm",
        base_url=settings.ai_default_base_url,
        model="Qwen/New-Model",
    )

    assert changed >= 1
    record = repository.get_ai_runtime_config(demo_engine, other.household_id)
    assert record.model == "Qwen/New-Model"
