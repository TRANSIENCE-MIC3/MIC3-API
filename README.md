# mic3-api

This repository contains mic3-api, the HTTP API for the modeling platform. The current
milestone is intentionally small: provide a stateless FastAPI health endpoint,
package it as a Docker image, and validate deployment on EOSC/OKD before adding
model execution or persistent infrastructure.

## Current endpoints

- `GET /health` returns `{"status": "healthy"}`.
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

The application version is defined once in `pyproject.toml`. Release Git tags
use the same version with a `v` prefix, for example `v0.1.2`.

The installed distribution, API title, container image, and Kubernetes resources
use `mic3-api`. Python code uses the import package `mic3_api`, because hyphens
are not valid in ordinary Python imports. Reinstall the project after changing
package metadata so the local installation reflects it.

Run the API from the repository root:

```powershell
python -m uvicorn mic3_api.main:create_app --factory --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000/health> or
<http://localhost:8000/docs>.

## Tests

Run the in-process integration tests without Docker or network access:

```powershell
python -m pytest tests/integration
```

The smoke test targets a real running environment. In PowerShell:

```powershell
$env:API_BASE_URL = "https://your-eosc-route.example"
python -m pytest tests/smoke
```

In a POSIX shell:

```bash
API_BASE_URL=https://your-eosc-route.example python -m pytest tests/smoke
```

## Docker

Build and run the application image:

```powershell
docker build -t mic3-api:dev .
docker run --rm -p 8000:8000 mic3-api:dev
```

The same image receives environment-specific configuration from the deployment
environment.

## EOSC/OKD deployment preparation

`deploy/okd/application.yaml` defines one Deployment, a ClusterIP Service, and an
edge-TLS Route. It references the private image repository
`ghcr.io/transience-mic3/mic3-api` and requires an image-pull Secret named
`ghcr-pull` in the target project. Create that Secret separately; never put its
credentials in Git. The Route exposes the API without authentication.

The manifest currently targets the pending `0.1.2` release. Do not deploy it
until that image has been published and its verified digest has been pinned.

For each release:

1. Review and promote the application changes through staging to master.
2. Create and push a Git tag matching the package version, such as `v0.1.2`.
   The existing Publish API image workflow tests and publishes that release.
3. Update the manifest to the published image's tag and verified digest. A
   digest-only deployment update needs no new application version or rebuild.
4. Review and promote the manifest update, then validate it against the selected
   EOSC project before applying it. After deployment, run the smoke test using
   the Route URL as `API_BASE_URL`.

The manifest does not hard-code a namespace or Route hostname. For another EOSC
project, recreate its image-pull Secret and review the resource requests/limits
before reusing the deployment configuration.
