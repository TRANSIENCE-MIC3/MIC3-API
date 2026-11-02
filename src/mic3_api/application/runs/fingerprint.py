"""Computation identity; independent of requester, release UUID and transport."""

from hashlib import sha256
import json

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.models.errors import ModelError


def fingerprint(release: ReleaseDefinition, parameters: dict) -> str:
    payload = {
        "model_id": release.model_id,
        "image_digest": release.image_digest,
        "execution_definition": release.execution_definition.resolve({"mode": parameters["mode"]}).computation_dict(),
        "parameters": parameters,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def matches_fingerprint(release: ReleaseDefinition, parameters: dict, expected: str) -> bool:
    """Whether a run still describes the same computation as its release.

    False when the release changed since submission, including a removed mode.
    """
    try:
        return fingerprint(release, parameters) == expected
    except ModelError:
        return False
