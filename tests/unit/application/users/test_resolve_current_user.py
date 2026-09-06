from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID

import pytest

from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.users import (
    IdentityAlreadyAssignedError,
    InactiveUserError,
    InitialRoleNotFoundError,
    ResolveCurrentUser,
    UserAccount,
    UserProfileUpdates,
    UserProvisioningConfigurationError,
)


USER_ID = UUID("10000000-0000-0000-0000-000000000001")
IDENTITY = ValidatedIdentity(
    issuer="https://issuer.example/realms/mic3",
    subject="Case-Sensitive-Subject",
    email="member@example.org",
    display_name="Member Name",
)


class FakeRepository:
    def __init__(self, account: UserAccount | None = None) -> None:
        self.account = account
        self.create_calls = 0
        self.update_calls = 0
        self.missing_role = False

    def find_by_identity(self, issuer: str, subject: str) -> UserAccount | None:
        assert issuer == IDENTITY.issuer
        assert subject == IDENTITY.subject
        return self.account

    def create_with_identity_and_role(
        self,
        identity: ValidatedIdentity,
        role_name: str,
    ) -> UserAccount:
        self.create_calls += 1
        if self.missing_role:
            raise InitialRoleNotFoundError(role_name)
        self.account = UserAccount(
            id=USER_ID,
            email=identity.email,
            display_name=identity.display_name,
            is_active=True,
            roles=frozenset({role_name}),
        )
        return self.account

    def update_profile(
        self,
        account: UserAccount,
        updates: UserProfileUpdates,
    ) -> UserAccount:
        self.update_calls += 1
        self.account = replace(
            account,
            email=updates.email if updates.email is not None else account.email,
            display_name=(
                updates.display_name
                if updates.display_name is not None
                else account.display_name
            ),
        )
        return self.account


class FakeUnitOfWork:
    def __init__(self, repository: FakeRepository) -> None:
        self.users = repository
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.transactions += 1
        try:
            yield
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


def test_provisions_one_member_and_returns_sorted_roles() -> None:
    repository = FakeRepository()
    unit_of_work = FakeUnitOfWork(repository)

    current_user = ResolveCurrentUser().execute(IDENTITY, unit_of_work)

    assert current_user.id == USER_ID
    assert current_user.email == "member@example.org"
    assert current_user.display_name == "Member Name"
    assert current_user.roles == ("member",)
    assert repository.create_calls == 1
    assert unit_of_work.commits == 1
    assert unit_of_work.rollbacks == 0


def test_updates_changed_claims_and_retains_missing_claims() -> None:
    account = UserAccount(
        id=USER_ID,
        email="old@example.org",
        display_name="Last Known Name",
        is_active=True,
        roles=frozenset({"z-role", "member"}),
    )
    repository = FakeRepository(account)
    unit_of_work = FakeUnitOfWork(repository)
    identity = replace(
        IDENTITY,
        email="new@example.org",
        display_name=None,
    )

    current_user = ResolveCurrentUser().execute(identity, unit_of_work)

    assert current_user.email == "new@example.org"
    assert current_user.display_name == "Last Known Name"
    assert current_user.roles == ("member", "z-role")
    assert repository.update_calls == 1


def test_does_not_write_when_profile_claims_are_unchanged() -> None:
    account = UserAccount(
        id=USER_ID,
        email=IDENTITY.email,
        display_name=IDENTITY.display_name,
        is_active=True,
        roles=frozenset({"member"}),
    )
    repository = FakeRepository(account)

    ResolveCurrentUser().execute(IDENTITY, FakeUnitOfWork(repository))

    assert repository.update_calls == 0


def test_rejects_inactive_account_without_updating_it() -> None:
    account = UserAccount(
        id=USER_ID,
        email="old@example.org",
        display_name="Old Name",
        is_active=False,
        roles=frozenset({"member"}),
    )
    repository = FakeRepository(account)
    unit_of_work = FakeUnitOfWork(repository)

    with pytest.raises(InactiveUserError):
        ResolveCurrentUser().execute(IDENTITY, unit_of_work)

    assert repository.update_calls == 0
    assert unit_of_work.rollbacks == 1


def test_missing_member_role_becomes_configuration_error() -> None:
    repository = FakeRepository()
    repository.missing_role = True
    unit_of_work = FakeUnitOfWork(repository)

    with pytest.raises(UserProvisioningConfigurationError):
        ResolveCurrentUser().execute(IDENTITY, unit_of_work)

    assert unit_of_work.rollbacks == 1


def test_reloads_the_winning_account_after_a_provisioning_race() -> None:
    winning_account = UserAccount(
        id=USER_ID,
        email=IDENTITY.email,
        display_name=IDENTITY.display_name,
        is_active=True,
        roles=frozenset({"member"}),
    )

    class RacingRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.find_calls = 0

        def find_by_identity(
            self,
            issuer: str,
            subject: str,
        ) -> UserAccount | None:
            self.find_calls += 1
            return None if self.find_calls == 1 else winning_account

        def create_with_identity_and_role(
            self,
            identity: ValidatedIdentity,
            role_name: str,
        ) -> UserAccount:
            raise IdentityAlreadyAssignedError

    repository = RacingRepository()
    unit_of_work = FakeUnitOfWork(repository)

    current_user = ResolveCurrentUser().execute(IDENTITY, unit_of_work)

    assert current_user.id == winning_account.id
    assert unit_of_work.transactions == 2
    assert unit_of_work.rollbacks == 1
    assert unit_of_work.commits == 1
