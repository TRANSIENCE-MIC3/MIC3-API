import os

import httpx2
import pytest


@pytest.fixture(scope="module")
def base_url() -> str:
    base_url = os.getenv("API_BASE_URL", "").strip()
    if not base_url:
        pytest.fail(
            "API_BASE_URL is required for smoke tests, for example "
            "API_BASE_URL=https://your-eosc-route.example"
        )
    return base_url.rstrip("/")


def get_endpoint(base_url: str, path: str) -> httpx2.Response:
    url = f"{base_url}{path}"
    try:
        return httpx2.get(url, timeout=10.0, follow_redirects=True)
    except httpx2.HTTPError as exc:
        pytest.fail(f"Unable to request {url}: {exc}")


@pytest.mark.smoke
def test_deployed_health_endpoint(base_url: str) -> None:
    response = get_endpoint(base_url, "/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "healthy"}


@pytest.mark.smoke
def test_deployed_readiness_endpoint(base_url: str) -> None:
    response = get_endpoint(base_url, "/ready")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ready"}
