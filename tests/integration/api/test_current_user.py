from dataclasses import replace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mic3_api.application.authentication import IdentityProviderUnavailableError, InvalidAccessTokenError
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.persistence import Role, User, UserIdentity, UserRole
from mic3_api.main import create_app
from tests.integration.factories import AUTH, IDENTITY, StubTokenValidator, count

PROTECTED = [("get", "/users/me"), ("get", "/users"), ("post", "/runs"), ("get", "/runs"),
             ("get", f"/runs/{uuid4()}"), ("get", "/runs/events")]


class TrackingDatabase(Database):
    """Records each session's close() so tests can check every request releases its session."""

    def __init__(self, settings) -> None:
        super().__init__(settings.database_url)
        self.close_trackers: list[MagicMock] = []

    def open_session(self) -> Session:
        session = super().open_session()
        session.close = MagicMock(wraps=session.close)
        self.close_trackers.append(session.close)
        return session


def test_first_request_provisions_member_and_repeated_request_is_idempotent(migrated_engine, postgres_test_settings):
    validator = StubTokenValidator()
    database = TrackingDatabase(postgres_test_settings)
    with TestClient(create_app(settings=postgres_test_settings, database=database,
                               token_validator=validator)) as client:
        first = client.get("/users/me", headers={"Authorization": "Bearer first-token"})
        second = client.get("/users/me", headers={"Authorization": "Bearer second-token"})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {
        "id": first.json()["id"], "email": "member@example.org",
        "display_name": "Member Name", "roles": ["member"],
    }
    UUID(first.json()["id"])
    assert validator.tokens == ["first-token", "second-token"]
    assert count(migrated_engine, User) == count(migrated_engine, UserIdentity) == count(migrated_engine, UserRole) == 1
    assert len(database.close_trackers) == 2
    for close in database.close_trackers:
        close.assert_called_once_with()


def test_existing_profile_updates_but_missing_claims_retain_last_values(client, validator):
    initial = client.get("/users/me").json()
    validator.identity = replace(IDENTITY, email="changed@example.org", display_name=None)
    updated = client.get("/users/me").json()
    assert updated["id"] == initial["id"]
    assert updated["email"] == "changed@example.org"
    assert updated["display_name"] == "Member Name"


def test_inactive_user_returns_forbidden(client, migrated_engine):
    user_id = UUID(client.get("/users/me").json()["id"])
    with Session(migrated_engine) as session, session.begin():
        session.execute(update(User).where(User.id == user_id).values(is_active=False))
    response = client.get("/users/me")
    assert response.status_code == 403
    assert response.json() == {"detail": "The MIC3 account is inactive."}


def test_missing_member_role_rolls_back_every_provisioning_record(client, migrated_engine):
    with Session(migrated_engine) as session, session.begin():
        session.execute(delete(Role).where(Role.name == "member"))
    assert client.get("/users/me").status_code == 500
    assert count(migrated_engine, User) == count(migrated_engine, UserIdentity) == count(migrated_engine, UserRole) == 0


@pytest.mark.parametrize("authorization", [None, "Basic credentials", "Bearer invalid-token"])
def test_protected_endpoints_require_a_valid_bearer_token(offline_client, validator, authorization):
    validator.error = InvalidAccessTokenError()
    headers = {"Authorization": authorization} if authorization else {}
    for method, path in PROTECTED:
        response = getattr(offline_client, method)(path, headers=headers)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"


def test_oidc_outage_returns_service_unavailable(offline_client, validator):
    validator.error = IdentityProviderUnavailableError()
    assert offline_client.get("/users/me", headers=AUTH).status_code == 503


def test_database_session_failure_returns_service_unavailable(test_settings):
    class UnavailableDatabase:
        def ping(self) -> None: ...
        def dispose(self) -> None: ...

        def open_session(self) -> Session:
            raise OperationalError("connect", {}, Exception("unavailable"))

    with TestClient(create_app(settings=test_settings, database=UnavailableDatabase(),
                               token_validator=StubTokenValidator())) as client:
        assert client.get("/users/me", headers=AUTH).status_code == 503
