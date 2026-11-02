from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from mic3_api.application.users.administration import AdministrationError, GrantAdmin
from mic3_api.cli import main
from mic3_api.infrastructure.persistence import Role, User, UserRole
from mic3_api.infrastructure.persistence.user_administration import (
    SqlAlchemyUserAdministrationRepository, SqlAlchemyUserAdministrationUnitOfWork as Uow,
)
from tests.integration.factories import count, grant


def add_user(engine, **values) -> UUID:
    user_id = values.pop("id", uuid4())
    with Session(engine) as session, session.begin():
        session.add(User(id=user_id, **values))
    return user_id


def test_directory_and_grant(client, migrated_engine):
    user_id = UUID(client.get("/users/me").json()["id"])
    assert client.get("/users").status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: grant(migrated_engine, user_id), range(2))) == [False, True]
    assert grant(migrated_engine, user_id) is False
    assert client.get("/users/me").json()["roles"] == ["admin", "member"]
    other_id = add_user(migrated_engine, is_active=False)

    response = client.get("/users")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 50 and body["offset"] == 0
    assert [u["id"] for u in body["items"]] == sorted([str(user_id), str(other_id)])
    other = next(u for u in body["items"] if u["id"] == str(other_id))
    assert other == dict(id=str(other_id), email=None, display_name=None, roles=[], is_active=False)
    for offset in range(2):
        assert client.get(f"/users?limit=1&offset={offset}").json()["items"] == body["items"][offset:offset + 1]
    assert client.get("/users?offset=20").json()["items"] == []
    for query in ["limit=0", "limit=101", "offset=-1", "limit=bad"]:
        assert client.get(f"/users?{query}").status_code == 422
    with Session(migrated_engine) as session, session.begin():
        session.execute(update(User).where(User.id == user_id).values(is_active=False))
    assert client.get("/users").status_code == 403


def test_grant_failures_leave_no_assignments(migrated_engine):
    missing = uuid4()
    with pytest.raises(AdministrationError, match="does not exist"):
        grant(migrated_engine, missing)
    add_user(migrated_engine, id=missing, is_active=False)
    with pytest.raises(AdministrationError, match="inactive"):
        grant(migrated_engine, missing)
    with Session(migrated_engine) as session, session.begin():
        session.execute(update(User).values(is_active=True))
        session.execute(delete(Role).where(Role.name == "admin"))
    with pytest.raises(AdministrationError, match="missing"):
        grant(migrated_engine, missing)
    assert count(migrated_engine, UserRole) == 0


def test_grant_write_failure_rolls_back_and_session_is_reusable(migrated_engine, monkeypatch):
    user_id = add_user(migrated_engine)
    original = SqlAlchemyUserAdministrationRepository.add_role

    def fail_after_write(self, target, role):
        original(self, target, role)
        raise RuntimeError("injected failure")

    with Session(migrated_engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(SqlAlchemyUserAdministrationRepository, "add_role", fail_after_write)
            with pytest.raises(RuntimeError):
                GrantAdmin(Uow(session)).execute(user_id)
        assert not session.in_transaction()
        with session.begin():
            assert list(session.scalars(select(UserRole))) == []
        assert GrantAdmin(Uow(session)).execute(user_id) is True


def test_cli_against_database_without_oidc(migrated_engine, monkeypatch, capsys):
    user_id = add_user(migrated_engine)
    monkeypatch.setenv("OIDC_ISSUER_URL", "")
    monkeypatch.setenv("OIDC_AUDIENCE", "")
    args = ["grant-admin", "--user-id", str(user_id)]
    assert main(args) == 0
    assert "granted" in capsys.readouterr().out
    assert main(args) == 0
    assert "already present" in capsys.readouterr().out
    assert main(["grant-admin", "--user-id", str(uuid4())]) == 1
