"""Public, read-only metadata for qualified model releases."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from mic3_api.api.dependencies.use_cases import get_list_models
from mic3_api.application.models.catalog import ListModels


router = APIRouter(prefix="/models", tags=["models"])


class ReleaseMetadata(BaseModel):
    id: UUID
    image_digest: str
    parameters: dict


class ModelMetadata(BaseModel):
    id: str
    display_name: str
    releases: list[ReleaseMetadata]


@router.get("", response_model=list[ModelMetadata], responses={
    503: {"description": "PostgreSQL is temporarily unavailable."},
})
def models(list_models: Annotated[ListModels, Depends(get_list_models)]) -> list[ModelMetadata]:
    return [ModelMetadata.model_validate(asdict(model)) for model in list_models.execute()]
