from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ModelError, register_release
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.infrastructure.persistence import Model, ModelReleaseRow, OutboxEvent, Run, User
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from mic3_api.main import create_app
from tests.catalog_data import CONFIG, PARAMETERS, MODE


ROOT = Path(__file__).resolve().parents[3]


def register(engine, config=None):
    with Session(engine) as session:
        return register_release(config or CONFIG, SqlAlchemyModelCatalog(session))


def submit(session, release_id, requested_by=None, parameters=None):
    return SubmitRun(SqlAlchemyModelCatalog(session), SqlAlchemyRunSubmissions(session)).execute(
        release_id, parameters if parameters is not None else PARAMETERS, requested_by,
    )


def test_concurrent_registration_and_editable_catalog(migrated_engine):
    with ThreadPoolExecutor(max_workers=2) as pool:
        releases = list(pool.map(lambda _: register(migrated_engine), range(2)))
    assert releases[0] == releases[1]
    config = deepcopy(CONFIG)
    config["display_name"] = "Renamed model"
    config["execution_definition"]["modes"][MODE]["arguments"] = ["changed", "invocation"]
    with Session(migrated_engine) as session:
        old_run = submit(session, releases[0].id)
    changed = register(migrated_engine, config)
    assert changed.id == releases[0].id
    with Session(migrated_engine) as session:
        catalog = SqlAlchemyModelCatalog(session)
        assert catalog.get_release(changed.id) == changed
        assert len(catalog.list_releases()) == 1
        new_run = submit(session, changed.id)
        assert old_run.fingerprint != new_run.fingerprint
        assert SqlAlchemyRunSubmissions(session).show(old_run.id)["fingerprint"] == old_run.fingerprint
    config["image_digest"] = "another/image@sha256:" + "b" * 64
    assert register(migrated_engine, config).id != changed.id


