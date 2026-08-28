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
async def test_get_health_ignores_environment_version_override(monkeypatch) -> None:
    """Deployment configuration cannot forge the running artifact identity."""
    monkeypatch.setenv("FAMILY_CFO_API_VERSION", "9.9.9")
    get_settings.cache_clear()
    try:
        app = create_app(Settings(health_check_database=False))
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/health")

        assert response.json() == {"status": "ok", "version": __version__}
    finally:
        # The cache is process-global; do not leak this test's Settings object.
        get_settings.cache_clear()
