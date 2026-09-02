import pytest

from mic3_api.core.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Return complete settings without reading developer or deployment secrets."""
    return Settings(
        app_env="test",
        app_name="mic3-api",
        db_host="localhost",
        db_port=5433,
        db_name="mic3",
        db_user="mic3_api",
        db_password="test-only-password",
    )
