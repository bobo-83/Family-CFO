from family_cfo_api.db import check_database_connection, create_database_engine


def test_check_database_connection_executes_probe() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")

    check_database_connection(engine)
