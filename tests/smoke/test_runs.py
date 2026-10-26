"""Opt-in live execution: creates one real buildings run and waits for its outcome."""
import os
import time
from uuid import UUID

import httpx2
import pytest


@pytest.mark.smoke
def test_authenticated_buildings_execution():
    if os.getenv("RUN_EXECUTION_SMOKE") != "1":
        pytest.skip("Set RUN_EXECUTION_SMOKE=1 to authorize one live buildings execution.")
    base = os.environ["API_BASE_URL"].rstrip("/")
    release_id = str(UUID(os.environ["RUN_MODEL_RELEASE_ID"]))
    headers = {"Authorization": f"Bearer {os.environ['OIDC_ACCESS_TOKEN']}"}
    deadline = time.monotonic() + float(os.getenv("RUN_SMOKE_TIMEOUT_SECONDS", "1800"))
    with httpx2.Client(base_url=base, headers=headers, timeout=20) as client:
        response = client.post("/runs", json={"model_release_id": release_id, "parameters": {"mode": "buildings"}})
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        print(f"Submitted buildings run {run_id}")
        assert response.headers["location"] == f"/runs/{run_id}"
        listing = client.get("/runs?limit=100")
        assert listing.status_code == 200, listing.text
        assert run_id in {item["id"] for item in listing.json()["items"]}
        while time.monotonic() < deadline:
            response = client.get(f"/runs/{run_id}")
            assert response.status_code == 200, response.text
            run = response.json()
            if run["status"] in ("succeeded", "failed"):
                assert run["status"] == "succeeded", f"Run {run_id}: {run}"
                assert run["finished_at"] and any(file["size_bytes"] > 0 for file in run["artifacts"])
                return
            time.sleep(5)
        pytest.fail(f"Run {run_id} did not finish before the configured timeout; it remains durable work.")
