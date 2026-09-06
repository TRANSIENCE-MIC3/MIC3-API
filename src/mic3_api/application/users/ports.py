"""Persistence and transaction ports required by user resolution."""

from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.users.user_account import UserAccount, UserProfileUpdates


class IdentityAlreadyAssignedError(Exception):
    """Another transaction has already persisted an external identity."""


class InitialRoleNotFoundError(Exception):
    """The configured initial local role is absent from persistence."""


class UserAccountRepository(Protocol):
    """Persist and retrieve the user aggregate needed by authentication."""

    def find_by_identity(self, issuer: str, subject: str) -> UserAccount | None:
        """Return the account mapped to an exact external identity."""
        ...

    def create_with_identity_and_role(
        self,
        identity: ValidatedIdentity,
        role_name: str,
    ) -> UserAccount:
        """Stage a new account, identity, and initial role assignment."""
        ...

    def update_profile(
        self,
        account: UserAccount,
        updates: UserProfileUpdates,
    ) -> UserAccount:
        """Stage changes to mutable profile fields and return the new snapshot."""
        ...


class UserAccountUnitOfWork(Protocol):
    """Provide user persistence inside explicit atomic transactions."""

    @property
    def users(self) -> UserAccountRepository:
        """Return the repository participating in this unit of work."""
        ...

    def transaction(self) -> AbstractContextManager[object]:
        """Commit on success and roll back when the block raises."""
        ...
