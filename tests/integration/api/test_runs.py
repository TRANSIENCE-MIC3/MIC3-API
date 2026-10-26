from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mic3_api.application.authentication import InvalidAccessTokenError
from mic3_api.application.runs.admission import RunAdmissionExceeded, RunAdmissionLimits
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.infrastructure.persistence import OutboxEvent, Run, User
from mic3_api.infrastructure.persistence.execution import RunArtifact
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_queries import SqlAlchemyRunQueries
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from mic3_api.main import create_app
from tests.catalog_data import PARAMETERS
from tests.integration.api.test_admin_users import grant
from tests.integration.api.test_current_user import IDENTITY, StubTokenValidator
from tests.integration.infrastructure.test_model_runs import register, submit

HEADERS = {"Authorization": "Bearer valid"}


@pytest.fixture
def run_api(migrated_engine, postgres_test_settings):
    release = register(migrated_engine)
    validator = StubTokenValidator()
    with TestClient(create_app(settings=postgres_test_settings, token_validator=validator)) as client:
        owner = UUID(client.get("/users/me", headers=HEADERS).json()["id"])
        yield client, validator, owner, release


def request_body(release):
    return {"model_release_id": str(release.id), "parameters": PARAMETERS}


def test_submit_detail_history_and_operator_access(run_api, migrated_engine):
    client, validator, owner, release = run_api
    body = request_body(release)
    first = client.post("/runs", headers=HEADERS, json=body)
    second = client.post("/runs", headers=HEADERS, json=body)
    assert first.status_code == second.status_code == 202
    run_id = first.json()["id"]
    assert first.headers["location"] == f"/runs/{run_id}"
    assert first.json() == dict(id=run_id, status="queued", **body)
    assert second.json()["id"] != run_id
    detail = client.get(first.headers["location"], headers=HEADERS).json()
    assert detail["requested_by"] == str(owner)
    assert detail["status"] == "queued" and detail["created_at"]
    assert detail["artifacts"] == [] and detail["accepted_at"] is None
    assert detail["dispatch_started_at"] is None and detail["finished_at"] is None
    assert detail["failure_code"] is None
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))) == 2
        session.rollback()
        anonymous = submit(session, release.id)
    assert client.get(f"/runs/{anonymous.id}", headers=HEADERS).status_code == 403
    validator.identity = replace(IDENTITY, subject="another-user")
    other = UUID(client.get("/users/me", headers=HEADERS).json()["id"])
    assert client.get(f"/runs/{run_id}", headers=HEADERS).status_code == 403
    assert client.get("/runs", headers=HEADERS).json()["items"] == []
    grant(migrated_engine, other)
    assert client.get(f"/runs/{run_id}", headers=HEADERS).status_code == 200
    assert client.get(f"/runs/{anonymous.id}", headers=HEADERS).json()["requested_by"] is None
    assert client.get("/runs", headers=HEADERS).json()["items"] == []
    validator.identity = IDENTITY
    listing = client.get("/runs", headers=HEADERS).json()
    assert listing["limit"] == 50 and listing["offset"] == 0
    assert {r["id"] for r in listing["items"]} == {run_id, second.json()["id"]}
    assert all("artifacts" not in r for r in listing["items"])
    for offset in range(2):
        assert client.get(f"/runs?limit=1&offset={offset}", headers=HEADERS).json()["items"] == listing["items"][offset:offset+1]
    assert client.get("/runs?offset=100", headers=HEADERS).json()["items"] == []
    assert client.get(f"/runs/{uuid4()}", headers=HEADERS).status_code == 404

    # Equal creation timestamps still have a stable UUID tie-breaker.
    with Session(migrated_engine) as session, session.begin():
        session.execute(update(Run).where(Run.requested_by == owner).values(created_at=datetime.now(timezone.utc)))
    tied = client.get("/runs", headers=HEADERS).json()["items"]
    assert [item["id"] for item in tied] == sorted([run_id, second.json()["id"]], reverse=True)


