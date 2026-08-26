import os

import httpx2
import pytest


@pytest.mark.smoke
def test_deployed_health_endpoint() -> None:
    base_url = os.getenv("API_BASE_URL", "").strip()
    if not base_url:
        pytest.fail(
            "API_BASE_URL is required for smoke tests, for example "
            "API_BASE_URL=https://your-eosc-route.example"
        )

    health_url = f"{base_url.rstrip('/')}/health"
    try:
        response = httpx2.get(health_url, timeout=10.0, follow_redirects=True)
    except httpx2.HTTPError as exc:
        pytest.fail(f"Unable to request {health_url}: {exc}")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "healthy"}
