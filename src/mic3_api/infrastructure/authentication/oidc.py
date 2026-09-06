"""Provider-independent OIDC discovery, JWKS lookup, and JWT validation."""

import json
from threading import Lock
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKError,
    PyJWKSetError,
)

from mic3_api.application.authentication import (
    IdentityProviderUnavailableError,
    InvalidAccessTokenError,
    ValidatedIdentity,
)


MAX_DISCOVERY_DOCUMENT_BYTES = 1_048_576


class OidcAccessTokenValidator:
    """Validate access tokens using standards-based discovery and signing keys."""

    def __init__(
        self,
        *,
        issuer_url: str,
        audience: str,
        allowed_algorithms: tuple[str, ...] = ("RS256",),
        clock_skew_seconds: int = 30,
        http_timeout_seconds: float = 5.0,
        jwks_cache_seconds: int = 300,
    ) -> None:
        self._issuer_url = issuer_url
        self._audience = audience
        self._allowed_algorithms = allowed_algorithms
        self._clock_skew_seconds = clock_skew_seconds
        self._http_timeout_seconds = http_timeout_seconds
        self._jwks_cache_seconds = jwks_cache_seconds
        self._jwks_client: PyJWKClient | None = None
        self._discovery_lock = Lock()

    def validate(self, access_token: str) -> ValidatedIdentity:
        """Verify a bearer token and return only claims trusted by MIC3."""
        try:
            header = jwt.get_unverified_header(access_token)
        except InvalidTokenError as exc:
            raise InvalidAccessTokenError from exc

        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in self._allowed_algorithms or not isinstance(key_id, str):
            raise InvalidAccessTokenError
        if not key_id:
            raise InvalidAccessTokenError

        try:
            signing_key = self._get_jwks_client().get_signing_key_from_jwt(
                access_token
            )
            claims = jwt.decode(
                access_token,
                signing_key.key,
                algorithms=list(self._allowed_algorithms),
                audience=self._audience,
                issuer=self._issuer_url,
                leeway=self._clock_skew_seconds,
                options={"require": ["iss", "sub", "aud", "exp"]},
            )
        except PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailableError from exc
        except (PyJWKError, PyJWKSetError) as exc:
            raise IdentityProviderUnavailableError from exc
        except (InvalidTokenError, PyJWKClientError) as exc:
            raise InvalidAccessTokenError from exc

        subject = claims.get("sub")
        issuer = claims.get("iss")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise InvalidAccessTokenError
        if not isinstance(issuer, str):
            raise InvalidAccessTokenError

        return ValidatedIdentity(
            issuer=issuer,
            subject=subject,
            email=self._optional_profile_claim(claims, "email"),
            display_name=(
                self._optional_profile_claim(claims, "name")
                or self._optional_profile_claim(claims, "preferred_username")
            ),
        )

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is not None:
            return self._jwks_client

        with self._discovery_lock:
            if self._jwks_client is None:
                jwks_uri = self._discover_jwks_uri()
                self._jwks_client = PyJWKClient(
                    jwks_uri,
                    cache_keys=False,
                    cache_jwk_set=True,
                    lifespan=self._jwks_cache_seconds,
                    timeout=self._http_timeout_seconds,
                )
        return self._jwks_client

    def _discover_jwks_uri(self) -> str:
        discovery_url = (
            f"{self._issuer_url.rstrip('/')}"
            "/.well-known/openid-configuration"
        )
        request = Request(
            discovery_url,
            headers={"Accept": "application/json", "User-Agent": "mic3-api"},
        )

        try:
            with urlopen(request, timeout=self._http_timeout_seconds) as response:
                encoded_document = response.read(MAX_DISCOVERY_DOCUMENT_BYTES + 1)
            if len(encoded_document) > MAX_DISCOVERY_DOCUMENT_BYTES:
                raise IdentityProviderUnavailableError
            document = json.loads(encoded_document)
        except IdentityProviderUnavailableError:
            raise
        except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
            raise IdentityProviderUnavailableError from exc

        if not isinstance(document, dict):
            raise IdentityProviderUnavailableError
        if document.get("issuer") != self._issuer_url:
            raise IdentityProviderUnavailableError

        jwks_uri = document.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not self._is_absolute_http_url(jwks_uri):
            raise IdentityProviderUnavailableError
        return jwks_uri

    @staticmethod
    def _optional_profile_claim(claims: dict[str, Any], name: str) -> str | None:
        value = claims.get(name)
        if not isinstance(value, str) or not value.strip():
            return None
        return value

    @staticmethod
    def _is_absolute_http_url(value: str) -> bool:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and not parsed.fragment
        )
