import os

import httpx2
import pytest


@pytest.fixture(scope="module")
def api_base_url() -> str:
    base_url = os.getenv("API_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        pytest.fail("API_BASE_URL is required, for example http://localhost:8000")
    return base_url


@pytest.fixture(scope="module")
def access_token() -> str:
    token = os.getenv("OIDC_ACCESS_TOKEN", "").strip()
    if not token:
        pytest.fail("OIDC_ACCESS_TOKEN is required; obtain it through Postman PKCE")
    return token


@pytest.mark.smoke
def test_access_token_resolves_one_member_profile(
    api_base_url: str,
    access_token: str,
) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    first = httpx2.get(f"{api_base_url}/users/me", headers=headers, timeout=10.0)
    second = httpx2.get(f"{api_base_url}/users/me", headers=headers, timeout=10.0)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert first.json()["roles"] == sorted(first.json()["roles"])
    assert "member" in first.json()["roles"]