def test_submission_and_fresh_session_retrieval(migrated_engine):
    release = register(migrated_engine)
    user_id = uuid4()
    with Session(migrated_engine) as session:
        with session.begin():
            session.add(User(id=user_id))
        anonymous = submit(session, release.id)
        attributed = submit(session, release.id, user_id)
        assert anonymous.id != attributed.id
        assert anonymous.fingerprint == attributed.fingerprint
        assert not session.in_transaction()
    with Session(migrated_engine) as session:
        for run in (anonymous, attributed):
            saved = SqlAlchemyRunSubmissions(session).show(run.id)
            assert saved["requested_by"] == run.requested_by
            assert saved["parameters"] == PARAMETERS
            assert saved["model_release_id"] == release.id
            assert saved["status"] == "queued"
            assert saved["created_at"].tzinfo is not None
            assert len(saved["events"]) == 1
            assert saved["events"][0]["event_type"] == "run.requested"
            assert saved["events"][0]["published_at"] is None
        assert session.scalar(select(func.count()).select_from(Run)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 2


def test_new_model_and_release_modes_need_no_python_registration(migrated_engine, postgres_test_settings):
    first = register(migrated_engine)
    config = deepcopy(CONFIG)
    config["model_id"] = "another-model"
    config["execution_definition"] = {"modes": {
        "scenario": {"arguments": ["gams", "scenario.gms"],
                     "working_directory": "/project", "output_directory": "/results"},
    }}
    second = register(migrated_engine, config)
    with Session(migrated_engine) as session:
        run = submit(session, second.id, parameters={"mode": "scenario"})
        assert run.parameters == {"mode": "scenario"}
        with pytest.raises(ModelError):
            submit(session, second.id, parameters={"mode": "missing"})
    with TestClient(create_app(settings=postgres_test_settings)) as client:
        metadata = client.get("/models").json()
        releases = {entry["id"]: entry for model in metadata for entry in model["releases"]}
        assert releases[str(second.id)]["parameters"]["properties"]["mode"]["enum"] == ["scenario"]
        assert releases[str(first.id)]["parameters"]["properties"]["mode"]["enum"] == sorted(CONFIG["execution_definition"]["modes"])


def test_invalid_submissions_leave_no_records(migrated_engine):
    release = register(migrated_engine)
    with Session(migrated_engine) as session:
        for release_id, user_id, parameters in [
            (uuid4(), None, PARAMETERS),
            (release.id, uuid4(), PARAMETERS),
            (release.id, None, {}), (release.id, None, {"mode": "steel"}),
        ]:
            with pytest.raises(ModelError):
                submit(session, release_id, user_id, parameters)
            assert not session.in_transaction()
        assert session.scalar(select(func.count()).select_from(Run)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_event_insert_failure_rolls_back_run_and_session_recovers(migrated_engine):
    release = register(migrated_engine)

    def fail_event(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox_events"):
            raise RuntimeError("injected event insert failure")

    with Session(migrated_engine) as session:
        event.listen(migrated_engine, "before_cursor_execute", fail_event)
        try:
            with pytest.raises(RuntimeError, match="event insert"):
                submit(session, release.id)
        finally:
            event.remove(migrated_engine, "before_cursor_execute", fail_event)
        assert not session.in_transaction()
        with session.begin():
            assert session.scalar(select(func.count()).select_from(Run)) == 0
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert submit(session, release.id).status == "queued"


def test_database_constraints(migrated_engine):
    release = register(migrated_engine)
    values = dict(id=uuid4(), model_release_id=release.id, parameters={}, fingerprint="a" * 64, status="queued")
    for change in [{"parameters": []}, {"fingerprint": "A" * 64}, {"fingerprint": "a" * 63},
                   {"status": "finished"}, {"model_release_id": uuid4()}, {"requested_by": uuid4()}]:
        with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
            connection.execute(Run.__table__.insert(), values | change)
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(ModelReleaseRow.__table__.insert(), dict(
            id=uuid4(), model_id=CONFIG["model_id"], image_digest=CONFIG["image_digest"], execution_definition=[],
        ))
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(OutboxEvent.__table__.insert(), dict(id=uuid4(), run_id=uuid4(), event_type="run.requested"))
    user_id = uuid4()
    with Session(migrated_engine) as session:
        with session.begin():
            session.add(User(id=user_id))
        run = submit(session, release.id, user_id)
    for table, identifier in [(User, user_id), (Model, CONFIG["model_id"]), (ModelReleaseRow, release.id), (Run, run.id)]:
        with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
            connection.execute(delete(table).where(table.id == identifier))


def test_public_metadata(migrated_engine, postgres_test_settings, monkeypatch):
    with TestClient(create_app(settings=postgres_test_settings)) as client:
        assert client.get("/models").json() == []
        release = register(migrated_engine)
        response = client.get("/models")
        assert response.status_code == 200
        assert response.json() == [{
            "id": CONFIG["model_id"], "display_name": CONFIG["display_name"],
            "releases": [{"id": str(release.id), "image_digest": CONFIG["image_digest"],
                "parameters": {"type": "object", "additionalProperties": False, "required": ["mode"],
                    "properties": {"mode": {"type": "string", "enum": sorted(CONFIG["execution_definition"]["modes"])}}}}],
        }]
        assert "execution_definition" not in response.text
        def fail(self):
            raise OperationalError("secret", {}, Exception("secret"))
        monkeypatch.setattr(SqlAlchemyModelCatalog, "list_releases", fail)
        failure = client.get("/models")
        assert failure.status_code == 503
        assert "secret" not in failure.text


def test_cli_acceptance_in_fresh_processes(migrated_engine, monkeypatch, tmp_path):
    monkeypatch.setenv("OIDC_ISSUER_URL", "")
    monkeypatch.setenv("OIDC_AUDIENCE", "")

    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "mic3_api.cli", *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    release = cli("register-model-release", "--config", "integrations/eu_mfa/release.json")
    assert cli("register-model-release", "--config", "integrations/eu_mfa/release.json") == release
    parameter_file = tmp_path / "request.json"
    parameter_file.write_text(json.dumps(PARAMETERS))
    run = cli("submit-run", "--release-id", release["id"], "--parameters", str(parameter_file))
    values = cli("model-job-values", "--run-id", run["id"])
    assert values["runId"] == run["id"]
    assert values["model"]["command"] == CONFIG["execution_definition"]["modes"][MODE]["arguments"]
    assert UUID(run["id"])
    saved = cli("show-run", "--run-id", run["id"])
    assert saved["status"] == "queued" and saved["requested_by"] is None
    assert saved["fingerprint"] == run["fingerprint"]
    assert len(saved["events"]) == 1 and saved["events"][0]["published_at"] is None


def test_cli_reports_invalid_inputs_without_writing(migrated_engine, capsys, tmp_path):
    from mic3_api.cli import main

    release = register(migrated_engine)
    invalid_parameters = tmp_path / "invalid.json"
    parameter_file = tmp_path / "request.json"
    parameter_file.write_text(json.dumps(PARAMETERS))
    invalid_parameters.write_text('{"mode": "unqualified"}')
    for args in [
        ["show-run", "--run-id", str(uuid4())],
        ["submit-run", "--release-id", str(release.id), "--parameters", str(invalid_parameters)],
        ["submit-run", "--release-id", str(release.id), "--parameters", str(tmp_path / "missing.json")],
        ["submit-run", "--release-id", str(release.id), "--parameters",
         str(parameter_file), "--requested-by", str(uuid4())],
    ]:
        assert main(args) == 1
        output = capsys.readouterr()
        assert output.err and not output.out
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_job_export_rejects_changed_configuration_and_failed_run(migrated_engine, capsys):
    from mic3_api.cli import main

    release = register(migrated_engine)
    with Session(migrated_engine) as session:
        original = submit(session, release.id)
    config = deepcopy(CONFIG)
    config["execution_definition"]["modes"][MODE]["arguments"] = ["changed"]
    register(migrated_engine, config)
    assert main(["model-job-values", "--run-id", str(original.id)]) == 1
    assert "Catalog execution has changed" in capsys.readouterr().err
    with Session(migrated_engine) as session:
        replacement = submit(session, release.id)
        with session.begin():
            session.get(Run, replacement.id).status = "failed"
    assert main(["model-job-values", "--run-id", str(replacement.id)]) == 1
    assert "retry by submitting a new run" in capsys.readouterr().err
