from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from mic3_api.application.models.catalog import ModelError, ModelRelease, ReleaseDefinition, register_release
from mic3_api.application.models.execution import ExecutionDefinition, Invocation
from mic3_api.application.runs.fingerprint import fingerprint
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.cli_runs import read_json
from tests.catalog_data import CONFIG


@pytest.fixture
def release():
    return ReleaseDefinition("example", "Example", "example/model@sha256:" + "a" * 64,
        ExecutionDefinition({
            "baseline": Invocation(("python", "baseline.py"), "/model", "/output"),
            "alternative": Invocation(("gams", "scenario.gms"), "/model", "/output"),
        }))


@pytest.mark.parametrize("parameters", [{}, [], None, {"mode": "missing"},
    {"mode": "baseline", "other": 1}, {"mode": ["baseline"]}])
def test_invalid_parameters(release, parameters):
    with pytest.raises(ModelError):
        release.execution_definition.prepare(parameters)


def test_modes_resolve_distinct_invocations_and_match_metadata(release):
    execution = release.execution_definition
    assert execution.parameter_metadata()["properties"]["mode"]["enum"] == ["alternative", "baseline"]
    assert execution.resolve({"mode": "baseline"}).arguments == ("python", "baseline.py")
    assert execution.resolve({"mode": "alternative"}).arguments == ("gams", "scenario.gms")


@pytest.mark.parametrize("mode", list(CONFIG["execution_definition"]["modes"]))
def test_registered_catalog_modes_are_usable(mode):
    catalog = Mock()
    register_release(CONFIG, catalog)
    definition = catalog.register.call_args.args[0]
    effective = definition.execution_definition.prepare({"mode": mode})
    assert effective == {"mode": mode}
    assert definition.execution_definition.resolve(effective).to_dict() == CONFIG["execution_definition"]["modes"][mode]


def test_fingerprint_identity(release):
    parameters = {"mode": "baseline", "nested": {"b": 2, "a": 1}}
    value = fingerprint(release, parameters)
    assert len(value) == 64
    assert value == fingerprint(release, {"nested": {"a": 1, "b": 2}, "mode": "baseline"})
    assert value == fingerprint(replace(release, display_name="New label"), parameters)
    assert value != fingerprint(replace(release, image_digest="image@sha256:" + "b" * 64), parameters)
    assert value != fingerprint(replace(release, model_id="another"), parameters)
    assert value != fingerprint(release, parameters | {"mode": "alternative"})
    assert value != fingerprint(release, parameters | {"nested": {"a": 3}})
    modes = dict(release.execution_definition.modes)
    modes["alternative"] = replace(modes["alternative"], arguments=("changed",))
    assert value == fingerprint(replace(release, execution_definition=ExecutionDefinition(modes)), parameters)
    modes["baseline"] = replace(modes["baseline"], arguments=("changed",))
    assert value != fingerprint(replace(release, execution_definition=ExecutionDefinition(modes)), parameters)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_fingerprints_rejected(release, number):
    with pytest.raises(ValueError):
        fingerprint(release, {"mode": "baseline", "number": number})


def test_submission_has_no_model_specific_dispatch(release):
    catalog, store = Mock(), Mock()
    catalog.get_release.return_value = ModelRelease(uuid4(), release)
    submit = SubmitRun(catalog, store)
    first = submit.execute(uuid4(), {"mode": "baseline"})
    second = submit.execute(uuid4(), {"mode": "baseline"}, uuid4())
    assert first.fingerprint == second.fingerprint and first.id != second.id
    assert store.save_requested.call_count == 2
    with pytest.raises(ModelError):
        submit.execute(uuid4(), {})
    assert store.save_requested.call_count == 2


@pytest.mark.parametrize("change", [
    {"image_digest": "image:latest"}, {"version": "unused-label"}, {"model_id": "../escape"},
    {"execution_definition": {}}, {"credentials": "forbidden"},
    {"execution_definition": {"modes": {}}},
    {"execution_definition": {"modes": {"../escape": {}}}},
])
def test_invalid_registration_rejected(change):
    catalog = Mock()
    with pytest.raises(ModelError):
        register_release(CONFIG | change, catalog)
    catalog.register.assert_not_called()


@pytest.mark.parametrize("content", ["{", '{"value": NaN}', '{"value": Infinity}'])
def test_invalid_json_files(tmp_path, content):
    path = tmp_path / "input.json"
    path.write_text(content)
    with pytest.raises(ModelError):
        read_json(path)
