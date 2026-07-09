import httpx
import pytest

from family_cfo_api.api.health import build_health_response
from family_cfo_api.config import Settings
from family_cfo_api.main import create_app


def test_build_health_response_uses_configured_version() -> None:
    response = build_health_response(Settings(version="9.9.9", health_check_database=False))

    assert response.model_dump() == {"status": "ok", "version": "9.9.9"}


@pytest.mark.anyio
async def test_get_health_returns_openapi_response_shape() -> None:
    app = create_app(Settings(version="0.1.0", health_check_database=False))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    from family_cfo_api import __version__

    assert response.json() == {"status": "ok", "version": __version__}
