from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine

metadata = MetaData()


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, future=True)


def check_database_connection(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("select 1"))

