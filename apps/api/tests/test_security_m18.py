import httpx
import pytest

from family_cfo_api import fixtures
from family_cfo_api.config import Settings
from family_cfo_api.main import create_app

_KEY = "jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY="


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        version="0.1.0",
        health_check_database=False,
        import_staging_dir=str(tmp_path / "import-staging"),
        backup_dir=str(tmp_path / "backups"),
        backup_encryption_key=_KEY,
        ai_allowed_base_urls=("http://vllm:8000",),
    )
    base.update(overrides)
    return Settings(**base)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _owner_token(client) -> str:
    resp = await client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


# --- SSRF: base_url allowlist ------------------------------------------------


@pytest.mark.anyio
async def test_ai_runtime_rejects_non_allowlisted_base_url(demo_client, demo_token) -> None:
    resp = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://169.254.169.254/latest/meta-data",
            "model": "m",
            "enabled": True,
        },
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_ai_runtime_accepts_allowlisted_base_url(demo_client, demo_token) -> None:
    resp = await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm:8000",
            "model": "m",
            "enabled": True,
        },
    )
    assert resp.status_code == 200


# --- Auth rate limiting ------------------------------------------------------


@pytest.mark.anyio
async def test_repeated_bad_logins_get_locked_out(demo_engine, tmp_path) -> None:
    app = create_app(
        _settings(tmp_path, auth_rate_limit_max_attempts=3, auth_rate_limit_lockout_seconds=60),
        engine=demo_engine,
    )
    async with _client(app) as client:
        for _ in range(3):
            bad = await client.post(
                "/api/v1/auth/sessions",
                json={"email": fixtures.DEMO_USER_EMAIL, "password": "wrongpassword"},
            )
            assert bad.status_code == 401
        # Now locked out — even the correct password is refused with 429.
        locked = await client.post(
            "/api/v1/auth/sessions",
            json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
        )
        assert locked.status_code == 429
        assert "Retry-After" in locked.headers


@pytest.mark.anyio
async def test_successful_login_resets_the_counter(demo_engine, tmp_path) -> None:
    app = create_app(_settings(tmp_path, auth_rate_limit_max_attempts=3), engine=demo_engine)
    async with _client(app) as client:
        for _ in range(2):
            await client.post(
                "/api/v1/auth/sessions",
                json={"email": fixtures.DEMO_USER_EMAIL, "password": "wrongpassword"},
            )
        ok = await client.post(
            "/api/v1/auth/sessions",
            json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
        )
        assert ok.status_code == 201
        # Counter reset, so two more bad attempts don't trip the limit of 3.
        for _ in range(2):
            bad = await client.post(
                "/api/v1/auth/sessions",
                json={"email": fixtures.DEMO_USER_EMAIL, "password": "wrongpassword"},
            )
            assert bad.status_code == 401


# --- Upload size cap ---------------------------------------------------------


@pytest.mark.anyio
async def test_oversized_upload_is_rejected(demo_engine, tmp_path) -> None:
    app = create_app(_settings(tmp_path, max_upload_bytes=1024), engine=demo_engine)
    async with _client(app) as client:
        token = await _owner_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = await client.post(
            "/api/v1/imports",
            headers=headers,
            json={"source_type": "csv", "filename": "big.csv"},
        )
        assert created.status_code == 201
        import_id = created.json()["id"]

        resp = await client.post(
            f"/api/v1/imports/{import_id}/file",
            headers=headers,
            files={"file": ("big.csv", b"x" * 2048, "text/csv")},
        )
        assert resp.status_code == 413


# --- Docs gating -------------------------------------------------------------


def test_docs_disabled_in_production(demo_engine, tmp_path) -> None:
    prod = create_app(_settings(tmp_path, environment="production"), engine=demo_engine)
    assert prod.docs_url is None
    assert prod.openapi_url is None

    dev = create_app(_settings(tmp_path, environment="development"), engine=demo_engine)
    assert dev.docs_url == "/api/v1/docs"


# --- Pairing secret entropy --------------------------------------------------


@pytest.mark.anyio
async def test_pairing_session_id_is_high_entropy(demo_client, demo_token) -> None:
    resp = await demo_client.post(
        "/api/v1/pairing/sessions", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert resp.status_code == 201
    # token_urlsafe(32) renders to ~43 url-safe chars — far beyond a 36-char uuid4.
    assert len(resp.json()["id"]) >= 40
