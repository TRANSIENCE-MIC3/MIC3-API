"""Authenticated HTTP operations for the current MIC3 user."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from mic3_api.api.dependencies import get_current_user
from mic3_api.api.dependencies.use_cases import get_list_users
from mic3_api.application.users import CurrentUser
from mic3_api.application.users.administration import ListUsers


router = APIRouter(prefix="/users", tags=["users"])


class CurrentUserResponse(BaseModel):
    """Public profile and local roles for the authenticated MIC3 user."""

    id: UUID
    email: str | None
    display_name: str | None
    roles: list[str]


class UserDirectoryItem(CurrentUserResponse):
    is_active: bool


class UserDirectoryResponse(BaseModel):
    items: list[UserDirectoryItem]
    limit: int
    offset: int


@router.get("", response_model=UserDirectoryResponse, responses={
    401: {"description": "The bearer token is missing or invalid."},
    403: {"description": "An active MIC3 administrator is required."},
    503: {"description": "OIDC or PostgreSQL is temporarily unavailable."},
})
def list_users(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    list_all: Annotated[ListUsers, Depends(get_list_users)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserDirectoryResponse:
    accounts = list_all.execute(current_user, limit=limit, offset=offset)
    return UserDirectoryResponse(
        items=[UserDirectoryItem(
            id=user.id, email=user.email, display_name=user.display_name,
            is_active=user.is_active, roles=sorted(user.roles),
        ) for user in accounts],
        limit=limit, offset=offset,
    )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "The bearer token is missing or invalid."},
        status.HTTP_403_FORBIDDEN: {"description": "The local MIC3 account is inactive."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "OIDC or PostgreSQL is temporarily unavailable."},
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
