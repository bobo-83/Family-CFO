from family_cfo_api.db import check_database_connection, create_database_engine


def test_check_database_connection_executes_probe() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    # Disposed: an engine left to the garbage collector closes its SQLite
    # connections whenever the collector gets to it, which Python 3.13+ reports
    # as a ResourceWarning — attributed to whichever test happens to be running
    # at the time, not this one.
    try:
        check_database_connection(engine)
    finally:
        engine.dispose()
