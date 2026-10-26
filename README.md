# mic3-api

MIC3's FastAPI control plane for a platform that will execute independent
scientific models. The current service provides PostgreSQL-backed profiles,
Keycloak OIDC authentication, and local member/admin authorization, locally and
on EOSC/OKD. A public model catalog and operator CLI support a configurable model
release catalog and durable queued submissions. A separate outbox publisher
forwards committed requests to Kafka; a separate worker records durable acceptance
or message rejection in PostgreSQL. With `--execute`, the worker dispatches and
recovers Kubernetes Jobs, verifies uploaded artifacts, and persists outcomes.
Kafka and the execution services are deployed on EOSC, where a buildings run and
uploaded outputs have been verified by the operator. Local kind/AIStor execution
uses the same model and storage charts. Authenticated users can submit runs with
`POST /runs`, inspect owned runs with `GET /runs/{id}`, and list their history with
`GET /runs`; admins may inspect any run by ID. The operator CLI remains available. Release
automation deploys all three services and checks a small diagnostic run through
Kafka and artifact storage after the one-time deployment bootstrap.

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

Use [Swagger UI](http://localhost:8000/docs) for endpoints and schemas. See the runbook for
[Postman login](docs/setup-and-deployment.md#login-and-smoke-checks) and
[tests](docs/setup-and-deployment.md#tests).

## Documentation

- [Project status](PROJECT_STATUS.md) (local, Git-ignored): current capabilities, unfinished work, and implementation order.
- [Architecture](docs/architecture/prd.md): component boundaries, Kafka execution, model adapters, and result reuse.
- [Setup and deployment](docs/setup-and-deployment.md): releases, debugging, and essential operations.
- [Agent instructions](AGENTS.md) (local, Git-ignored): engineering constraints and validation expectations.

The distribution/service name is `mic3-api`; the Python package is `mic3_api`.
Reinstall the package after changing its metadata.
