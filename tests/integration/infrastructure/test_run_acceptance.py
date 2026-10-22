from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, current_thread
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import MetaData, Table, create_engine, event, inspect, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ModelError, register_release
from mic3_api.application.runs.acceptance import AcceptRun
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.infrastructure.persistence.execution import Run
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_acceptance import SqlAlchemyRunAcceptance
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from tests.catalog_data import CONFIG, MODE, PARAMETERS


def register(engine, config=None):
    with Session(engine) as session:
        return register_release(config or CONFIG, SqlAlchemyModelCatalog(session))


def submit(engine, config=None):
    release = register(engine, config)
    with Session(engine) as session:
        return SubmitRun(SqlAlchemyModelCatalog(session), SqlAlchemyRunSubmissions(session)).execute(
            release.id, PARAMETERS,
        )


def accept(engine, run_id):
    with Session(engine) as session:
        result = AcceptRun(SqlAlchemyRunAcceptance(session)).execute(run_id)
        assert not session.in_transaction()
        return result


def show(engine, run_id):
    with Session(engine) as session:
        return SqlAlchemyRunSubmissions(session).show(run_id)


def test_concurrent_acceptance_returns_one_timestamp_and_keeps_queued(migrated_engine):
    run = submit(migrated_engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(migrated_engine, run.id), range(2)))
    assert results[0] == results[1]
    assert results[0].accepted_at.tzinfo is not None
    saved = show(migrated_engine, run.id)
    assert saved["accepted_at"] == results[0].accepted_at
    assert saved["failure_code"] is None
    assert saved["status"] == "queued"
    assert saved["fingerprint"] == run.fingerprint
    assert len(saved["events"]) == 1


@pytest.mark.parametrize("change", ["command", "remove"])
def test_incompatible_unaccepted_run_is_failed_without_acceptance(migrated_engine, change):
    run = submit(migrated_engine)
    config = deepcopy(CONFIG)
    modes = config["execution_definition"]["modes"]
    if change == "command":
        modes[MODE]["arguments"] = ["changed"]
    else:
        modes["other"] = modes.pop(MODE)
    register(migrated_engine, config)
    outcome = accept(migrated_engine, run.id)
    assert outcome.status == "failed"
    assert outcome.failure_code == "execution_definition_changed"
    assert outcome.accepted_at is None
    register(migrated_engine)  # Repairing catalog never revives a failed run.
    assert accept(migrated_engine, run.id) == outcome


def test_optional_outputs_can_be_added_before_acceptance(migrated_engine):
    run = submit(migrated_engine)
    config = deepcopy(CONFIG)
    config["execution_definition"]["modes"][MODE]["required_outputs"] = ["export/result.csv"]
    register(migrated_engine, config)
    outcome = accept(migrated_engine, run.id)
    assert outcome.accepted_at is not None
    assert outcome.fingerprint == run.fingerprint


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("change", ["command", "outputs", "remove"])
def test_active_mode_changes_blocked_and_registration_rolls_back(migrated_engine, status, change):
    run = submit(migrated_engine)
    accept(migrated_engine, run.id)
    with migrated_engine.begin() as connection:
        connection.execute(update(Run).where(Run.id == run.id).values(status=status))
    config = deepcopy(CONFIG)
    config["display_name"] = "Must roll back"
    modes = config["execution_definition"]["modes"]
    if change == "command":
        modes[MODE]["arguments"] = ["changed"]
    elif change == "outputs":
        modes[MODE]["required_outputs"] = ["result.csv"]
    else:
        modes["other"] = modes.pop(MODE)
    with pytest.raises(ModelError, match="accepted unfinished"):
        register(migrated_engine, config)
    with Session(migrated_engine) as session:
        release = SqlAlchemyModelCatalog(session).get_release(run.model_release_id)
    assert release.definition.display_name == CONFIG["display_name"]
    assert release.definition.execution_definition.to_dict() == CONFIG["execution_definition"]


def test_display_name_unrelated_modes_and_new_image_remain_editable(migrated_engine):
    run = submit(migrated_engine)
    accepted = accept(migrated_engine, run.id)
    config = deepcopy(CONFIG)
    config["display_name"] = "Renamed"
    config["execution_definition"]["modes"]["other"] = {
        "arguments": ["other"], "working_directory": "/other", "output_directory": "/out",
    }
    assert register(migrated_engine, config).id == run.model_release_id
    config["execution_definition"]["modes"]["other"]["arguments"] = ["modified"]
    register(migrated_engine, config)
    del config["execution_definition"]["modes"]["other"]
    register(migrated_engine, config)
    assert accept(migrated_engine, run.id) == accepted
    config["image_digest"] = "example/image@sha256:" + "b" * 64
    config["execution_definition"]["modes"][MODE]["arguments"] = ["new"]
    assert register(migrated_engine, config).id != run.model_release_id


@pytest.mark.parametrize("status", ["failed", "succeeded"])
def test_terminal_runs_release_protection_without_becoming_eligible(migrated_engine, status):
    run = submit(migrated_engine)
    accepted = accept(migrated_engine, run.id)
    with migrated_engine.begin() as connection:
        connection.execute(update(Run).where(Run.id == run.id).values(status=status))
    config = deepcopy(CONFIG)
    config["execution_definition"]["modes"][MODE]["arguments"] = ["changed"]
    register(migrated_engine, config)
    outcome = accept(migrated_engine, run.id)
    assert outcome.status == status
    assert outcome.accepted_at == accepted.accepted_at


