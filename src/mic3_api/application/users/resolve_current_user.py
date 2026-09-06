"""Use case for resolving or safely provisioning an authenticated user."""

from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.users.ports import (
    IdentityAlreadyAssignedError,
    InitialRoleNotFoundError,
    UserAccountUnitOfWork,
)
from mic3_api.application.users.user_account import (
    CurrentUser,
    UserAccount,
    UserProfileUpdates,
)


MEMBER_ROLE = "member"


class InactiveUserError(Exception):
    """The external identity maps to a disabled local account."""


class UserProvisioningConfigurationError(Exception):
    """A required local provisioning invariant is missing."""


class IdentityProvisioningError(Exception):
    """A concurrent identity assignment could not be resolved safely."""


class ResolveCurrentUser:
    """Resolve an exact identity and provision a non-elevated user if needed."""

    def execute(
        self,
        identity: ValidatedIdentity,
        unit_of_work: UserAccountUnitOfWork,
    ) -> CurrentUser:
        """Return the active local user for a trusted external identity."""
        try:
            return self._resolve_once(identity, unit_of_work)
        except IdentityAlreadyAssignedError:
            return self._resolve_after_concurrent_assignment(identity, unit_of_work)
        except InitialRoleNotFoundError as exc:
            raise UserProvisioningConfigurationError from exc

    def _resolve_once(
        self,
        identity: ValidatedIdentity,
        unit_of_work: UserAccountUnitOfWork,
    ) -> CurrentUser:
        with unit_of_work.transaction():
            account = unit_of_work.users.find_by_identity(
                identity.issuer,
                identity.subject,
            )
            if account is None:
                account = unit_of_work.users.create_with_identity_and_role(
                    identity,
                    MEMBER_ROLE,
                )
            else:
                account = self._synchronize_profile(
                    account,
                    identity,
                    unit_of_work,
                )

            return self._as_current_user(account)

    def _resolve_after_concurrent_assignment(
        self,
        identity: ValidatedIdentity,
        unit_of_work: UserAccountUnitOfWork,
    ) -> CurrentUser:
        with unit_of_work.transaction():
            account = unit_of_work.users.find_by_identity(
                identity.issuer,
                identity.subject,
            )
            if account is None:
                raise IdentityProvisioningError

            account = self._synchronize_profile(account, identity, unit_of_work)
            return self._as_current_user(account)

    @staticmethod
    def _synchronize_profile(
        account: UserAccount,
        identity: ValidatedIdentity,
        unit_of_work: UserAccountUnitOfWork,
    ) -> UserAccount:
        if not account.is_active:
            raise InactiveUserError

        updates = UserProfileUpdates(
            email=(
                identity.email
                if identity.email is not None and identity.email != account.email
                else None
            ),
            display_name=(
                identity.display_name
                if identity.display_name is not None
                and identity.display_name != account.display_name
                else None
            ),
        )
        if not updates.has_changes:
            return account

        return unit_of_work.users.update_profile(account, updates)

    @staticmethod
    def _as_current_user(account: UserAccount) -> CurrentUser:
        if not account.is_active:
            raise InactiveUserError

        return CurrentUser(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            roles=tuple(sorted(account.roles)),
        )
