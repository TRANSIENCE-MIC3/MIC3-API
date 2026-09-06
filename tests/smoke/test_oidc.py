import os

import httpx2
import pytest


@pytest.fixture(scope="module")
def issuer_url() -> str:
    issuer_url = os.getenv("OIDC_ISSUER_URL", "").strip().rstrip("/")
    if not issuer_url:
        pytest.fail(
            "OIDC_ISSUER_URL is required, for example "
            "OIDC_ISSUER_URL=http://localhost:8080/realms/mic3"
        )
    return issuer_url


def get_json(url: str) -> dict[str, object]:
    try:
        response = httpx2.get(url, timeout=10.0, follow_redirects=True)
    except httpx2.HTTPError as exc:
        pytest.fail(f"Unable to request {url}: {exc}")

    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.smoke
def test_oidc_discovery_and_signing_keys(issuer_url: str) -> None:
    discovery = get_json(f"{issuer_url}/.well-known/openid-configuration")

    assert discovery["issuer"] == issuer_url
    assert discovery["authorization_endpoint"] == (
        f"{issuer_url}/protocol/openid-connect/auth"
    )
    assert discovery["token_endpoint"] == (
        f"{issuer_url}/protocol/openid-connect/token"
    )

    signing_keys = get_json(str(discovery["jwks_uri"]))
    assert signing_keys["keys"]
