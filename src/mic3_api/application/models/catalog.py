"""Catalog registration and public metadata, independent of persistence."""

from dataclasses import dataclass
import re
from typing import Protocol
from uuid import UUID

from mic3_api.application.models.errors import ModelError
from mic3_api.application.models.execution import ExecutionDefinition, validate_key


@dataclass(frozen=True)
class ReleaseDefinition:
    model_id: str
    display_name: str
    image_digest: str
    execution_definition: ExecutionDefinition


@dataclass(frozen=True)
class ModelRelease:
    id: UUID
    definition: ReleaseDefinition


class Catalog(Protocol):
    def register(self, definition: ReleaseDefinition) -> ModelRelease: ...

    def get_release(self, release_id: UUID) -> ModelRelease: ...

    def list_releases(self) -> list[ModelRelease]: ...


def register_release(config: object, catalog: Catalog) -> ModelRelease:
    required = {"model_id", "display_name", "image_digest", "execution_definition"}
    if not isinstance(config, dict) or set(config) != required:
        raise ModelError("Invalid release configuration fields.")
    for field in ("model_id", "display_name", "image_digest"):
        if not isinstance(config[field], str) or not config[field].strip():
            raise ModelError("Model identifier, display name and image must be nonempty strings.")
    validate_key(config["model_id"])
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", config["image_digest"]):
        raise ModelError("The image must be pinned to a lowercase SHA-256 digest.")
    return catalog.register(ReleaseDefinition(
        model_id=config["model_id"], display_name=config["display_name"],
        image_digest=config["image_digest"],
        execution_definition=ExecutionDefinition.from_dict(config["execution_definition"]),
    ))


def list_models(catalog: Catalog) -> list[dict]:
    models: dict[str, dict] = {}
    for release in catalog.list_releases():
        definition = release.definition
        model = models.setdefault(definition.model_id, {
            "id": definition.model_id, "display_name": definition.display_name,
            "releases": [],
        })
        model["releases"].append({
            "id": release.id, "image_digest": definition.image_digest,
            "parameters": definition.execution_definition.parameter_metadata(),
        })
    return list(models.values())
