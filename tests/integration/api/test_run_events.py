from contextlib import contextmanager
from dataclasses import replace
import json
import socket
from threading import Thread
import time
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import text, update
from sqlalchemy.orm import Session
import uvicorn

from mic3_api.infrastructure.persistence.execution import Run, RunArtifact
from mic3_api.infrastructure.persistence.users import User
from mic3_api.main import create_app
from tests.integration.api.test_current_user import IDENTITY, StubTokenValidator
from tests.integration.api.test_admin_users import grant
from tests.integration.infrastructure.test_model_runs import register, submit

pytestmark = pytest.mark.live_notifications


def wait_until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "Condition did not become true"
        time.sleep(0.02)


@contextmanager
def live_api(settings, validator):
    app = create_app(settings=settings, token_validator=validator)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=2))
        thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            wait_until(lambda: server.started and app.state.run_feed._available)
            with httpx2.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
                               headers={"Authorization": "Bearer valid"}, timeout=5) as client:
                yield client, app
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def next_frame(lines):
    event, data = None, None
    for line in lines:
        if line.startswith(":"):
            return "heartbeat", None
        if line.startswith("event: "):
            event = line[7:]
        if line.startswith("data: "):
            data = json.loads(line[6:])
        if line == "" and event:
            return event, data
    return None, None


