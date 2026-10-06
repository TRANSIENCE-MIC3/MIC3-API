"""Public, read-only metadata for qualified model releases."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mic3_api.api.dependencies.database import get_session
from mic3_api.application.models.catalog import list_models
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog


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
def models(session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    try:
        return list_models(SqlAlchemyModelCatalog(session))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database access is unavailable.") from exc
