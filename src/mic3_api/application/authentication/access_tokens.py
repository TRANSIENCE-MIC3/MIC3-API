"""Application port and failures for validating bearer access tokens."""

from typing import Protocol

from mic3_api.application.authentication.claims import ValidatedIdentity


class InvalidAccessTokenError(Exception):
    """The supplied bearer token cannot authenticate an identity."""


class IdentityProviderUnavailableError(Exception):
    """OIDC metadata or signing keys cannot currently be obtained."""


class AccessTokenValidator(Protocol):
    """Validate a bearer token without exposing a provider-specific SDK."""

    def validate(self, access_token: str) -> ValidatedIdentity:
        """Return trusted identity claims or raise an authentication failure."""
        ...
