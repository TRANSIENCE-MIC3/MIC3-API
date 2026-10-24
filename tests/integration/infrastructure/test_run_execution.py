from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from alembic import command
from sqlalchemy import create_engine, event, select, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
import pytest

from mic3_api.application.models.catalog import register_release
from mic3_api.application.runs.acceptance import AcceptRun
from mic3_api.application.runs.execution import Artifact
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.infrastructure.persistence.execution import Run, OutboxEvent, RunArtifact
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from mic3_api.infrastructure.persistence.run_acceptance import SqlAlchemyRunAcceptance
from mic3_api.infrastructure.persistence.run_execution import SqlAlchemyExecutions
from tests.catalog_data import CONFIG, PARAMETERS


def accepted(engine):
    with Session(engine) as s:
        catalog = SqlAlchemyModelCatalog(s)
        release = register_release(CONFIG, catalog)
        run = SubmitRun(catalog, SqlAlchemyRunSubmissions(s)).execute(release.id, PARAMETERS)
        AcceptRun(SqlAlchemyRunAcceptance(s)).execute(run.id)
        return run.id


def test_claim_capacity_locks_and_terminal_idempotency(migrated_engine):
    first, second = accepted(migrated_engine), accepted(migrated_engine)
    store = SqlAlchemyExecutions(migrated_engine)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: store.claim_next("test", 1), range(2)))
    assert sorted(str(x) for x in claimed) == sorted([str(first), str(None)])
    with store.locked(first) as record:
        with store.locked(first) as conflict:
            assert conflict is None
        work = record.load()
        assert work.namespace == "test" and work.job_uid is None
        record.save_job_uid("uid-one")
        record.finish(None, (Artifact("bucket", "key", 10),))
        record.finish(None, (Artifact("bucket", "key", 10),))
    assert store.claim_next("test", 1) == second
    with Session(migrated_engine) as s:
        assert s.get(Run, first).job_uid == "uid-one"
        assert s.get(Run, first).finished_at
        assert len(list(s.scalars(select(RunArtifact)))) == 1
        assert len(list(s.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "run.succeeded")))) == 1


def test_terminal_commit_rolls_back_artifacts_and_event(migrated_engine):
    run_id = accepted(migrated_engine)
    store = SqlAlchemyExecutions(migrated_engine)
    store.claim_next("test", 1)
    with store.locked(run_id) as record:
        def fail(_):
            raise OperationalError("test", {}, Exception("commit failure"))
        event.listen(migrated_engine, "commit", fail)
        try:
            with pytest.raises(OperationalError):
                record.finish(None, (Artifact("bucket", "key", 10),))
        finally:
            event.remove(migrated_engine, "commit", fail)
    with Session(migrated_engine) as s:
        assert s.get(Run, run_id).status == "running"
        assert list(s.scalars(select(RunArtifact))) == []
        assert list(s.scalars(select(OutboxEvent.event_type))) == ["run.requested"]


def test_stale_claim_fails_without_dispatch(migrated_engine):
    run_id = accepted(migrated_engine)
    with Session(migrated_engine) as s, s.begin():
        s.get(Run, run_id).fingerprint = "a" * 64
    assert SqlAlchemyExecutions(migrated_engine).claim_next("test", 1) is None
    with Session(migrated_engine) as s:
        row = s.get(Run, run_id)
        assert row.failure_code == "execution_definition_changed"
        assert row.dispatch_started_at is None


def test_upgrade_from_0004_preserves_runs(alembic_config, postgres_test_settings):
    command.upgrade(alembic_config, "0004_run_acceptance")
    engine = create_engine(postgres_test_settings.database_url)
    from sqlalchemy import MetaData, Table
    from uuid import uuid4
    metadata = MetaData()
    tables = {n:Table(n,metadata,autoload_with=engine) for n in ('models','model_releases','runs')}
    release_id, run_id = uuid4(), uuid4()
    with engine.begin() as c:
        c.execute(tables['models'].insert(),dict(id='test',display_name='Test'))
        c.execute(tables['model_releases'].insert(),dict(id=release_id,model_id='test',image_digest=CONFIG['image_digest'],execution_definition=CONFIG['execution_definition']))
        c.execute(tables['runs'].insert(),dict(id=run_id,model_release_id=release_id,parameters=PARAMETERS,fingerprint='a'*64,status='queued',accepted_at=datetime.now(timezone.utc)))
        before=dict(c.execute(select(tables['runs'])).mappings().one())
    try:
        command.upgrade(alembic_config,'head')
        command.check(alembic_config)
        with Session(engine) as s:
            row=s.get(Run,run_id)
            assert all(getattr(row,k)==v for k,v in before.items())
            assert row.job_uid is row.dispatch_started_at is row.finished_at is None
        command.downgrade(alembic_config,'0004_run_acceptance')
        assert 'run_artifacts' not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
