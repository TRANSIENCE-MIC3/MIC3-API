import json
from uuid import uuid4

import pytest

from mic3_api.application.runs.consumption import Rejection
from mic3_api.infrastructure.request_envelope import decode_request


def envelope():
    return dict(schema_version=1, event_id=str(uuid4()), run_id=str(uuid4()),
                event_type="run.requested", created_at="2026-10-22T10:00:00+00:00")


def test_valid_envelope_allows_additional_fields():
    data = {**envelope(), "extra": "compatible"}
    assert str(decode_request(data["run_id"].encode(), json.dumps(data).encode())) == data["run_id"]


@pytest.mark.parametrize("value", [None, b"{", b"\xff", b"[]", b"null", b"42"])
def test_unreadable_envelopes(value):
    assert decode_request(None, value).code == "invalid_envelope"


@pytest.mark.parametrize("field,value,code", [
    ("schema_version", True, "unsupported_version"),
    ("schema_version", 2, "unsupported_version"),
    ("event_type", "run.finished", "unsupported_event"),
    ("run_id", "invalid", "invalid_envelope"),
    ("event_id", None, "invalid_envelope"),
    ("created_at", "2026-10-22T10:00:00", "invalid_envelope"),
    ("created_at", 123, "invalid_envelope"),
])
def test_invalid_fields(field, value, code):
    data = envelope()
    data[field] = value
    result = decode_request(b"key", json.dumps(data).encode())
    assert isinstance(result, Rejection)
    assert result.code == code
    assert len(result.diagnostic) <= 256


def test_key_must_match_and_missing_fields_are_rejected():
    data = envelope()
    assert decode_request(None, json.dumps(data).encode()).code == "key_mismatch"
    del data["event_id"]
    assert decode_request(b"key", json.dumps(data).encode()).code == "invalid_envelope"
