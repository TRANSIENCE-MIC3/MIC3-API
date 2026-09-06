# Setup and deployment

## Local

Activate a Python 3.13 Conda/venv environment and start Docker with
Linux containers. Run commands from the repository root.

### Install

Copy [`.env.example`](../.env.example) to `.env` if it does not exist. Set a
local-only `DB_PASSWORD`; keep the other defaults. For an existing `.env`, copy
the `OIDC_*` and `KEYCLOAK_*` entries from `.env.example`. Replace the three
example Keycloak passwords with distinct local-only values. Never commit
`.env`.

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

### Keycloak and local OIDC

Keycloak uses its own PostgreSQL container, database user, database, and named
volume. It never reads or writes the MIC3 application database.

Start Keycloak and its database:

```text
docker compose up -d --wait keycloak
docker compose ps
```

The first start imports the checked-in `mic3` realm and creates:

- the bearer-only `mic3-api` resource-server client;
- the public `mic3-local` browser client using authorization code flow with
  PKCE, the standard OIDC subject claim, and the `mic3-api` access-token
  audience;
- local self-registration without email verification, because the development
  stack has no SMTP service;
- one non-elevated local identity using `KEYCLOAK_DEV_USERNAME`,
  `KEYCLOAK_DEV_EMAIL`, and `KEYCLOAK_DEV_PASSWORD` from `.env`.

Open the [Admin Console](http://localhost:8080/admin/) and sign in with
`KEYCLOAK_ADMIN_USERNAME` and `KEYCLOAK_ADMIN_PASSWORD`. Select the `mic3`
realm to inspect its clients and local identity. The master-realm administrator
is Keycloak infrastructure administration only and is not a MIC3 application
administrator.

Verify Keycloak readiness and its provider-independent OIDC discovery/JWKS
contract in PowerShell:

```powershell
Invoke-RestMethod http://localhost:9000/health/ready
$env:OIDC_ISSUER_URL = "http://localhost:8080/realms/mic3"
python -m pytest tests/smoke/test_oidc.py
```

The issuer is `http://localhost:8080/realms/mic3`. Keycloak imports a realm at
startup only when it does not already exist, so editing the JSON does not
overwrite persisted local realm state. To intentionally re-import it, delete
only the disposable `mic3` realm through the Admin Console and restart
Keycloak. This removes locally registered Keycloak test identities but does not
touch the MIC3 application database:

```text
docker compose restart keycloak
```

Never use `docker compose down --volumes` merely to refresh the realm because
that also deletes the persistent MIC3 PostgreSQL volume.

### Postman login without a frontend

The API is an OAuth2 resource server, not the login redirect target. Postman
temporarily acts as the public client that a browser frontend will later
replace. In Postman, create a request or collection using **OAuth 2.0**, select
**Authorization Code (With PKCE)**, and enter:

| Setting | Value |
| --- | --- |
| Callback URL | `https://oauth.pstmn.io/v1/browser-callback` |
| Auth URL | `http://localhost:8080/realms/mic3/protocol/openid-connect/auth` |
| Access Token URL | `http://localhost:8080/realms/mic3/protocol/openid-connect/token` |
| Client ID | `mic3-local` |
| Client Secret | leave empty |
| Scope | `openid profile email` |
| Code Challenge Method | `SHA-256` |

Leave client authentication unset/no-secret. Select **Get New Access Token**;
Postman opens the `mic3` realm login page. Sign in as the seeded local member or
use the **Register** link to create another local test identity. Use the token
on a `GET http://localhost:8000/users/me` request. Postman sends it as:

```text
Authorization: Bearer <access-token>
```

The first successful request creates one MIC3 user, exact issuer/subject
identity mapping, and `member` assignment in a single transaction. Repeating
the request returns the same internal user. MIC3 stores neither the password
nor the token.

A future frontend receives its own public OIDC client and exact website
callback URI. It will use the same authorization-code/PKCE flow and
`mic3-api` audience, so FastAPI validation and the `/users/me` contract do not
change. CORS will be configured when that frontend origin exists.

### API

```text
python -m alembic upgrade head
python -m uvicorn mic3_api.main:create_app --factory --reload
```

Alembic creates or updates the MIC3-owned application schema. It does not run
automatically when FastAPI starts.
The API runs on your host. Open [Swagger UI](http://localhost:8000/docs).
`OIDC_ISSUER_URL` and `OIDC_AUDIENCE` configure standards-based validation;
there is no Keycloak client secret in the API.

### Tests

Unit and dependency-independent health/readiness tests do not need running
services:

```text
python -m pytest tests/unit tests/integration/api/test_health.py tests/integration/api/test_readiness.py
```

The complete integration suite requires a running Docker engine. Testcontainers
starts and removes its own disposable PostgreSQL instance, separate from the
persistent Compose database and any EOSC database:

```text
python -m pytest tests/unit tests/integration
```

With PostgreSQL and the API running, run public smoke tests in another terminal
using the same Python environment. In PowerShell:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
python -m pytest tests/smoke/test_health.py
```

`API_BASE_URL` is a test-only environment variable, not read from `.env`.
You can also set it in your IDE's test configuration. Smoke tests check
`/health` and database-aware `/ready`.

For the authenticated smoke path, also run Keycloak and copy the temporary
access token obtained through Postman into the current terminal only:

```powershell
$env:OIDC_ISSUER_URL = "http://localhost:8080/realms/mic3"
$env:OIDC_ACCESS_TOKEN = "<temporary access token from Postman>"
python -m pytest tests/smoke/test_oidc.py tests/smoke/test_authenticated_user.py
Remove-Item Env:OIDC_ACCESS_TOKEN
```

Do not put an access or refresh token in `.env`, shell profiles, test files, or
Postman exports committed to the repository.

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
