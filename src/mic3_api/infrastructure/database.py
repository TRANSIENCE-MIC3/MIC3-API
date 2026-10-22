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

    def __init__(self, url: URL, *, statement_timeout_ms: int | None = None,
                 lock_timeout_ms: int | None = None) -> None:
        connect_args = {"connect_timeout": 3}
        options = []
        for name, value in (("statement_timeout", statement_timeout_ms), ("lock_timeout", lock_timeout_ms)):
            if value is not None:
                if type(value) is not int or value <= 0:
                    raise ValueError("Database timeouts must be positive integers.")
                options.append(f"-c {name}={value}")
        if options:
            connect_args["options"] = " ".join(options)
        self._engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args=connect_args,
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

    @property
    def engine(self):
        """Execution repositories own their own short sessions and lock connections."""
        return self._engine

    def dispose(self) -> None:
        """Close all connections held by the engine pool."""
        self._engine.dispose()

    def open_session(self) -> Session:
        """Create a session whose closure is owned by the caller."""
        return self._session_factory()
