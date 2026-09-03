# mic3-api

This repository contains mic3-api, the HTTP API for the modeling platform. The
PostgreSQL connectivity foundation is deployed on EOSC/OKD: the API provides
database readiness alongside dependency-independent health checks. Application
schema, users, and authentication follow in later changes.

See the [installation and deployment guide](docs/setup-and-deployment.md) for
local setup, tests, and EOSC deployment.

## Current endpoints

- `GET /health` returns `{"status": "healthy"}`.
- `GET /ready` checks PostgreSQL and returns `{"status": "ready"}` or a `503`
  response with `{"status": "not_ready"}`.
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
The file is excluded from Git and Docker builds.

```powershell
docker compose up -d --wait postgres
python -m alembic current
python -m uvicorn mic3_api.main:create_app --factory --reload
```

Then open <http://localhost:8000/health>, <http://localhost:8000/ready>, or
<http://localhost:8000/docs>. Compose runs PostgreSQL only; the API runs on the
host for development. PostgreSQL uses `127.0.0.1:5433` and a persistent named
volume, independently of any native PostgreSQL installation.

## Tests

Run the isolated tests without Docker or network access:

```powershell
python -m pytest tests/unit tests/integration
```

The same smoke tests target a local or deployed running API:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
python -m pytest tests/smoke
```

## Deployment

The [EOSC setup](docs/setup-and-deployment.md#eosc)
covers the Secret, PVC, and PostgreSQL StatefulSet/ClusterIP Service. PostgreSQL
has no public Route. The API has a Deployment, ClusterIP Service, and edge-TLS
Route, and its private GHCR image requires the `ghcr-pull` Secret.

The manifests use verified image digests and do not hard-code project IDs or
Route hostnames. Authentication is not implemented yet; the API endpoints are
public.

## Naming and versioning

The application version is defined in `pyproject.toml`; release Git tags use a
`v` prefix, for example `v0.1.3`. The distribution, API title, container image,
and Kubernetes resources use `mic3-api`; the Python import package is
`mic3_api`. Reinstall the project after changing package metadata.
