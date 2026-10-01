"""Persistence for operator grants and the administrative user directory."""

from contextlib import AbstractContextManager
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mic3_api.application.users.user_account import UserAccount
from mic3_api.infrastructure.persistence import Role, User, UserRole


class SqlAlchemyUserAdministrationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_for_grant(self, user_id: UUID) -> UserAccount | None:
        user = self._session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        return self._snapshots([user])[0] if user is not None else None

    def role_exists(self, role: str) -> bool:
        return self._session.get(Role, role) is not None

    def add_role(self, user_id: UUID, role: str) -> bool:
        statement = (
            insert(UserRole).values(user_id=user_id, role_name=role)
            .on_conflict_do_nothing(index_elements=["user_id", "role_name"])
            .returning(UserRole.user_id)
        )
        return self._session.scalar(statement) is not None

    def list_users(self, limit: int, offset: int) -> list[UserAccount]:
        users = list(self._session.scalars(
            select(User).order_by(User.id).limit(limit).offset(offset)
        ))
        return self._snapshots(users)

    def _snapshots(self, users: list[User]) -> list[UserAccount]:
        if not users:
            return []
        roles: dict[UUID, set[str]] = {user.id: set() for user in users}
        for user_id, role in self._session.execute(
            select(UserRole.user_id, UserRole.role_name)
            .where(UserRole.user_id.in_(roles))
        ):
            roles[user_id].add(role)
        return [UserAccount(
            id=user.id, email=user.email, display_name=user.display_name,
            is_active=user.is_active, roles=frozenset(roles[user.id]),
        ) for user in users]


class SqlAlchemyUserAdministrationUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.users = SqlAlchemyUserAdministrationRepository(session)

    def transaction(self) -> AbstractContextManager[object]:
        return self._session.begin()
