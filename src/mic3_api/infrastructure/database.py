from typing import Protocol

from sqlalchemy import URL, create_engine, text


class DatabaseGateway(Protocol):
    """Operations the API needs from its database infrastructure."""

    def ping(self) -> None:
        """Raise a SQLAlchemy error when PostgreSQL is unavailable."""
        ...

    def dispose(self) -> None:
        """Release database resources."""
        ...


class Database:
    """Own the SQLAlchemy engine and database-level health operations."""

    def __init__(self, url: URL) -> None:
        self._engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )

    def ping(self) -> None:
        """Raise a SQLAlchemy error when PostgreSQL cannot answer a query."""
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        """Close all connections held by the engine pool."""
        self._engine.dispose()
