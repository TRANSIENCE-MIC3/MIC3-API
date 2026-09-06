import json
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from mic3_api.application.authentication import (
    IdentityProviderUnavailableError,
    InvalidAccessTokenError,
)
from mic3_api.infrastructure.authentication import OidcAccessTokenValidator


@dataclass
class SigningKey:
    key_id: str
    private_key: rsa.RSAPrivateKey

    def public_jwk(self) -> dict[str, Any]:
        jwk = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        jwk.update({"kid": self.key_id, "use": "sig", "alg": "RS256"})
        return jwk


@dataclass
class OidcTestServer:
    issuer: str
    keys: list[SigningKey]
    reported_issuer: str


@pytest.fixture
def oidc_server() -> Iterator[OidcTestServer]:
    state: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            server_state = state["server"]
            assert isinstance(server_state, OidcTestServer)
            if self.path == "/.well-known/openid-configuration":
                self._send_json(
                    {
                        "issuer": server_state.reported_issuer,
                        "jwks_uri": f"{server_state.issuer}/jwks",
                    }
                )
                return
            if self.path == "/jwks":
                self._send_json(
                    {"keys": [key.public_jwk() for key in server_state.keys]}
                )
                return
            self.send_error(404)

        def _send_json(self, document: dict[str, object]) -> None:
            body = json.dumps(document).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    issuer = f"http://127.0.0.1:{server.server_port}"
    server_state = OidcTestServer(
        issuer=issuer,
        keys=[_signing_key("key-1")],
        reported_issuer=issuer,
    )
    state["server"] = server_state
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _signing_key(key_id: str) -> SigningKey:
    return SigningKey(
        key_id=key_id,
        private_key=rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


def _validator(server: OidcTestServer) -> OidcAccessTokenValidator:
    return OidcAccessTokenValidator(
        issuer_url=server.issuer,
        audience="mic3-api",
        clock_skew_seconds=0,
        http_timeout_seconds=1,
        jwks_cache_seconds=60,
    )


def _token(
    server: OidcTestServer,
    *,
    signing_key: SigningKey | None = None,
    claims: dict[str, object] | None = None,
) -> str:
    key = signing_key or server.keys[0]
    now = int(time())
    payload: dict[str, object] = {
        "iss": server.issuer,
        "sub": "Case-Sensitive-Subject",
        "aud": "mic3-api",
        "iat": now,
        "exp": now + 300,
        "email": "member@example.org",
        "name": "Member Name",
        "preferred_username": "member-user",
    }
    if claims:
        payload.update(claims)
    return jwt.encode(
        payload,
        key.private_key,
        algorithm="RS256",
        headers={"kid": key.key_id},
    )


def test_validates_token_and_maps_standard_profile_claims(
    oidc_server: OidcTestServer,
) -> None:
    identity = _validator(oidc_server).validate(_token(oidc_server))

    assert identity.issuer == oidc_server.issuer
    assert identity.subject == "Case-Sensitive-Subject"
    assert identity.email == "member@example.org"
    assert identity.display_name == "Member Name"


def test_uses_preferred_username_when_name_is_empty(
    oidc_server: OidcTestServer,
) -> None:
    identity = _validator(oidc_server).validate(
        _token(oidc_server, claims={"name": ""})
    )

    assert identity.display_name == "member-user"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"iss": "https://wrong-issuer.example"},
        {"aud": "wrong-audience"},
        {"sub": ""},
        {"sub": "x" * 256},
    ],
)
def test_rejects_invalid_required_claims(
    oidc_server: OidcTestServer,
    claims: dict[str, object],
) -> None:
    with pytest.raises(InvalidAccessTokenError):
        _validator(oidc_server).validate(_token(oidc_server, claims=claims))


def test_rejects_missing_required_claim(oidc_server: OidcTestServer) -> None:
    key = oidc_server.keys[0]
    token = jwt.encode(
        {
            "iss": oidc_server.issuer,
            "aud": "mic3-api",
            "exp": int(time()) + 300,
        },
        key.private_key,
        algorithm="RS256",
        headers={"kid": key.key_id},
    )

    with pytest.raises(InvalidAccessTokenError):
        _validator(oidc_server).validate(token)


def test_rejects_invalid_signature(oidc_server: OidcTestServer) -> None:
    untrusted_key = _signing_key(oidc_server.keys[0].key_id)

    with pytest.raises(InvalidAccessTokenError):
        _validator(oidc_server).validate(
            _token(oidc_server, signing_key=untrusted_key)
        )


def test_rejects_unsupported_algorithm(oidc_server: OidcTestServer) -> None:
    token = jwt.encode(
        {
            "iss": oidc_server.issuer,
            "sub": "subject",
            "aud": "mic3-api",
            "exp": int(time()) + 300,
        },
        "not-a-public-key-but-at-least-32-bytes-long",
        algorithm="HS256",
        headers={"kid": "key-1"},
    )

    with pytest.raises(InvalidAccessTokenError):
        _validator(oidc_server).validate(token)


def test_rejects_malformed_token(oidc_server: OidcTestServer) -> None:
    with pytest.raises(InvalidAccessTokenError):
        _validator(oidc_server).validate("not-a-jwt")


def test_refreshes_jwks_when_a_new_key_id_appears(
    oidc_server: OidcTestServer,
) -> None:
    validator = _validator(oidc_server)
    validator.validate(_token(oidc_server))
    rotated_key = _signing_key("key-2")
    oidc_server.keys = [rotated_key]

    identity = validator.validate(
        _token(oidc_server, signing_key=rotated_key)
    )

    assert identity.subject == "Case-Sensitive-Subject"


def test_rejects_discovery_with_a_different_issuer(
    oidc_server: OidcTestServer,
) -> None:
    oidc_server.reported_issuer = "https://different-issuer.example"

    with pytest.raises(IdentityProviderUnavailableError):
        _validator(oidc_server).validate(_token(oidc_server))


def test_reports_unusable_signing_keys_as_provider_unavailable(
    oidc_server: OidcTestServer,
) -> None:
    token = _token(oidc_server)
    oidc_server.keys = []

    with pytest.raises(IdentityProviderUnavailableError):
        _validator(oidc_server).validate(token)
