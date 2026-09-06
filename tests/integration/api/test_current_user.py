from dataclasses import replace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mic3_api.application.authentication import (
    IdentityProviderUnavailableError,
    InvalidAccessTokenError,
    ValidatedIdentity,
)
from mic3_api.core.config import Settings
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.persistence import Role, User, UserIdentity, UserRole
from mic3_api.main import create_app


IDENTITY = ValidatedIdentity(
    issuer="https://issuer.example/realms/mic3",
    subject="Case-Sensitive-Subject",
    email="member@example.org",
    display_name="Member Name",
)


class StubTokenValidator:
    def __init__(self, identity: ValidatedIdentity = IDENTITY) -> None:
        self.identity = identity
        self.error: Exception | None = None
        self.tokens: list[str] = []

    def validate(self, access_token: str) -> ValidatedIdentity:
        self.tokens.append(access_token)
        if self.error is not None:
            raise self.error
        return self.identity


class TrackingDatabase(Database):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.database_url)
        self.close_trackers: list[MagicMock] = []

    def open_session(self) -> Session:
        session = super().open_session()
        close_tracker = MagicMock(wraps=session.close)
        session.close = close_tracker
        self.close_trackers.append(close_tracker)
        return session


def test_first_request_provisions_member_and_repeated_request_is_idempotent(
    migrated_engine: Engine,
    postgres_test_settings: Settings,
) -> None:
    validator = StubTokenValidator()
    database = TrackingDatabase(postgres_test_settings)

    with TestClient(
        create_app(
            settings=postgres_test_settings,
            database=database,
            token_validator=validator,
        )
    ) as client:
        first = client.get(
            "/users/me",
            headers={"Authorization": "Bearer first-token"},
        )
        second = client.get(
            "/users/me",
            headers={"Authorization": "Bearer second-token"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json() == {
        "id": first.json()["id"],
        "email": "member@example.org",
        "display_name": "Member Name",
        "roles": ["member"],
    }
    UUID(first.json()["id"])
    assert validator.tokens == ["first-token", "second-token"]

    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(UserIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(UserRole)) == 1

    assert len(database.close_trackers) == 2
    for close_tracker in database.close_trackers:
        close_tracker.assert_called_once_with()


def test_existing_profile_updates_but_missing_claims_retain_last_values(
    migrated_engine: Engine,
    postgres_test_settings: Settings,
) -> None:
    validator = StubTokenValidator()

    with TestClient(
        create_app(settings=postgres_test_settings, token_validator=validator)
    ) as client:
        initial = client.get(
            "/users/me",
            headers={"Authorization": "Bearer token"},
        )
        validator.identity = replace(
            IDENTITY,
            email="changed@example.org",
            display_name=None,
        )
        updated = client.get(
            "/users/me",
            headers={"Authorization": "Bearer token"},
        )

    assert initial.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["id"] == initial.json()["id"]
    assert updated.json()["email"] == "changed@example.org"
    assert updated.json()["display_name"] == "Member Name"


def test_inactive_user_returns_forbidden(
    migrated_engine: Engine,
    postgres_test_settings: Settings,
) -> None:
    validator = StubTokenValidator()

    with TestClient(
        create_app(settings=postgres_test_settings, token_validator=validator)
    ) as client:
        provisioned = client.get(
            "/users/me",
            headers={"Authorization": "Bearer token"},
        )
        with Session(migrated_engine) as session, session.begin():
            session.execute(
                update(User)
                .where(User.id == UUID(provisioned.json()["id"]))
                .values(is_active=False)
            )

        response = client.get(
            "/users/me",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "The MIC3 account is inactive."}


def test_missing_member_role_rolls_back_every_provisioning_record(
    migrated_engine: Engine,
    postgres_test_settings: Settings,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        session.execute(delete(Role).where(Role.name == "member"))

    with TestClient(
        create_app(
            settings=postgres_test_settings,
            token_validator=StubTokenValidator(),
        )
    ) as client:
        response = client.get(
            "/users/me",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 500
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
        assert session.scalar(select(func.count()).select_from(UserIdentity)) == 0
        assert session.scalar(select(func.count()).select_from(UserRole)) == 0


@pytest.mark.parametrize(
    ("authorization", "validator_error"),
    [
        (None, None),
        ("Basic credentials", None),
        ("Bearer invalid-token", InvalidAccessTokenError()),
    ],
)
def test_missing_or_invalid_bearer_token_returns_unauthorized(
    test_settings: Settings,
    authorization: str | None,
    validator_error: Exception | None,
) -> None:
    validator = StubTokenValidator()
    validator.error = validator_error
    headers = {"Authorization": authorization} if authorization else {}

    with TestClient(
        create_app(settings=test_settings, token_validator=validator)
    ) as client:
        response = client.get("/users/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_oidc_outage_returns_service_unavailable(test_settings: Settings) -> None:
    validator = StubTokenValidator()
    validator.error = IdentityProviderUnavailableError()

    with TestClient(
        create_app(settings=test_settings, token_validator=validator)
    ) as client:
        response = client.get(
            "/users/me",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 503


def test_database_session_failure_returns_service_unavailable(
    test_settings: Settings,
) -> None:
    class UnavailableDatabase:
        def ping(self) -> None:
            return None

        def open_session(self) -> Session:
            raise OperationalError("connect", {}, Exception("unavailable"))

        def dispose(self) -> None:
            return None

    with TestClient(
        create_app(
            settings=test_settings,
            database=UnavailableDatabase(),
            token_validator=StubTokenValidator(),
        )
    ) as client:
        response = client.get(
            "/users/me",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 503