def test_database_failure_rolls_back_acceptance_and_releases_locks(migrated_engine):
    run = submit(migrated_engine)
    with Session(migrated_engine) as session:
        def fail_commit(session):
            raise OperationalError("injected", {}, Exception("database unavailable"))
        event.listen(session, "before_commit", fail_commit, once=True)
        with pytest.raises(OperationalError):
            AcceptRun(SqlAlchemyRunAcceptance(session)).execute(run.id)
        assert not session.in_transaction()
    saved = show(migrated_engine, run.id)
    assert saved["accepted_at"] is saved["failure_code"] is None
    assert saved["status"] == "queued"
    assert accept(migrated_engine, run.id).accepted_at is not None


def test_mode_stays_protected_until_all_accepted_runs_are_terminal(migrated_engine):
    first, second = submit(migrated_engine), submit(migrated_engine)
    accept(migrated_engine, first.id)
    accept(migrated_engine, second.id)
    with migrated_engine.begin() as connection:
        connection.execute(update(Run).where(Run.id == first.id).values(status="succeeded"))
    config = deepcopy(CONFIG)
    config["execution_definition"]["modes"][MODE]["arguments"] = ["changed"]
    with pytest.raises(ModelError, match="accepted unfinished"):
        register(migrated_engine, config)
    with migrated_engine.begin() as connection:
        connection.execute(update(Run).where(Run.id == second.id).values(status="failed"))
    register(migrated_engine, config)


@pytest.mark.parametrize("first", ["accept", "register"])
def test_acceptance_and_registration_serialize_on_release(migrated_engine, first):
    run = submit(migrated_engine)
    changed = deepcopy(CONFIG)
    changed["execution_definition"]["modes"][MODE]["arguments"] = ["changed"]
    locked, attempted = Event(), Event()
    threads = {}

    def before(connection, cursor, statement, parameters, context, executemany):
        if "FROM model_releases" in statement and "FOR UPDATE" in statement:
            if current_thread().ident == threads.get("second"):
                attempted.set()

    def after(connection, cursor, statement, parameters, context, executemany):
        if "FROM model_releases" in statement and "FOR UPDATE" in statement:
            if current_thread().ident == threads.get("first"):
                locked.set()
                assert attempted.wait(10), "Second operation never attempted its release lock"

    def operation(name, slot):
        threads[slot] = current_thread().ident
        if name == "accept":
            return accept(migrated_engine, run.id)
        return register(migrated_engine, changed)

    event.listen(migrated_engine, "before_cursor_execute", before)
    event.listen(migrated_engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(operation, first, "first")
            assert locked.wait(10)
            loser = pool.submit(operation, "register" if first == "accept" else "accept", "second")
            winner.result(timeout=15)
            if first == "accept":
                with pytest.raises(ModelError, match="accepted unfinished"):
                    loser.result(timeout=15)
                assert show(migrated_engine, run.id)["accepted_at"] is not None
            else:
                assert loser.result(timeout=15).failure_code == "execution_definition_changed"
    finally:
        event.remove(migrated_engine, "before_cursor_execute", before)
        event.remove(migrated_engine, "after_cursor_execute", after)


def test_migration_preserves_existing_runs_and_adds_acceptance_and_rejections(alembic_config, postgres_test_settings):
    command.upgrade(alembic_config, "0003_model_runs")
    engine = create_engine(postgres_test_settings.database_url)
    release_id, run_id = uuid4(), uuid4()
    try:
        metadata = MetaData()
        tables = {name: Table(name, metadata, autoload_with=engine)
                  for name in ("models", "model_releases", "runs", "outbox_events")}
        with engine.begin() as connection:
            connection.execute(tables["models"].insert(), {"id": "test", "display_name": "Test"})
            connection.execute(tables["model_releases"].insert(), {
                "id": release_id, "model_id": "test", "image_digest": CONFIG["image_digest"],
                "execution_definition": CONFIG["execution_definition"],
            })
            connection.execute(tables["runs"].insert(), {
                "id": run_id, "model_release_id": release_id, "parameters": PARAMETERS,
                "fingerprint": "a" * 64, "status": "queued",
            })
            connection.execute(tables["outbox_events"].insert(), {
                "id": uuid4(), "run_id": run_id, "event_type": "run.requested",
            })
            before = dict(connection.execute(select(tables["runs"])).mappings().one())
        command.upgrade(alembic_config, "head")
        saved = show(engine, run_id)
        assert all(saved[key] == value for key, value in before.items())
        assert saved["accepted_at"] is saved["failure_code"] is None
        assert len(saved["events"]) == 1
        assert "run_executions" not in inspect(engine).get_table_names()
        assert "rejected_messages" in inspect(engine).get_table_names()
        command.check(alembic_config)
        command.downgrade(alembic_config, "0003_model_runs")
        assert "rejected_messages" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert dict(connection.execute(select(tables["runs"])).mappings().one()) == before
    finally:
        engine.dispose()
