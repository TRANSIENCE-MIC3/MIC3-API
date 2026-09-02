# mic3-api

This repository contains mic3-api, the HTTP API for the modeling platform. The
current milestone adds PostgreSQL connectivity and database readiness while
preserving the deployed health-only baseline. Application schema, users, and
authentication follow in later changes.

## Current endpoints

- `GET /health` returns `{"status": "healthy"}`.
- `GET /ready` checks PostgreSQL and returns `{"status": "ready"}` or a `503`
  response with `{"status": "not_ready"}`.
- `GET /docs` serves FastAPI's generated Swagger UI.
- `GET /openapi.json` serves FastAPI's generated OpenAPI schema.

## Local setup

The development environment uses Python 3.13 in the Conda environment named
`transience`.

```powershell
conda activate transience
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

`.env` is for local configuration and is excluded from Git and Docker builds.
Environment variables override values from the file.

| Variable | Default |
| --- | --- |
| `APP_ENV` | `development` |
| `APP_NAME` | `mic3-api` |
| `DB_HOST` | Required; `127.0.0.1` in `.env.example` |
| `DB_PORT` | `5432`; local Compose uses `5433` |
| `DB_NAME` | Required; `mic3` locally |
| `DB_USER` | Required; `mic3_api` locally |
| `DB_PASSWORD` | Required; use a local-only value in `.env` |

The application version is defined once in `pyproject.toml`. Release Git tags
use the same version with a `v` prefix, for example `v0.1.3`.

The installed distribution, API title, container image, and Kubernetes resources
use `mic3-api`. Python code uses the import package `mic3_api`, because hyphens
are not valid in ordinary Python imports. Reinstall the project after changing
package metadata so the local installation reflects it.

Start the local PostgreSQL 18 dependency. Docker stores its data in the Compose
volume `mic3-postgres-data` (normally project-prefixed by Docker); ordinary
container removal does not remove it.

```powershell
docker compose up -d --wait postgres
docker compose exec postgres /bin/sh -c 'PGPASSWORD="$POSTGRESQL_PASSWORD" psql -U "$POSTGRESQL_USER" -d "$POSTGRESQL_DATABASE" -c "SELECT 1;"'
```

The container listens on port `5432`, mapped to `127.0.0.1:5433` to avoid a
conflict with a native PostgreSQL installation. Run Alembic's non-mutating
connectivity check and start the API from the repository root:

```powershell
python -m alembic current
python -m uvicorn mic3_api.main:create_app --factory --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000/health>, <http://localhost:8000/ready>, or
<http://localhost:8000/docs>. `alembic current` creates no schema or migration
version table when no revision exists.

The equivalent POSIX setup commands are:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d --wait postgres
python -m alembic current
python -m uvicorn mic3_api.main:create_app --factory --host 0.0.0.0 --port 8000
```

## Tests

Run the isolated tests without Docker or network access:

```powershell
python -m pytest tests/unit tests/integration
```

The same smoke tests target a local or deployed running environment. In
PowerShell:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
python -m pytest tests/smoke
```

In a POSIX shell:

```bash
API_BASE_URL=http://localhost:8000 python -m pytest tests/smoke
```

## Docker

`compose.yaml` runs only the local PostgreSQL dependency so the API can run and
be debugged on the host. Build the application image separately when needed:

```powershell
docker build -t mic3-api:dev .
```

Stopping or removing the PostgreSQL container preserves its named volume. The
following reset is destructive and is appropriate only for disposable local
data:

```powershell
docker compose down -v
```

## EOSC/OKD deployment preparation

`deploy/okd/postgres.yaml` defines a one-replica PostgreSQL StatefulSet and
internal ClusterIP Service. It reuses the existing `mic3-postgres-data` PVC and
the `mic3-postgres-credentials` Secret. PostgreSQL has no Route and is not
publicly accessible.

`deploy/okd/application.yaml` defines the API Deployment, ClusterIP Service, and
edge-TLS Route. It references the private image repository
`ghcr.io/transience-mic3/mic3-api` and requires an image-pull Secret named
`ghcr-pull` in the target project. Create that Secret separately; never put its
credentials in Git. The Route exposes the API without authentication.

The manifest currently targets the pending `0.1.3` release. Do not deploy it
until that image has been published and its verified digest has been pinned.

For each release:

1. Review and promote the application changes through staging to master.
2. Create and push a Git tag matching the package version, such as `v0.1.3`.
   The existing Publish API image workflow tests and publishes that release.
3. Update the manifest to the published image's tag and verified digest. A
   digest-only deployment update needs no new application version or rebuild.
4. Review and promote the manifest update, then validate it against the selected
   EOSC project before applying it. After deployment, run the smoke test using
   the Route URL as `API_BASE_URL`.

The manifests do not hard-code a namespace or Route hostname. For another EOSC
project, recreate the Secrets and PVC and review resource requests/limits before
reusing the deployment configuration.
