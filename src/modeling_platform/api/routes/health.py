from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel


router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response returned by the health endpoint."""

    status: Literal["healthy"] = "healthy"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report whether the API process is healthy."""
    return HealthResponse()
