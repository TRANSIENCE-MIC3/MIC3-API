from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from mic3_api.core.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[3]
POSTGRES_IMAGE = (
    "postgres:18@sha256:"
    "4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280"
)
TEST_DATABASE = "mic3_schema_test"
TEST_USERNAME = "mic3_schema_test"
TEST_PASSWORD = "test-only-password"


@pytest.fixture(scope="session")
def postgres_test_settings() -> Iterator[Settings]:
    """Start an isolated PostgreSQL instance for schema integration tests."""
    with PostgresContainer(
        image=POSTGRES_IMAGE,
        username=TEST_USERNAME,
        password=TEST_PASSWORD,
        dbname=TEST_DATABASE,
        driver="psycopg",
    ) as postgres:
        yield Settings(
            app_env="test",
            app_name="mic3-api",
            db_host=postgres.get_container_host_ip(),
            db_port=int(postgres.get_exposed_port(5432)),
            db_name=TEST_DATABASE,
            db_user=TEST_USERNAME,
            db_password=TEST_PASSWORD,
        )


@pytest.fixture
def alembic_config(
    postgres_test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Config]:
    """Point Alembic exclusively at the disposable test database."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DB_HOST", postgres_test_settings.db_host)
    monkeypatch.setenv("DB_PORT", str(postgres_test_settings.db_port))
    monkeypatch.setenv("DB_NAME", postgres_test_settings.db_name)
    monkeypatch.setenv("DB_USER", postgres_test_settings.db_user)
    monkeypatch.setenv(
        "DB_PASSWORD",
        postgres_test_settings.db_password.get_secret_value(),
    )

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.downgrade(config, "base")
    try:
        yield config
    finally:
        command.downgrade(config, "base")


@pytest.fixture
def migrated_engine(
    alembic_config: Config,
    postgres_test_settings: Settings,
) -> Iterator[Engine]:
    """Upgrade a clean schema and expose a short-lived SQLAlchemy engine."""
    command.upgrade(alembic_config, "head")
    engine = create_engine(postgres_test_settings.database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
