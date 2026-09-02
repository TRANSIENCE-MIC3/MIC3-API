from unittest.mock import MagicMock, patch

from sqlalchemy import URL

from mic3_api.infrastructure.database import Database


def test_database_executes_select_one_and_disposes_engine() -> None:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    url = URL.create(
        "postgresql+psycopg",
        username="mic3_api",
        password="test-only-password",
        host="localhost",
        port=5433,
        database="mic3",
    )

    with patch(
        "mic3_api.infrastructure.database.create_engine",
        return_value=engine,
    ) as create_engine:
        database = Database(url)
        database.ping()
        database.dispose()

    create_engine.assert_called_once_with(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    engine.dispose.assert_called_once_with()
