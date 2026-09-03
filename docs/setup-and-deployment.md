# Setup and deployment

## Local

Activate a Python 3.13 Conda/venv environment and start Docker with
Linux containers. Run commands from the repository root.

### Install

Copy [`.env.example`](../.env.example) to `.env` if it does not exist. Set a
local-only `DB_PASSWORD`; keep the other defaults. Never commit `.env`.

```text
python -m pip install -r requirements-dev.txt
```

### PostgreSQL

```text
docker compose up -d --wait postgres
```

Compose creates database `mic3` and user `mic3_api` on first startup.
Connect at `127.0.0.1:5433` using your `.env` password.
Data persists in Docker's named volume across container replacements; keep the
volume and existing database/user names. A native PostgreSQL install is not needed.

### API

```text
python -m alembic upgrade head
python -m uvicorn mic3_api.main:create_app --factory --reload
```

Alembic creates or updates the MIC3-owned application schema. It does not run
automatically when FastAPI starts.
The API runs on your host. Open [Swagger UI](http://localhost:8000/docs).

### Tests

Unit and API-only integration tests do not need running services:

```text
python -m pytest tests/unit tests/integration/api
```

The complete integration suite requires a running Docker engine. Testcontainers
starts and removes its own disposable PostgreSQL instance, separate from the
persistent Compose database and any EOSC database:

```text
python -m pytest tests/unit tests/integration
```

With PostgreSQL and the API running, run smoke tests in another terminal using
the same Python environment. In PowerShell:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
python -m pytest tests/smoke
```

`API_BASE_URL` is a test-only environment variable, not read from `.env`.
You can also set it in your IDE's test configuration. Smoke tests check
`/health` and database-aware `/ready`.

## EOSC

Not required for local development. Authenticate using EOSC's supplied login
instructions and select the intended project before deploying.

Create these once in the EOSC console, or reuse them if they already exist:

- Secret `mic3-postgres-credentials`: `database=mic3`, `username=mic3_api`,
  and `password` with a cloud-only password.
- PVC `mic3-postgres-data`: `ReadWriteOnce`, filesystem storage, with capacity
  and StorageClass chosen for your project.
- Image pull Secret `ghcr-pull`: `ghcr.io`, GitHub username, and a token
  with `read:packages` access to the private image.

From reviewed `master`, with a published, digest-pinned API image, run each
command separately and stop if it fails:

```text
oc apply -f deploy/okd/postgres.yaml
oc rollout status statefulset/mic3-postgres --timeout=180s
oc apply -f deploy/okd/application.yaml
oc rollout status deployment/mic3-api --timeout=180s
```

PostgreSQL stays internal-only; reuse its PVC to preserve
data. Get the API's HTTPS URL from the `mic3-api` Route in the EOSC console and
use it as `API_BASE_URL` for the same smoke tests above, run from your computer or CI.
