# mic3-api

MIC3's FastAPI control plane for a platform that will execute independent
scientific models. The current service provides PostgreSQL-backed user profiles
and OIDC authentication through a separately deployed Keycloak instance, locally
and on EOSC/OKD. Model execution is planned, not yet implemented.

## Current endpoints

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Public, dependency-independent health check |
| `GET /ready` | PostgreSQL readiness; returns `503` when unavailable |
| `GET /users/me` | Authenticated MIC3 profile and local roles |
| `GET /docs` | Swagger UI |
| `GET /openapi.json` | OpenAPI schema |

## Local quickstart

Use Python 3.13 in an activated environment and Docker with Linux containers.
Run from the repository root:

```powershell
python -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set distinct local-only database and Keycloak passwords in `.env`; never use
EOSC credentials. Then start the dependencies and host-based API:

```powershell
docker compose up -d --wait postgres keycloak
docker compose run --rm keycloak-config
python -m alembic upgrade head
python -m uvicorn mic3_api.main:create_app --factory --reload
```

Open [Swagger UI](http://localhost:8000/docs). See the runbook for
[Postman login](docs/setup-and-deployment.md#login-and-smoke-checks) and
[tests](docs/setup-and-deployment.md#tests).

## Documentation

- [Project status](PROJECT_STATUS.md) (local, Git-ignored): current capabilities, unfinished work, and implementation order.
- [Architecture](docs/architecture/prd.md): component boundaries, Kafka execution, model adapters, and result reuse.
- [Setup and deployment](docs/setup-and-deployment.md): local development, EOSC operations, releases, and recovery.
- [Agent instructions](AGENTS.md) (local, Git-ignored): engineering constraints and validation expectations.

Stable version tags trigger CI tests, image publication, database migration,
deployment, and public checks. See [release instructions](docs/setup-and-deployment.md#release-the-api).
The version is defined in `pyproject.toml`; release tags use `v` followed by that
version. The distribution/service name is `mic3-api`, and the Python package is
`mic3_api`. Reinstall the package after changing its metadata.
