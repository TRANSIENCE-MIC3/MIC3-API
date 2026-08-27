from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from mic3_api.main import create_app


def test_health_returns_expected_response() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_docs_are_available() -> None:
    client = TestClient(create_app())

    response = client.get("/docs")

    assert response.status_code == 200


def test_openapi_schema_contains_health_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Integration Test API")
    client = TestClient(create_app())

    response = client.get("/openapi.json")
    schema = response.json()

    assert response.status_code == 200
    assert "/health" in schema["paths"]
    assert schema["info"]["title"] == "Integration Test API"
    assert schema["info"]["version"] == version("mic3-api")
