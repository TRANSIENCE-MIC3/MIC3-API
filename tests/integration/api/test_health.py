from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from mic3_api.core.config import Settings
from mic3_api.main import create_app


def test_health_returns_expected_response(test_settings: Settings) -> None:
    with TestClient(create_app(settings=test_settings)) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


def test_docs_are_available(test_settings: Settings) -> None:
    with TestClient(create_app(settings=test_settings)) as client:
        response = client.get("/docs")

        assert response.status_code == 200


def test_openapi_schema_contains_health_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_NAME", "Integration Test API")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_NAME", "mic3")
    monkeypatch.setenv("DB_USER", "mic3_api")
    monkeypatch.setenv("DB_PASSWORD", "test-only-password")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.test/realms/mic3")
    monkeypatch.setenv("OIDC_AUDIENCE", "mic3-api")

    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")
        schema = response.json()

        assert response.status_code == 200
        assert "/health" in schema["paths"]
        assert "/ready" in schema["paths"]
        assert "/users/me" in schema["paths"]
        bearer_schemes = {
            name
            for name, security_scheme in schema["components"][
                "securitySchemes"
            ].items()
            if security_scheme.get("type") == "http"
            and security_scheme.get("scheme") == "bearer"
        }
        assert bearer_schemes
        assert schema["paths"]["/users/me"]["get"]["security"] == [
            {bearer_schemes.pop(): []}
        ]
        assert "security" not in schema["paths"]["/health"]["get"]
        assert "security" not in schema["paths"]["/ready"]["get"]
        assert schema["info"]["title"] == "Integration Test API"
        assert schema["info"]["version"] == version("mic3-api")
