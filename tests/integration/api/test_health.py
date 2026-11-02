from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

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


def probe(test_settings, path: str, database: StubDatabase):
    with TestClient(create_app(settings=test_settings, database=database)) as client:
        return client.get(path)


def test_health_never_queries_the_database(test_settings):
    database = StubDatabase(SQLAlchemyError("database unavailable"))
    response = probe(test_settings, "/health", database)
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    assert database.ping_calls == 0


@pytest.mark.parametrize("error,code,status", [(None, 200, "ready"),
                                               (SQLAlchemyError("unavailable"), 503, "not_ready")])
def test_ready_reflects_database_reachability(test_settings, error, code, status):
    database = StubDatabase(error)
    response = probe(test_settings, "/ready", database)
    assert response.status_code == code
    assert response.json() == {"status": status}
    assert database.ping_calls == 1
    assert database.disposed is True


def test_docs_and_openapi_schema(test_settings):
    settings = test_settings.model_copy(update={"app_name": "Integration Test API"})
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()

    assert {"/health", "/ready", "/users/me", "/models"} <= schema["paths"].keys()
    bearer = [name for name, scheme in schema["components"]["securitySchemes"].items()
              if scheme.get("type") == "http" and scheme.get("scheme") == "bearer"]
    assert len(bearer) == 1
    assert schema["paths"]["/users/me"]["get"]["security"] == [{bearer[0]: []}]
    for public in ("/health", "/ready", "/models"):
        assert "security" not in schema["paths"][public]["get"]
    assert schema["info"] == {"title": "Integration Test API", "version": version("mic3-api")}
