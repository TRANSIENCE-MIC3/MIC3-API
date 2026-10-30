"""Local user administration, independent of HTTP and persistence frameworks."""

from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from mic3_api.application.errors import AccessDenied, ApplicationError
from mic3_api.application.users.roles import ADMIN_ROLE
from mic3_api.application.users.user_account import CurrentUser, UserAccount

__all__ = ["ADMIN_ROLE", "AdminRequiredError", "AdministrationError", "GrantAdmin", "ListUsers",
           "UserAdministrationRepository", "UserAdministrationUnitOfWork"]


class AdministrationError(ApplicationError):
    """An administrator operation cannot be completed."""


class AdminRequiredError(AccessDenied):
    """The caller lacks local administrator access."""


class UserAdministrationRepository(Protocol):
    def find_for_grant(self, user_id: UUID) -> UserAccount | None: ...

    def role_exists(self, role: str) -> bool: ...

    def add_role(self, user_id: UUID, role: str) -> bool: ...

    def list_users(self, limit: int, offset: int) -> list[UserAccount]: ...


class UserAdministrationUnitOfWork(Protocol):
    @property
    def users(self) -> UserAdministrationRepository: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class GrantAdmin:
    def __init__(self, uow: UserAdministrationUnitOfWork) -> None:
        self._uow = uow

    def execute(self, user_id: UUID) -> bool:
        with self._uow.transaction():
            account = self._uow.users.find_for_grant(user_id)
            if account is None:
                raise AdministrationError("The MIC3 user does not exist.")
            if not account.is_active:
                raise AdministrationError("The MIC3 user is inactive.")
            if not self._uow.users.role_exists(ADMIN_ROLE):
                raise AdministrationError("The admin role is missing; apply migrations.")
            return self._uow.users.add_role(user_id, ADMIN_ROLE)


class ListUsers:
    def __init__(self, uow: UserAdministrationUnitOfWork) -> None:
        self._uow = uow

    def execute(self, caller: CurrentUser, *, limit: int = 50, offset: int = 0) -> list[UserAccount]:
        if ADMIN_ROLE not in caller.roles:
            raise AdminRequiredError("MIC3 administrator access is required.")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination.")
        with self._uow.transaction():
            return self._uow.users.list_users(limit, offset)
