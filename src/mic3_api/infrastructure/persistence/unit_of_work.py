"""SQLAlchemy transaction boundary for user-account application behavior."""

from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from mic3_api.infrastructure.persistence.user_accounts import (
    SqlAlchemyUserAccountRepository,
)


class SqlAlchemyUserAccountUnitOfWork:
    """Use a request-scoped session for explicit atomic transactions."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._users = SqlAlchemyUserAccountRepository(session)

    @property
    def users(self) -> SqlAlchemyUserAccountRepository:
        """Return the repository bound to this unit of work's session."""
        return self._users

    def transaction(self) -> AbstractContextManager[object]:
        """Commit on success and roll back automatically on failure."""
        return self._session.begin()
