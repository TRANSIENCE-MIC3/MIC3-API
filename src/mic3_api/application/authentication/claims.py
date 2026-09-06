"""Trusted, provider-neutral identity claims used by the application."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ValidatedIdentity:
    """Identity and optional profile data from a verified OIDC access token."""

    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
