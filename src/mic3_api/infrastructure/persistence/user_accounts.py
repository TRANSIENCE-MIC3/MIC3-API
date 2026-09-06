"""SQLAlchemy repository for the user account authentication aggregate."""

from dataclasses import replace

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.users import (
    IdentityAlreadyAssignedError,
    InitialRoleNotFoundError,
    UserAccount,
    UserProfileUpdates,
)
from mic3_api.infrastructure.persistence.roles import Role, UserRole
from mic3_api.infrastructure.persistence.users import User, UserIdentity


IDENTITY_PRIMARY_KEY = "pk_user_identities"


class SqlAlchemyUserAccountRepository:
    """Persist user accounts without owning sessions or transaction commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_identity(self, issuer: str, subject: str) -> UserAccount | None:
        """Return the account for an exact issuer and subject pair."""
        statement = (
            select(
                User.id,
                User.email,
                User.display_name,
                User.is_active,
                UserRole.role_name,
            )
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .outerjoin(UserRole, UserRole.user_id == User.id)
            .where(
                UserIdentity.issuer == issuer,
                UserIdentity.subject == subject,
            )
        )
        rows = self._session.execute(statement).all()
        if not rows:
            return None

        first = rows[0]
        return UserAccount(
            id=first.id,
            email=first.email,
            display_name=first.display_name,
            is_active=first.is_active,
            roles=frozenset(row.role_name for row in rows if row.role_name),
        )

    def create_with_identity_and_role(
        self,
        identity: ValidatedIdentity,
        role_name: str,
    ) -> UserAccount:
        """Stage all records required for an initially authorized account."""
        persisted_role = self._session.scalar(
            select(Role.name).where(Role.name == role_name)
        )
        if persisted_role is None:
            raise InitialRoleNotFoundError(role_name)

        user = User(
            email=identity.email,
            display_name=identity.display_name,
        )
        self._session.add(user)
        self._session.flush()

        self._session.add_all(
            [
                UserIdentity(
                    issuer=identity.issuer,
                    subject=identity.subject,
                    user_id=user.id,
                ),
                UserRole(user_id=user.id, role_name=role_name),
            ]
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            if self._constraint_name(exc) == IDENTITY_PRIMARY_KEY:
                raise IdentityAlreadyAssignedError from exc
            raise

        return UserAccount(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=frozenset({role_name}),
        )

    def update_profile(
        self,
        account: UserAccount,
        updates: UserProfileUpdates,
    ) -> UserAccount:
        """Stage only profile values that the application marked as changed."""
        values: dict[str, str] = {}
        if updates.email is not None:
            values["email"] = updates.email
        if updates.display_name is not None:
            values["display_name"] = updates.display_name

        if values:
            self._session.execute(
                update(User).where(User.id == account.id).values(**values)
            )
            self._session.flush()

        return replace(
            account,
            email=updates.email if updates.email is not None else account.email,
            display_name=(
                updates.display_name
                if updates.display_name is not None
                else account.display_name
            ),
        )

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        diagnostic = getattr(error.orig, "diag", None)
        return getattr(diagnostic, "constraint_name", None)
