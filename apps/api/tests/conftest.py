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
        backup_dir=str(tmp_path / "backups"),
        backup_encryption_key="jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY=",
        # M32 lockout is tested explicitly in test_household_lockout; the shared
        # fixture allows multiples so pre-existing bootstrap tests keep working
        # against the seeded demo household.
        allow_multiple_households=True,
        # Allowlist the base_urls the runtime/advisor tests configure (ADR 0010).
        ai_allowed_base_urls=(
            "http://vllm:8000",
            "http://vllm.local:8000",
            "http://ollama.local:11434",
        ),
    )


@pytest.fixture
def demo_file_engine(tmp_path) -> Engine:
    """A file-based (not `:memory:`) SQLite engine, for backup/restore tests.

    `SqliteFileBackupAdapter` copies the database file directly, which is
    only possible when there is a file on disk to copy.
    """
    database_path = tmp_path / "family_cfo.sqlite3"
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path}")
    fixtures.create_schema(engine)
    fixtures.seed_demo_household(engine)
    return engine


@pytest.fixture
def demo_file_settings(tmp_path, demo_file_engine: Engine) -> Settings:
    database_path = tmp_path / "family_cfo.sqlite3"
    return Settings(
        version="0.1.0",
        health_check_database=False,
        database_url=f"sqlite+pysqlite:///{database_path}",
        import_staging_dir=str(tmp_path / "import-staging"),
        backup_dir=str(tmp_path / "backups"),
        backup_encryption_key="jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY=",
    )


@pytest.fixture
def demo_file_app(demo_file_engine: Engine, demo_file_settings: Settings):
    return create_app(demo_file_settings, engine=demo_file_engine)


@pytest.fixture
async def demo_file_client(demo_file_app):
    transport = httpx.ASGITransport(app=demo_file_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def demo_file_token(demo_file_client: httpx.AsyncClient) -> str:
    return await login(demo_file_client, fixtures.DEMO_USER_EMAIL, fixtures.DEMO_USER_PASSWORD)


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
