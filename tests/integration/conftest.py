from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from mic3_api.core.config import Settings
from mic3_api.infrastructure.persistence import Base


PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = (
    "postgres:18@sha256:"
    "4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280"
)
TEST_DATABASE = "mic3_schema_test"
TEST_USERNAME = "mic3_schema_test"
TEST_PASSWORD = "test-only-password"


@pytest.fixture(scope="session")
def postgres_test_settings() -> Iterator[Settings]:
    """Start an isolated PostgreSQL instance for integration tests."""
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
            oidc_issuer_url="https://issuer.test/realms/mic3",
            oidc_audience="mic3-api",
        )


def _alembic(settings: Settings) -> Config:
    """Alembic aimed at the disposable test database, never at .env settings."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    return config


def _point_environment_at(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI commands and subprocesses read the database from the environment."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DB_HOST", settings.db_host)
    monkeypatch.setenv("DB_PORT", str(settings.db_port))
    monkeypatch.setenv("DB_NAME", settings.db_name)
    monkeypatch.setenv("DB_USER", settings.db_user)
    monkeypatch.setenv("DB_PASSWORD", settings.db_password.get_secret_value())


class MigratedSchema:
    """The schema at head, migrated once per session, with its seed rows remembered."""

    def __init__(self, settings: Settings) -> None:
        self.config = _alembic(settings)
        self.head = ScriptDirectory.from_config(self.config).get_current_head()
        command.downgrade(self.config, "base")
        command.upgrade(self.config, "head")
        self.engine = create_engine(settings.database_url, poolclass=NullPool)
        with self.engine.connect() as connection:
            # Rows the migrations seed (the roles); restored after every test.
            self.seeds = {table: [dict(row._mapping) for row in connection.execute(table.select())]
                          for table in Base.metadata.sorted_tables}

    def reset(self) -> None:
        """Empty every table and restore the seeds. Skipped if a migration test moved the schema;
        alembic_config rebuilds it in that case."""
        with self.engine.begin() as connection:
            if connection.scalar(text("SELECT version_num FROM alembic_version")) != self.head:
                return
            names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
            connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            for table, rows in self.seeds.items():
                if rows:
                    connection.execute(table.insert(), rows)


@pytest.fixture(scope="session")
def migrated_schema(postgres_test_settings: Settings) -> Iterator[MigratedSchema]:
    schema = MigratedSchema(postgres_test_settings)
    try:
        yield schema
    finally:
        schema.engine.dispose()


@pytest.fixture
def alembic_config(
    postgres_test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Config]:
    """For migration tests: the schema starts empty, and is rebuilt to head afterwards."""
    _point_environment_at(postgres_test_settings, monkeypatch)
    config = _alembic(postgres_test_settings)
    command.downgrade(config, "base")
    try:
        yield config
    finally:
        command.downgrade(config, "base")
        command.upgrade(config, "head")


@pytest.fixture
def migrated_engine(
    migrated_schema: MigratedSchema,
    postgres_test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Engine]:
    """An engine on the schema at head; every table is emptied after the test."""
    _point_environment_at(postgres_test_settings, monkeypatch)
    try:
        yield migrated_schema.engine
    finally:
        migrated_schema.reset()