@pytest.mark.parametrize("state", ["queued", "running", "succeeded", "failed"])
def test_lifecycle_and_safe_artifact_metadata(run_api, migrated_engine, state):
    client, _, owner, release = run_api
    now = datetime.now(timezone.utc)
    with Session(migrated_engine) as session:
        run = submit(session, release.id, owner)
        with session.begin():
            row = session.get(Run, run.id)
            row.status = state
            row.accepted_at = now
            row.dispatch_started_at = now if state != "queued" else None
            row.finished_at = now if state in ("succeeded", "failed") else None
            row.failure_code = "model_failed" if state == "failed" else None
            row.job_namespace = "private-namespace"
            row.job_uid = "private-uid"
            if row.finished_at:
                session.add(RunArtifact(run_id=run.id, bucket="private-bucket", object_key="private-prefix/folder/result.csv", size_bytes=123))
    response = client.get(f"/runs/{run.id}", headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == state and result["accepted_at"] is not None
    assert result["artifacts"] == ([{"name": "result.csv", "size_bytes": 123}] if state in ("succeeded", "failed") else [])
    assert result["failure_code"] == ("model_failed" if state == "failed" else None)
    assert "private" not in response.text
    assert not {"events", "fingerprint", "job_uid", "job_namespace"} & result.keys()


def test_validation_and_inactive_account(run_api, migrated_engine):
    client, _, owner, release = run_api
    body = request_body(release)
    for invalid in [dict(body, requested_by=str(owner)), dict(body, command=["shell"]),
                    dict(body, model_release_id="bad"), dict(body, parameters={}),
                    dict(body, parameters={"mode": "unknown"}), dict(body, parameters=[]),
                    dict(body, parameters=dict(PARAMETERS, extra=True)), {}]:
        assert client.post("/runs", headers=HEADERS, json=invalid).status_code == 422
    assert client.post("/runs", headers=HEADERS, json=dict(body, model_release_id=str(uuid4()))).status_code == 404
    for query in ["limit=0", "limit=101", "offset=-1", "limit=bad"]:
        assert client.get(f"/runs?{query}", headers=HEADERS).status_code == 422
    assert client.get("/runs/not-a-uuid", headers=HEADERS).status_code == 422
    with Session(migrated_engine) as session, session.begin():
        assert session.scalar(select(func.count()).select_from(Run)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        session.execute(update(User).where(User.id == owner).values(is_active=False))
    for method, path, kwargs in [("post", "/runs", {"json": body}), ("get", "/runs", {}), ("get", f"/runs/{uuid4()}", {})]:
        assert getattr(client, method)(path, headers=HEADERS, **kwargs).status_code == 403


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer invalid"])
def test_authentication_required(test_settings, authorization):
    validator = StubTokenValidator()
    validator.error = InvalidAccessTokenError()
    headers = {"Authorization": authorization} if authorization else {}
    with TestClient(create_app(settings=test_settings, token_validator=validator)) as client:
        for method, path, kwargs in [("post", "/runs", {"json": {"model_release_id": str(uuid4()), "parameters": PARAMETERS}}),
                                     ("get", "/runs", {}), ("get", f"/runs/{uuid4()}", {})]:
            response = getattr(client, method)(path, headers=headers, **kwargs)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"


def test_limits_cli_bypass_and_terminal_capacity(run_api, migrated_engine):
    client, _, owner, release = run_api
    body = request_body(release)
    assert all(client.post("/runs", headers=HEADERS, json=body).status_code == 202 for _ in range(5))
    assert client.post("/runs", headers=HEADERS, json=body).status_code == 429
    grant(migrated_engine, owner)
    with Session(migrated_engine) as session, session.begin():
        session.execute(update(Run).values(status="running"))
    assert client.post("/runs", headers=HEADERS, json=body).status_code == 429
    with Session(migrated_engine) as session:
        submit(session, release.id, owner)  # CLI bypasses the per-user cap.
        with session.begin():
            session.execute(update(Run).values(status="succeeded"))
        for _ in range(20):
            submit(session, release.id)  # Unattributed operator work counts globally.
    assert client.post("/runs", headers=HEADERS, json=body).status_code == 429
    with Session(migrated_engine) as session, session.begin():
        row = session.scalar(select(Run).where(Run.status == "queued").limit(1))
        row.status = "failed"
    assert client.post("/runs", headers=HEADERS, json=body).status_code == 202


@pytest.mark.parametrize("per_user,global_total,different_users", [(1, 20, False), (5, 1, True)])
def test_concurrent_admission(migrated_engine, per_user, global_total, different_users):
    release = register(migrated_engine)
    users = [uuid4(), uuid4()]
    with Session(migrated_engine) as session, session.begin():
        session.add_all([User(id=user) for user in users])
    def attempt(index):
        with Session(migrated_engine) as session:
            try:
                SubmitRun(SqlAlchemyModelCatalog(session), SqlAlchemyRunSubmissions(
                    session, RunAdmissionLimits(per_user, global_total),
                )).execute(release.id, PARAMETERS, users[index] if different_users else users[0])
                return True
            except RunAdmissionExceeded:
                assert not session.in_transaction()
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


def test_api_rollback_and_database_errors(run_api, migrated_engine, monkeypatch):
    client, _, _, release = run_api
    # Intercept after the run insert, before the outbox insert, on the API session.
    original = Session.flush
    def fail_flush(session, *args, **kwargs):
        if any(isinstance(row, OutboxEvent) for row in session.new):
            raise OperationalError("private-query", {}, Exception("private-error"))
        return original(session, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail_flush)
        response = client.post("/runs", headers=HEADERS, json=request_body(release))
        assert response.status_code == 503 and "private" not in response.text
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert client.post("/runs", headers=HEADERS, json=request_body(release)).status_code == 202
    def unavailable(*args, **kwargs):
        raise OperationalError("private-query", {}, Exception("private-error"))
    monkeypatch.setattr(SqlAlchemyRunQueries, "find", unavailable)
    monkeypatch.setattr(SqlAlchemyRunQueries, "list_owned", unavailable)
    for path in ["/runs", f"/runs/{uuid4()}"]:
        response = client.get(path, headers=HEADERS)
        assert response.status_code == 503 and "private" not in response.text


def test_query_snapshot_survives_concurrent_terminal_commit(run_api, migrated_engine):
    _, _, owner, release = run_api
    with Session(migrated_engine) as session:
        run = submit(session, release.id, owner)
    committed = False
    def finish_after_read(connection, cursor, statement, parameters, context, executemany):
        nonlocal committed
        if "LEFT OUTER JOIN run_artifacts" in statement and not committed:
            committed = True
            with Session(migrated_engine) as other, other.begin():
                row = other.get(Run, run.id)
                row.status = "succeeded"
                row.finished_at = datetime.now(timezone.utc)
                other.add(RunArtifact(run_id=run.id, bucket="bucket", object_key="outputs/result.csv", size_bytes=1))
    event.listen(migrated_engine, "after_cursor_execute", finish_after_read)
    try:
        with Session(migrated_engine) as session:
            snapshot = SqlAlchemyRunQueries(session).find(run.id)
            assert committed and snapshot.status == "queued" and snapshot.artifacts == ()
            assert not session.in_transaction()
            latest = SqlAlchemyRunQueries(session).find(run.id)
            assert latest.status == "succeeded" and len(latest.artifacts) == 1
    finally:
        event.remove(migrated_engine, "after_cursor_execute", finish_after_read)
