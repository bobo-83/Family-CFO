import httpx
import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures
from family_cfo_api.config import Settings
from family_cfo_api.db import create_database_engine
from family_cfo_api.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def demo_engine() -> Engine:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    fixtures.create_schema(engine)
    fixtures.seed_demo_household(engine)
    return engine


@pytest.fixture
def demo_settings(tmp_path) -> Settings:
    return Settings(
        version="0.1.0",
        health_check_database=False,
        import_staging_dir=str(tmp_path / "import-staging"),
    )


@pytest.fixture
def demo_app(demo_engine: Engine, demo_settings: Settings):
    return create_app(demo_settings, engine=demo_engine)


@pytest.fixture
async def demo_client(demo_app):
    transport = httpx.ASGITransport(app=demo_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def login(client: httpx.AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/api/v1/auth/sessions",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201
    return response.json()["access_token"]


@pytest.fixture
async def demo_token(demo_client: httpx.AsyncClient) -> str:
    return await login(demo_client, fixtures.DEMO_USER_EMAIL, fixtures.DEMO_USER_PASSWORD)


@pytest.fixture
async def demo_viewer_token(demo_client: httpx.AsyncClient) -> str:
    return await login(demo_client, fixtures.DEMO_VIEWER_EMAIL, fixtures.DEMO_VIEWER_PASSWORD)

