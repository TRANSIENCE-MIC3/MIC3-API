"""Read-only acceptance with separately supplied operator-selected tokens."""
import os

import httpx2
import pytest


@pytest.mark.smoke
def test_admin_authorization():
    names = ("API_BASE_URL", "OIDC_ADMIN_ACCESS_TOKEN", "OIDC_MEMBER_ACCESS_TOKEN")
    values = [os.getenv(name, "").strip() for name in names]
    if not all(values):
        pytest.fail("Required: " + ", ".join(names))
    base, admin, member = values
    url = base.rstrip("/") + "/users"
    for token, expected in [(admin, 200), (member, 403), (None, 401), ("invalid", 401)]:
        response = httpx2.get(url, headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=10)
        assert response.status_code == expected
