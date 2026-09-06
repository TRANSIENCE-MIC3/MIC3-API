"""FastAPI dependencies that authenticate and resolve the current user."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mic3_api.api.dependencies.database import get_session
from mic3_api.application.authentication import (
    AccessTokenValidator,
    IdentityProviderUnavailableError,
    InvalidAccessTokenError,
    ValidatedIdentity,
)
from mic3_api.application.users import (
    CurrentUser,
    IdentityProvisioningError,
    InactiveUserError,
    ResolveCurrentUser,
    UserProvisioningConfigurationError,
)
from mic3_api.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUserAccountUnitOfWork,
)


logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="OIDC access token issued for the MIC3 API audience.",
)
current_user_resolver = ResolveCurrentUser()


def get_token_validator(request: Request) -> AccessTokenValidator:
    """Return the application-scoped access-token validation boundary."""
    return request.app.state.token_validator


def get_validated_identity(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
    token_validator: Annotated[AccessTokenValidator, Depends(get_token_validator)],
) -> ValidatedIdentity:
    """Extract and validate the bearer token without exposing it downstream."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    try:
        return token_validator.validate(credentials.credentials)
    except InvalidAccessTokenError as exc:
        raise _unauthorized() from exc
    except IdentityProviderUnavailableError as exc:
        logger.warning("OIDC discovery or signing keys are unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication dependencies are unavailable.",
        ) from exc


def get_current_user(
    identity: Annotated[ValidatedIdentity, Depends(get_validated_identity)],
    session: Annotated[Session, Depends(get_session)],
) -> CurrentUser:
    """Resolve a validated identity to one active, persisted MIC3 user."""
    unit_of_work = SqlAlchemyUserAccountUnitOfWork(session)
    try:
        return current_user_resolver.execute(identity, unit_of_work)
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The MIC3 account is inactive.",
        ) from exc
    except UserProvisioningConfigurationError as exc:
        logger.error("The required member role is missing during provisioning")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The user account could not be provisioned.",
        ) from exc
    except IdentityProvisioningError as exc:
        logger.warning("A concurrent identity assignment could not be resolved")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication dependencies are unavailable.",
        ) from exc
    except SQLAlchemyError as exc:
        logger.warning("Database access failed while resolving a user: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication dependencies are unavailable.",
        ) from exc


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid bearer access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
