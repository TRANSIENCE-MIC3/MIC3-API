"""Decode the publisher's version-one JSON wire contract without driver types."""

import json
from datetime import datetime
from uuid import UUID

from mic3_api.application.runs.consumption import Rejection


def decode_request(key: bytes | None, value: bytes | None) -> UUID | Rejection:
    try:
        data = json.loads(value) if value is not None else None
    except (ValueError, UnicodeError, RecursionError):
        return Rejection("invalid_envelope", "Expected a JSON object.")
    if not isinstance(data, dict):
        return Rejection("invalid_envelope", "Expected a JSON object.")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        return Rejection("unsupported_version", "Expected schema version 1.")
    if data.get("event_type") != "run.requested":
        return Rejection("unsupported_event", "Expected run.requested.")
    try:
        UUID(data["event_id"])
        run_id = UUID(data["run_id"])
        created_at = datetime.fromisoformat(data["created_at"])
        if created_at.utcoffset() is None:
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError):
        return Rejection("invalid_envelope", "Expected UUID identifiers and a timezone-aware creation time.")
    if key != str(run_id).encode("utf-8"):
        return Rejection("key_mismatch", "Record key must match the run UUID.")
    return run_id
