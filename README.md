# mic3-api

This repository contains mic3-api, the HTTP API for the modeling platform. The
PostgreSQL connectivity is deployed on EOSC/OKD, and the initial user,
external-identity, and member-role schema is managed through Alembic. The API
provides database readiness alongside dependency-independent health checks.
The local development path validates OIDC access tokens and provisions MIC3
member profiles from the separate Keycloak identity provider.

See the [installation and deployment guide](docs/setup-and-deployment.md) for
local setup, tests, and EOSC deployment.

## Current endpoints

- `GET /health` returns `{"status": "healthy"}`.
- `GET /ready` checks PostgreSQL and returns `{"status": "ready"}` or a `503`
  response with `{"status": "not_ready"}`.
- `GET /users/me` validates an OIDC bearer token and returns the active MIC3
  profile and local roles.
- `GET /docs` serves FastAPI's generated Swagger UI.
- `GET /openapi.json` serves FastAPI's generated OpenAPI schema.

## Local quickstart

Use Python 3.13 and a running Docker engine with Linux containers. These commands
assume the existing Conda environment `transience` and a terminal in the
repository root; an activated Python 3.13 venv also works.

```powershell
conda activate transience
python -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Review `.env` and use a local-only database password, never EOSC credentials.
The file is excluded from Git and Docker builds. If `.env` already existed,
copy the `OIDC_*` and `KEYCLOAK_*` entries from `.env.example` and replace the
example passwords with local-only values.

```powershell
docker compose up -d --wait postgres
python -m alembic upgrade head
python -m uvicorn mic3_api.main:create_app --factory --reload
```

Then open <http://localhost:8000/health>, <http://localhost:8000/ready>, or
<http://localhost:8000/docs>. The command above starts only the MIC3 PostgreSQL
service; the API runs on the host for development. PostgreSQL uses
`127.0.0.1:5433` and a persistent named volume, independently of any native
PostgreSQL installation.

Start the separate local Keycloak database and identity provider with:

```powershell
docker compose up -d --wait keycloak
$env:OIDC_ISSUER_URL = "http://localhost:8080/realms/mic3"
python -m pytest tests/smoke/test_oidc.py
```

The Admin Console is at <http://localhost:8080/admin/>. The reproducible `mic3`
realm contains the `mic3-api` audience, a PKCE-enabled `mic3-local` browser
client, local self-registration, and the member identity configured through
`.env`. The detailed guide contains the exact Postman login settings.

## Tests

Unit and dependency-independent health/readiness tests run without Docker or
network access:

```powershell
python -m pytest tests/unit tests/integration/api/test_health.py tests/integration/api/test_readiness.py
```

With Docker running, execute the complete unit/integration suite. Testcontainers
starts and removes a disposable PostgreSQL instance for schema and migration
tests; it does not use the persistent Compose or EOSC databases.

```powershell
python -m pytest tests/unit tests/integration
```

Public smoke tests target a local or deployed running API:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
python -m pytest tests/smoke/test_health.py
```

With local Keycloak running, discovery and an access token copied from Postman
exercise the real authenticated path:

```powershell
$env:OIDC_ISSUER_URL = "http://localhost:8080/realms/mic3"
$env:OIDC_ACCESS_TOKEN = "<temporary access token from Postman>"
python -m pytest tests/smoke/test_oidc.py tests/smoke/test_authenticated_user.py
```

## Deployment

The [EOSC setup](docs/setup-and-deployment.md#eosc)
covers the Secret, PVC, and PostgreSQL StatefulSet/ClusterIP Service. PostgreSQL
has no public Route. The API has a Deployment, ClusterIP Service, and edge-TLS
Route, and its private GHCR image requires the `ghcr-pull` Secret.

The manifests use verified image digests and do not hard-code project IDs or
Route hostnames. The deployed EOSC image has not yet been promoted to this
local authentication implementation, and this change adds no EOSC Keycloak
resources.

## Naming and versioning

The application version is defined in `pyproject.toml`; release Git tags use a
`v` prefix, for example `v0.1.3`. The distribution, API title, container image,
and Kubernetes resources use `mic3-api`; the Python import package is
`mic3_api`. Reinstall the project after changing package metadata.
