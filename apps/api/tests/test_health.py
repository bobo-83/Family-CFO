import httpx
import pytest

from family_cfo_api import __version__
from family_cfo_api.api.health import build_health_response
from family_cfo_api.config import Settings, get_settings
from family_cfo_api.main import create_app


def test_build_health_response_uses_configured_version() -> None:
    response = build_health_response(Settings(version="9.9.9", health_check_database=False))

    assert response.model_dump() == {"status": "ok", "version": "9.9.9"}


@pytest.mark.anyio
async def test_get_health_returns_openapi_response_shape() -> None:
    app = create_app(Settings(health_check_database=False))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


@pytest.mark.anyio
async def test_get_health_reports_the_configured_version(monkeypatch) -> None:
    """`get_health` calls `get_settings()` itself rather than taking the app's
    injected Settings, so `FAMILY_CFO_API_VERSION` is what actually governs what
    a box reports — pinned because the version is the compatibility signal every
    client compares against (ADR 0074).

    `get_settings` is `@lru_cache`d, so the first caller in the process wins and
    the cache has to be cleared for the override to be seen. That is fine in
    production (the environment is set before the app starts) but it means a
    version set after startup is silently ignored.
    """
    monkeypatch.setenv("FAMILY_CFO_API_VERSION", "9.9.9")
    get_settings.cache_clear()
    try:
        app = create_app(Settings(health_check_database=False))
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/health")

        assert response.json() == {"status": "ok", "version": "9.9.9"}
    finally:
        # The cache is process-global; leaving 9.9.9 in it would leak into every
        # later test that reads settings.
        get_settings.cache_clear()
