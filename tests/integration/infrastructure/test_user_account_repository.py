from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.users import ResolveCurrentUser, UserAccount
from mic3_api.infrastructure.persistence import User, UserIdentity, UserRole
from mic3_api.infrastructure.persistence.user_accounts import (
    SqlAlchemyUserAccountRepository,
)


def test_concurrent_first_requests_resolve_one_atomic_account(
    migrated_engine: Engine,
) -> None:
    first_lookup = Barrier(2)

    class BarrierRepository(SqlAlchemyUserAccountRepository):
        def __init__(self, session: Session) -> None:
            super().__init__(session)
            self._waited = False

        def find_by_identity(
            self,
            issuer: str,
            subject: str,
        ) -> UserAccount | None:
            account = super().find_by_identity(issuer, subject)
            if account is None and not self._waited:
                self._waited = True
                first_lookup.wait(timeout=5)
            return account

    class BarrierUnitOfWork:
        def __init__(self, session: Session) -> None:
            self._session = session
            self.users = BarrierRepository(session)

        def transaction(self):
            return self._session.begin()

    def resolve(email: str):
        identity = ValidatedIdentity(
            issuer="https://issuer.example/realms/mic3",
            subject="one-concurrent-subject",
            email=email,
            display_name="Concurrent Member",
        )
        with Session(migrated_engine) as session:
            return ResolveCurrentUser().execute(
                identity,
                BarrierUnitOfWork(session),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(resolve, "first@example.org"),
            executor.submit(resolve, "second@example.org"),
        ]
        resolved_users = [future.result(timeout=10) for future in futures]

    assert resolved_users[0].id == resolved_users[1].id
    assert resolved_users[0].roles == ("member",)
    assert resolved_users[1].roles == ("member",)
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(UserIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(UserRole)) == 1
