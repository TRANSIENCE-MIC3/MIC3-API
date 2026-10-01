from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from mic3_api.application.users.administration import AdministrationError, GrantAdmin
from mic3_api.infrastructure.persistence import Role, User, UserRole
from mic3_api.infrastructure.persistence.user_administration import SqlAlchemyUserAdministrationUnitOfWork as Uow
from mic3_api.main import create_app
from tests.integration.api.test_current_user import StubTokenValidator


def grant(engine, user_id):
    with Session(engine) as session:
        return GrantAdmin().execute(user_id, Uow(session))


def test_directory_and_grant(migrated_engine, postgres_test_settings):
    with TestClient(create_app(settings=postgres_test_settings, token_validator=StubTokenValidator())) as client:
        headers = {"Authorization": "Bearer valid"}
        profile = client.get("/users/me", headers=headers).json()
        user_id = UUID(profile["id"])
        assert client.get("/users", headers=headers).status_code == 403
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(lambda _: grant(migrated_engine, user_id), range(2))) == [False, True]
        assert grant(migrated_engine, user_id) is False
        assert client.get("/users/me", headers=headers).json()["roles"] == ["admin", "member"]
        other_id = uuid4()
        with Session(migrated_engine) as session, session.begin():
            session.add(User(id=other_id, is_active=False))
        response = client.get("/users", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["limit"] == 50 and body["offset"] == 0
        assert [u["id"] for u in body["items"]] == sorted([str(user_id), str(other_id)])
        other = next(u for u in body["items"] if u["id"] == str(other_id))
        assert other == dict(id=str(other_id), email=None, display_name=None, roles=[], is_active=False)
        for offset in range(2):
            page = client.get(f"/users?limit=1&offset={offset}", headers=headers).json()
            assert page["items"] == body["items"][offset:offset+1]
        assert client.get("/users?offset=20", headers=headers).json()["items"] == []
        for query in ["limit=0", "limit=101", "offset=-1", "limit=bad"]:
            assert client.get(f"/users?{query}", headers=headers).status_code == 422
        with Session(migrated_engine) as session, session.begin():
            session.execute(update(User).where(User.id == user_id).values(is_active=False))
        assert client.get("/users", headers=headers).status_code == 403


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer invalid"])
def test_unauthorized(test_settings, authorization):
    from mic3_api.application.authentication import InvalidAccessTokenError
    validator = StubTokenValidator()
    validator.error = InvalidAccessTokenError()
    with TestClient(create_app(settings=test_settings, token_validator=validator)) as client:
        response = client.get("/users", headers={"Authorization": authorization} if authorization else {})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_grant_failures_leave_no_assignments(migrated_engine):
    missing = uuid4()
    with pytest.raises(AdministrationError, match="does not exist"):
        grant(migrated_engine, missing)
    with Session(migrated_engine) as session, session.begin():
        session.add(User(id=missing, is_active=False))
    with pytest.raises(AdministrationError, match="inactive"):
        grant(migrated_engine, missing)
    with Session(migrated_engine) as session, session.begin():
        session.execute(update(User).values(is_active=True))
        session.execute(delete(Role).where(Role.name == "admin"))
    with pytest.raises(AdministrationError, match="missing"):
        grant(migrated_engine, missing)
    with Session(migrated_engine) as session:
        assert list(session.scalars(select(UserRole))) == []


def test_listing_database_failure(migrated_engine, postgres_test_settings, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from mic3_api.infrastructure.persistence.user_administration import SqlAlchemyUserAdministrationRepository
    with TestClient(create_app(settings=postgres_test_settings, token_validator=StubTokenValidator())) as client:
        headers = {"Authorization": "Bearer valid"}
        user_id = UUID(client.get("/users/me", headers=headers).json()["id"])
        grant(migrated_engine, user_id)
        def fail(*args):
            raise OperationalError("query", {}, Exception("private"))
        monkeypatch.setattr(SqlAlchemyUserAdministrationRepository, "list_users", fail)
        response = client.get("/users", headers=headers)
        assert response.status_code == 503
        assert "private" not in response.text


def test_grant_write_failure_rolls_back_and_session_is_reusable(migrated_engine, monkeypatch):
    from mic3_api.infrastructure.persistence.user_administration import SqlAlchemyUserAdministrationRepository
    user_id = uuid4()
    with Session(migrated_engine) as session, session.begin():
        session.add(User(id=user_id))
    original = SqlAlchemyUserAdministrationRepository.add_role
    def fail_after_write(self, target, role):
        original(self, target, role)
        raise RuntimeError("injected failure")
    with Session(migrated_engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(SqlAlchemyUserAdministrationRepository, "add_role", fail_after_write)
            with pytest.raises(RuntimeError):
                GrantAdmin().execute(user_id, Uow(session))
        assert not session.in_transaction()
        with session.begin():
            assert list(session.scalars(select(UserRole))) == []
        assert GrantAdmin().execute(user_id, Uow(session)) is True


def test_cli_against_database_without_oidc(migrated_engine, monkeypatch, capsys):
    from mic3_api.cli import main
    user_id = uuid4()
    with Session(migrated_engine) as session, session.begin():
        session.add(User(id=user_id))
    monkeypatch.setenv("OIDC_ISSUER_URL", "")
    monkeypatch.setenv("OIDC_AUDIENCE", "")
    args = ["grant-admin", "--user-id", str(user_id)]
    assert main(args) == 0
    assert "granted" in capsys.readouterr().out
    assert main(args) == 0
    assert "already present" in capsys.readouterr().out
    assert main(["grant-admin", "--user-id", str(uuid4())]) == 1
