from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

metadata = MetaData()


def create_database_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        # A single shared connection keeps the in-memory database alive across
        # the worker threads FastAPI uses to run sync route dependencies.
        return create_engine(
            database_url,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    return create_engine(database_url, pool_pre_ping=True, future=True)


def check_database_connection(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("select 1"))
