"""Authenticated HTTP operations for the current MIC3 user."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from mic3_api.api.dependencies import get_current_user
from mic3_api.application.users import CurrentUser


router = APIRouter(prefix="/users", tags=["users"])


class CurrentUserResponse(BaseModel):
    """Public profile and local roles for the authenticated MIC3 user."""

    id: UUID
    email: str | None
    display_name: str | None
    roles: list[str]


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The bearer token is missing or invalid."
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "The local MIC3 account is inactive."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "OIDC or PostgreSQL is temporarily unavailable."
        },
    },
)
def read_current_user(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUserResponse:
    """Return the authenticated local profile and role names."""
    return CurrentUserResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        roles=list(current_user.roles),
    )
