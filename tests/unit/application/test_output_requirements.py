from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from mic3_api.application.models.catalog import ModelError, RegisterRelease
from mic3_api.application.models.catalog_changes import ensure_active_modes_unchanged
from mic3_api.application.models.execution import ExecutionDefinition
from mic3_api.application.models.outputs import OutputRequirements
from mic3_api.application.runs.fingerprint import fingerprint
from tests.catalog_data import CONFIG, MODE, PARAMETERS
from unittest.mock import Mock


def definition(config):
    catalog = Mock()
    RegisterRelease(catalog).execute(config)
    return catalog.register.call_args.args[0]


def test_output_requirements_preserve_legacy_fingerprint_and_roundtrip():
    original = definition(CONFIG)
    legacy_payload = {
        "model_id": CONFIG["model_id"], "image_digest": CONFIG["image_digest"],
        "execution_definition": CONFIG["execution_definition"]["modes"][MODE],
        "parameters": PARAMETERS,
    }
    expected = sha256(json.dumps(legacy_payload, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert fingerprint(original, PARAMETERS) == expected
    for paths in ([], ["export/b.csv", "export/a.csv"]):
        config = deepcopy(CONFIG)
        config["execution_definition"]["modes"][MODE]["required_outputs"] = paths
        changed = definition(config)
        assert fingerprint(changed, PARAMETERS) == expected
        invocation = changed.execution_definition.resolve(PARAMETERS)
        assert invocation.output_requirements.required_files == tuple(sorted(paths))
        assert ExecutionDefinition.from_dict(changed.execution_definition.to_dict()) == changed.execution_definition
    changed_modes = dict(original.execution_definition.modes)
    changed_modes[MODE] = replace(changed_modes[MODE], arguments=("different",))
    assert fingerprint(replace(original, execution_definition=ExecutionDefinition(changed_modes)), PARAMETERS) != expected


@pytest.mark.parametrize("paths", [None, "file.csv", [1], [""], ["."], ["../file"],
    ["a/../file"], ["/absolute"], ["C:/file"], ["a\\file"], ["a//file"], ["./file"],
    ["a/"], ["*.csv"], ["a?.csv"], ["[ab].csv"], ["a\0b"], ["a\nb"], ["a", "a"]])
def test_invalid_required_outputs_rejected(paths):
    with pytest.raises(ModelError):
        OutputRequirements.from_list(paths)


def test_only_active_modes_are_protected_and_output_order_is_irrelevant():
    current = ExecutionDefinition.from_dict({"modes": {"active": {
        "arguments": ["model"], "working_directory": "/model", "output_directory": "/out",
        "required_outputs": ["b", "a"],
    }}})
    reordered = current.to_dict()
    reordered["modes"]["active"]["required_outputs"] = ["b", "a"]
    ensure_active_modes_unchanged(current, ExecutionDefinition.from_dict(reordered), ["active"])
    changed = ExecutionDefinition({"other": current.modes["active"]})
    ensure_active_modes_unchanged(current, changed, [])
    with pytest.raises(ModelError, match="accepted unfinished"):
        ensure_active_modes_unchanged(current, changed, ["active"])
