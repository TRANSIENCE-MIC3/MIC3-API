from alembic import command
import psycopg
from sqlalchemy import text
from sqlalchemy.orm import Session

from mic3_api.infrastructure.persistence.execution import Run, RunArtifact
from tests.integration.infrastructure.test_model_runs import register, submit


def listen(settings):
    connection = psycopg.connect(host=settings.db_host, port=settings.db_port,
                                dbname=settings.db_name, user=settings.db_user,
                                password=settings.db_password.get_secret_value(), autocommit=True)
    connection.execute("LISTEN mic3_run_changes")
    return connection


def notifications(connection):
    return [item.payload for item in connection.notifies(timeout=0.15)]


def test_commits_rollback_artifacts_and_noop_updates(migrated_engine, postgres_test_settings):
    release = register(migrated_engine)
    with listen(postgres_test_settings) as listener, Session(migrated_engine) as session:
        run = submit(session, release.id)
        assert notifications(listener) == [str(run.id)]
        with session.begin():
            row = session.get(Run, run.id)
            row.status = "running"
            session.flush()
            assert notifications(listener) == []
        assert notifications(listener) == [str(run.id)]
        with session.begin():
            row = session.get(Run, run.id)
            row.job_uid = "diagnostic-only"
        assert notifications(listener) == []
        session.execute(text("UPDATE runs SET status = status WHERE id = :id"), {"id": run.id})
        session.commit()
        assert notifications(listener) == []
        session.execute(text("UPDATE runs SET status = 'failed' WHERE id = :id"), {"id": run.id})
        session.rollback()
        assert notifications(listener) == []
        with session.begin():
            session.get(Run, run.id).status = "succeeded"
            session.add(RunArtifact(run_id=run.id, object_key="outputs/a.csv", bucket="private", size_bytes=10))
        assert notifications(listener) == [str(run.id)]
        with session.begin():
            artifact = session.get(RunArtifact, (run.id, "outputs/a.csv"))
            artifact.size_bytes = 20
        assert notifications(listener) == [str(run.id)]
        with session.begin():
            session.delete(session.get(RunArtifact, (run.id, "outputs/a.csv")))
        assert notifications(listener) == [str(run.id)]


def test_owned_run_notification_carries_owner(migrated_engine, postgres_test_settings):
    from uuid import uuid4
    from mic3_api.infrastructure.persistence.users import User
    release = register(migrated_engine)
    owner = uuid4()
    with listen(postgres_test_settings) as listener, Session(migrated_engine) as session:
        with session.begin():
            session.add(User(id=owner))
        run = submit(session, release.id, owner)
        assert notifications(listener) == [f"{run.id}:{owner}"]
        with session.begin():
            session.get(Run, run.id).status = "succeeded"
            session.add(RunArtifact(run_id=run.id, object_key="outputs/a.csv", bucket="private", size_bytes=1))
        assert sorted(notifications(listener)) == sorted([f"{run.id}:{owner}", str(run.id)])


def test_trigger_security_and_downgrade(migrated_engine, postgres_test_settings, alembic_config):
    release = register(migrated_engine)
    with Session(migrated_engine) as session:
        run = submit(session, release.id)
    with migrated_engine.begin() as conn:
        assert conn.scalar(text("SELECT prosecdef FROM pg_proc WHERE oid = 'public.mic3_notify_run_change()'::regprocedure")) is False
        conn.execute(text("CREATE ROLE notification_test_writer NOLOGIN"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO notification_test_writer"))
        conn.execute(text("GRANT SELECT, UPDATE ON public.runs TO notification_test_writer"))
    try:
        with listen(postgres_test_settings) as listener:
            with migrated_engine.begin() as conn:
                conn.execute(text("SET LOCAL ROLE notification_test_writer"))
                conn.execute(text("UPDATE public.runs SET accepted_at = now() WHERE id = :id"), {"id": run.id})
            assert notifications(listener) == [str(run.id)]
        with migrated_engine.connect() as conn:
            assert conn.scalar(text("SELECT accepted_at IS NOT NULL FROM runs WHERE id = :id"), {"id": run.id})
    finally:
        with migrated_engine.begin() as conn:
            conn.execute(text("DROP OWNED BY notification_test_writer"))
            conn.execute(text("DROP ROLE notification_test_writer"))
    command.downgrade(alembic_config, "0005_run_execution")
    with migrated_engine.connect() as conn:
        assert conn.scalar(text("SELECT to_regprocedure('public.mic3_notify_run_change()')")) is None
        assert conn.scalar(text("SELECT count(*) FROM runs")) == 1
    command.upgrade(alembic_config, "head")
