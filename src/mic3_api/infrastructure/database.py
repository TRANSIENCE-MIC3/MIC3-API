from typing import Protocol

from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


class DatabaseHealthGateway(Protocol):
    """Small database capability required by the readiness endpoint."""

    def ping(self) -> None:
        """Raise a SQLAlchemy error when PostgreSQL is unavailable."""
        ...


class DatabaseGateway(DatabaseHealthGateway, Protocol):
    """Application database lifecycle and request-session capabilities."""

    def dispose(self) -> None:
        """Release database resources."""
        ...

    def open_session(self) -> Session:
        """Create one SQLAlchemy session for a request boundary."""
        ...


class Database:
    """Own the SQLAlchemy engine and database-level health operations."""

    def __init__(self, url: URL) -> None:
        self._engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
        self._session_factory = sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def ping(self) -> None:
        """Raise a SQLAlchemy error when PostgreSQL cannot answer a query."""
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        """Close all connections held by the engine pool."""
        self._engine.dispose()

    def open_session(self) -> Session:
        """Create a session whose closure is owned by the caller."""
        return self._session_factory()
