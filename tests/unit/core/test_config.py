import pytest
from pydantic import ValidationError

from mic3_api.core.config import DatabaseSettings, Settings


def test_database_url_preserves_and_encodes_special_character_password() -> None:
    password = "p@ss/word:with#characters"
    settings = Settings(
        db_host="localhost",
        db_port=5433,
        db_name="mic3",
        db_user="mic3_api",
        db_password=password,
        oidc_issuer_url="https://issuer.test/realms/mic3",
        oidc_audience="mic3-api",
    )

    database_url = settings.database_url
    rendered_url = database_url.render_as_string(hide_password=False)

    assert database_url.drivername == "postgresql+psycopg"
    assert database_url.password == password
    assert password not in rendered_url
    assert "p%40ss%2Fword%3Awith%23characters" in rendered_url


def test_oidc_settings_accept_the_local_resource_server() -> None:
    settings = Settings(
        db_host="localhost",
        db_name="mic3",
        db_user="mic3_api",
        db_password="test-only-password",
        oidc_issuer_url="http://localhost:8080/realms/mic3",
        oidc_audience="mic3-api",
    )

    assert settings.oidc_issuer_url == "http://localhost:8080/realms/mic3"
    assert settings.oidc_audience == "mic3-api"
    assert settings.oidc_allowed_algorithms == ("RS256",)


def test_oidc_rejects_symmetric_signing_algorithms() -> None:
    with pytest.raises(ValidationError):
        Settings(
            db_host="localhost",
            db_name="mic3",
            db_user="mic3_api",
            db_password="test-only-password",
            oidc_issuer_url="https://issuer.test/realms/mic3",
            oidc_audience="mic3-api",
            oidc_allowed_algorithms=("HS256",),
        )


def test_api_requires_oidc_settings_but_database_settings_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OIDC_ISSUER_URL", raising=False)
    monkeypatch.delenv("OIDC_AUDIENCE", raising=False)
    database_values = {
        "db_host": "localhost",
        "db_name": "mic3",
        "db_user": "mic3_api",
        "db_password": "test-only-password",
    }

    DatabaseSettings(**database_values, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(**database_values, _env_file=None)
