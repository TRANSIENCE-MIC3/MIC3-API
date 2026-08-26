# Modeling Platform API

This repository contains the HTTP API for the Modeling Platform. The current
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
| `APP_NAME` | `Modeling Platform API` |
| `APP_VERSION` | `0.1.0` |

Run the API from the repository root:

```powershell
python -m uvicorn modeling_platform.main:create_app --factory --app-dir src --host 0.0.0.0 --port 8000
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
docker build -t modeling-platform-api:dev .
docker run --rm -p 8000:8000 modeling-platform-api:dev
```

The same image is intended to receive environment-specific configuration from
the deployment environment. Kubernetes manifests and integrations are deferred
until the local and containerized health API have been validated.
