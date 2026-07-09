import httpx
import pytest

from family_cfo_api.config import Settings
from family_cfo_api.main import create_app

_KEY = "jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY="


def _payload(email: str) -> dict:
    return {
        "display_name": "Second Family",
        "base_currency": "USD",
        "owner_email": email,
        "owner_password": "a-strong-password",
        "owner_display_name": "Owner",
    }


def _app(demo_engine, **overrides):
    settings = Settings(
        version="0.1.0", health_check_database=False, backup_encryption_key=_KEY, **overrides
    )
    return create_app(settings, engine=demo_engine)


@pytest.mark.anyio
async def test_bootstrap_refused_once_a_household_exists(demo_engine) -> None:
    app = _app(demo_engine)  # default: single-tenant lockout
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/api/v1/households", json=_payload("new@example.com"))
    assert resp.status_code == 403
    assert "already has a household" in resp.json()["error"]["message"]


@pytest.mark.anyio
async def test_opt_out_allows_additional_households(demo_engine) -> None:
    app = _app(demo_engine, allow_multiple_households=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/api/v1/households", json=_payload("second@example.com"))
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_first_run_bootstrap_unaffected() -> None:
    from family_cfo_api import fixtures
    from family_cfo_api.db import create_database_engine

    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    fixtures.create_schema(engine)  # empty schema, no household
    app = _app(engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/api/v1/households", json=_payload("first@example.com"))
    assert resp.status_code == 201
