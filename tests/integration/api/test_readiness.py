from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from mic3_api.core.config import Settings
from mic3_api.main import create_app


class StubDatabase:
    def __init__(self, error: SQLAlchemyError | None = None) -> None:
        self.error = error
        self.ping_calls = 0
        self.disposed = False

    def ping(self) -> None:
        self.ping_calls += 1
        if self.error is not None:
            raise self.error

    def dispose(self) -> None:
        self.disposed = True


def test_ready_returns_ready_when_database_responds(
    test_settings: Settings,
) -> None:
    database = StubDatabase()

    with TestClient(create_app(settings=test_settings, database=database)) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert database.ping_calls == 1
    assert database.disposed is True


def test_ready_returns_not_ready_when_database_is_unavailable(
    test_settings: Settings,
) -> None:
    database = StubDatabase(SQLAlchemyError("database unavailable"))

    with TestClient(create_app(settings=test_settings, database=database)) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert database.ping_calls == 1


def test_health_does_not_query_an_unavailable_database(
    test_settings: Settings,
) -> None:
    database = StubDatabase(SQLAlchemyError("database unavailable"))

    with TestClient(create_app(settings=test_settings, database=database)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    assert database.ping_calls == 0