def test_real_stream_snapshot_notifications_terminal_and_reconnect(migrated_engine, postgres_test_settings, monkeypatch):
    monkeypatch.setattr("mic3_api.api.routes.run_events.HEARTBEAT_SECONDS", 0.2)
    validator = StubTokenValidator(replace(IDENTITY, expires_at=time.time() + 120))
    release = register(migrated_engine)
    with live_api(postgres_test_settings, validator) as (client, app):
        owner = UUID(client.get("/users/me").json()["id"])
        with client.stream("GET", "/runs/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.iter_lines()
            assert next_frame(lines) == ("runs.snapshot", {"items": []})
            with Session(migrated_engine) as session:
                run = submit(session, release.id, owner)
            def next_update():
                while True:
                    event, payload = next_frame(lines)
                    assert event is not None
                    if event != "heartbeat":
                        return event, payload
            name, data = next_update()
            assert name == "run.updated" and data["id"] == str(run.id) and data["status"] == "queued"
            with Session(migrated_engine) as session, session.begin():
                session.get(Run, run.id).status = "succeeded"
                session.add(RunArtifact(run_id=run.id, object_key="private/result.csv", bucket="hidden", size_bytes=123))
            name, data = next_update()
            assert name == "run.updated" and data["status"] == "succeeded"
            assert data["artifacts"] == [{"name": "result.csv", "size_bytes": 123}]
            assert data == client.get(f"/runs/{run.id}").json()
            assert "hidden" not in json.dumps(data)
            assert next_frame(lines)[0] == "heartbeat"  # Terminal run does not close personal feed.
        wait_until(lambda: not app.state.run_feed._subscriptions)
        with client.stream("GET", "/runs/events", headers={"Last-Event-ID": "ignored"}) as response:
            name, data = next_frame(response.iter_lines())
            assert name == "runs.snapshot" and data["items"][0]["status"] == "succeeded"


def test_expiry_inactive_and_initial_errors(migrated_engine, postgres_test_settings, monkeypatch):
    monkeypatch.setattr("mic3_api.api.routes.run_events.HEARTBEAT_SECONDS", 0.1)
    validator = StubTokenValidator(replace(IDENTITY, expires_at=time.time() + 120))
    with live_api(postgres_test_settings, validator) as (client, app):
        owner = UUID(client.get("/users/me").json()["id"])
        assert client.get("/runs/events", headers={"Authorization": ""}).status_code == 401
        validator.identity = replace(validator.identity, expires_at=time.time() - 1)
        assert client.get("/runs/events").status_code == 401
        validator.identity = replace(validator.identity, expires_at=time.time() + 0.6)
        with client.stream("GET", "/runs/events") as response:
            frames = [line for line in response.iter_lines()]
            assert any('"code":"token_expired"' in line for line in frames)
        validator.identity = replace(validator.identity, expires_at=time.time() + 120)
        with client.stream("GET", "/runs/events") as response:
            lines = response.iter_lines()
            assert next_frame(lines)[0] == "runs.snapshot"
            with Session(migrated_engine) as session, session.begin():
                session.execute(update(User).where(User.id == owner).values(is_active=False))
            assert any('"code":"access_denied"' in line for line in lines)
        assert client.get("/runs/events").status_code == 403
        with Session(migrated_engine) as session, session.begin():
            session.execute(update(User).where(User.id == owner).values(is_active=True))
        app.state.run_feed.unavailable()
        assert client.get("/runs/events").status_code == 503


def test_personal_isolation_limits_and_two_listeners(migrated_engine, postgres_test_settings, monkeypatch):
    monkeypatch.setattr("mic3_api.api.routes.run_events.HEARTBEAT_SECONDS", 0.1)
    settings = postgres_test_settings.model_copy(update={"sse_max_streams_per_user": 1})
    first = StubTokenValidator(replace(IDENTITY, expires_at=time.time() + 120))
    second = StubTokenValidator(first.identity)
    release = register(migrated_engine)
    with live_api(settings, first) as (one, app1), live_api(settings, second) as (two, app2):
        owner = UUID(one.get("/users/me").json()["id"])
        with one.stream("GET", "/runs/events") as r1, two.stream("GET", "/runs/events") as r2:
            l1, l2 = r1.iter_lines(), r2.iter_lines()
            assert next_frame(l1)[0] == next_frame(l2)[0] == "runs.snapshot"
            assert one.get("/runs/events").status_code == 429
            with Session(migrated_engine) as session:
                run = submit(session, release.id, owner)
            for lines in (l1, l2):
                event, data = next_frame(lines)
                while event == "heartbeat":
                    event, data = next_frame(lines)
                assert event == "run.updated" and data["id"] == str(run.id)
        first.identity = replace(first.identity, subject="different-member")
        other = UUID(one.get("/users/me").json()["id"])
        grant(migrated_engine, other)  # Even admins have a personal-only stream.
        with one.stream("GET", "/runs/events") as response:
            lines = response.iter_lines()
            assert next_frame(lines) == ("runs.snapshot", {"items": []})
            with Session(migrated_engine) as session:
                submit(session, release.id, owner)
            for _ in range(8):
                assert next_frame(lines)[0] == "heartbeat"


def test_listener_loss_closes_stream_and_reconnect_restores_snapshot(migrated_engine, postgres_test_settings):
    validator = StubTokenValidator(replace(IDENTITY, expires_at=time.time() + 120))
    with live_api(postgres_test_settings, validator) as (client, app):
        with client.stream("GET", "/runs/events") as response:
            lines = response.iter_lines()
            assert next_frame(lines)[0] == "runs.snapshot"
            with migrated_engine.begin() as connection:
                pids = connection.scalars(text("SELECT pid FROM pg_stat_activity WHERE application_name = 'mic3-run-notifications' AND datname = current_database()" )).all()
                assert len(pids) == 1
                connection.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pids[0]})
            assert next_frame(lines) == ("stream.error", {"code": "unavailable"})
            assert next_frame(lines) == (None, None)
        wait_until(lambda: app.state.run_feed._available)
        with client.stream("GET", "/runs/events") as response:
            assert next_frame(response.iter_lines()) == ("runs.snapshot", {"items": []})


def test_snapshot_includes_all_active_and_only_fifty_newest_terminal(migrated_engine, postgres_test_settings):
    from mic3_api.infrastructure.database import Database
    from mic3_api.infrastructure.persistence.run_feed import SqlAlchemyRunFeed
    from uuid import uuid4
    release = register(migrated_engine)
    owner = uuid4()
    with Session(migrated_engine) as session:
        with session.begin():
            session.add(User(id=owner))
        active = submit(session, release.id, owner)
        ids = [submit(session, release.id, owner).id for _ in range(52)]
        with session.begin():
            session.execute(update(Run).where(Run.id.in_(ids)).values(status="succeeded"))
    database = Database(postgres_test_settings.database_url)
    try:
        snapshot = SqlAlchemyRunFeed(database).snapshot(owner)
        assert len(snapshot) == 51
        assert snapshot[-1].id == active.id
        assert [r.id for r in snapshot[:-1]] == list(reversed(ids[2:]))
    finally:
        database.dispose()
