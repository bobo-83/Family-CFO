"""The API's manager wire contract (issue #170 item 4, ADR 0081).

The promoted sidecar speaks protocol major 2 and the API refuses anything
else: a pre-v2 manager (the v1 fixture's surface: `POST /swap`, `GET
/status`, `GET /logs`) is a mixed pair that must fail safely WITHOUT any
mutation. The v1 fixture stays untouched as historical evidence; these tests
freeze the API side of the v2 fixture instead.
"""

import json
from pathlib import Path

import httpx
import pytest

from family_cfo_api import model_manager_client
from family_cfo_api.api import ai_runtime as ai_runtime_module
from family_cfo_api.config import Settings
from family_cfo_api.main import create_app
from tests._test_keys import TEST_FERNET_KEY

_SHARED = Path(__file__).resolve().parents[3] / "shared/schemas"
_V2 = json.loads((_SHARED / "model-manager-v2-contract.json").read_text())
_V1 = json.loads((_SHARED / "model-manager-v1-contract.json").read_text())

_BASE = "http://model-manager:8000"
_CREDENTIAL = "test-deployment-credential"


def _settings() -> Settings:
    return Settings(
        health_check_database=False,
        backup_encryption_key=TEST_FERNET_KEY,
        model_manager_url=_BASE,
        model_manager_credential=_CREDENTIAL,
        # The fixture freezes this runtime key: vllm+<the API's default base>.
        ai_default_base_url="http://api:8000/v1",
    )


async def _owner_client_token(app):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    login = await client.post(
        "/api/v1/auth/sessions",
        json={"email": "demo@family-cfo.local", "password": "demo-password-123"},
    )
    return client, login.json()["access_token"]


def _serve(monkeypatch, routes: dict[str, object], calls: list):
    """Serve canned documents by path suffix, recording every call."""

    def fake_request(method, url, json=None, headers=None, timeout=None):
        calls.append((method, url, json, headers))
        for suffix, responder in routes.items():
            if url.endswith(suffix):
                return responder
        raise AssertionError(f"the API must not call {method} {url}")

    monkeypatch.setattr(model_manager_client.httpx, "request", fake_request)


@pytest.mark.anyio
async def test_api_drives_the_frozen_v2_admission_contract(demo_engine, monkeypatch) -> None:
    """The API's admission request carries exactly the frozen key set with
    the fixture's contract fields, authenticates with the deployment
    credential, and the fixture's response document is exactly what the API
    projects (nothing invented, nothing dropped)."""
    admit = _V2["operations"]["admit_job"]
    capabilities = _V2["operations"]["capabilities"]
    calls: list[tuple] = []
    _serve(
        monkeypatch,
        {
            capabilities["path"]: httpx.Response(
                200,
                json=capabilities["response"],
                request=httpx.Request("GET", _BASE + capabilities["path"]),
            ),
            admit["path"]: httpx.Response(
                202,
                json=admit["response"],
                request=httpx.Request("POST", _BASE + admit["path"]),
            ),
        },
        calls,
    )
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda _hub, _model: True)

    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        request = admit["request"]
        resp = await client.post(
            "/api/v1/ai/runtime/apply",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": request["request_id"],
                "main_model": request["main_model"],
                "vision_model": request["vision_model"],
                "photo_mode": request["photo_mode"],
                "topology": request["topology"],
            },
        )
        assert resp.status_code == 202

        _method, url, sent, headers = calls[1]
        assert url == _BASE + admit["path"]
        assert set(sent) == set(request)
        for field in (
            "request_id",
            "main_model",
            "vision_model",
            "photo_mode",
            "topology",
            "cluster_consent",
            "origin",
            "runtime_key",
        ):
            assert sent[field] == request[field], field
        assert headers["x-manager-credential"] == _CREDENTIAL

        body = resp.json()
        expected = admit["response"]
        assert body["job_id"] == expected["job_id"]
        assert body["request_id"] == expected["request_id"]
        assert body["state"] == "running"  # the coarse projection of `accepted`
        assert body["lifecycle_state"] == "accepted"
        assert body["requested_selection"] == expected["requested_selection"]
        assert body["resolved_plan"] is None  # the plan resolves inside the manager
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_api_projects_the_frozen_v2_job_document(demo_engine, monkeypatch) -> None:
    """A v2 job document in the frozen shape decodes additively: the coarse
    legacy `state` rides on top of the full lifecycle, and the resolved plan
    summary round-trips field for field."""
    document = _V2["operations"]["get_job"]["response"]
    capabilities = _V2["operations"]["capabilities"]
    calls: list[tuple] = []
    _serve(
        monkeypatch,
        {
            capabilities["path"]: httpx.Response(
                200,
                json=capabilities["response"],
                request=httpx.Request("GET", _BASE + capabilities["path"]),
            ),
            f"/v2/jobs/{document['job_id']}": httpx.Response(
                200,
                json=document,
                request=httpx.Request("GET", _BASE + f"/v2/jobs/{document['job_id']}"),
            ),
        },
        calls,
    )

    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        resp = await client.get(
            f"/api/v1/ai/runtime/apply/status?job_id={document['job_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # commit_pending keeps the deployment transaction open: coarse running.
        assert body["state"] == "running"
        assert body["lifecycle_state"] == "commit_pending"
        assert body["requested_selection"] == document["requested_selection"]
        assert body["resolved_plan"] == document["resolved_plan"]
        assert body["previous_selection"] == document["previous_selection"]
        assert body["reason_code"] is None
        assert body["deadline_at"] is not None
        assert body["recovery_available"] is False
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["no_v2_routes", "older_major"])
async def test_api_refuses_the_previous_manager_surface_without_mutation(
    demo_engine, monkeypatch, mode
) -> None:
    """The v1 surface (POST /swap, GET /status, GET /logs) is a mixed pair:
    apply fails safely with the stable upgrade reason, the status poll
    degrades loudly, and NOTHING is called or written on the legacy paths."""
    calls: list[tuple] = []

    def responder(method, url, json=None, headers=None, timeout=None):
        calls.append((method, url, json))
        if mode == "no_v2_routes":
            return httpx.Response(
                404, json={"detail": "Not Found"}, request=httpx.Request(method, url)
            )
        return httpx.Response(
            200,
            json={"protocol_major": 1, "build": "0"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(model_manager_client.httpx, "request", responder)
    monkeypatch.setattr(ai_runtime_module, "_hf_model_exists", lambda _hub, _model: True)

    app = create_app(_settings(), engine=demo_engine)
    client, token = await _owner_client_token(app)
    try:
        apply_response = await client.post(
            "/api/v1/ai/runtime/apply",
            headers={"Authorization": f"Bearer {token}"},
            json={"main_model": "Qwen/Qwen2.5-14B-Instruct"},
        )
        assert apply_response.status_code == 503
        assert apply_response.json()["error"]["code"] == "manager_upgrade_required"

        status_response = await client.get(
            "/api/v1/ai/runtime/apply/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert status_response.status_code == 200
        assert status_response.json()["state"] == "unavailable"
        assert status_response.json()["reason_code"] == "manager_upgrade_required"

        # No legacy mutation path was ever touched, and nothing was written.
        legacy_paths = tuple(op["path"] for op in _V1["operations"].values())
        assert all(not url.endswith(path) for _m, url, _j in calls for path in legacy_paths)
    finally:
        await client.aclose()
