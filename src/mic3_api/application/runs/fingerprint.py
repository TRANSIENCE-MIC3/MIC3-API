"""Computation identity; independent of requester, release UUID and transport."""

from hashlib import sha256
import json

from mic3_api.application.models.catalog import ReleaseDefinition


def fingerprint(release: ReleaseDefinition, parameters: dict) -> str:
    payload = {
        "model_id": release.model_id,
        "image_digest": release.image_digest,
        "execution_definition": release.execution_definition.resolve({"mode": parameters["mode"]}).to_dict(),
        "parameters": parameters,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()
