import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from mic3_api.api.dependencies import get_database
from mic3_api.infrastructure.database import DatabaseHealthGateway


logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class ReadinessResponse(BaseModel):
    """Response returned by the readiness endpoint."""

    status: Literal["ready", "not_ready"]


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "PostgreSQL is unavailable.",
        }
    },
)
def readiness(
    database: Annotated[DatabaseHealthGateway, Depends(get_database)],
) -> ReadinessResponse | JSONResponse:
    """Report whether the API can reach PostgreSQL."""
    try:
        database.ping()
    except SQLAlchemyError as exc:
        logger.warning("PostgreSQL readiness check failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready"},
        )

    return ReadinessResponse(status="ready")
