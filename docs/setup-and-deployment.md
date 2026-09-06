# Setup and deployment

## Local

Activate a Python 3.13 Conda/venv environment and start Docker with
Linux containers. Run commands from the repository root.

### Install

Copy [`.env.example`](../.env.example) to `.env` if it does not exist. Set a
local-only `DB_PASSWORD`; keep the other defaults. For an existing `.env`, copy
the `OIDC_*` and `KEYCLOAK_*` entries from `.env.example`. Replace the two
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
docker compose run --rm keycloak-config
docker compose ps
```

The first command starts only Keycloak and its PostgreSQL database. The second
runs the pinned `keycloak-config-cli` image as a one-shot task after Keycloak is
healthy. It creates the realm when absent or reconciles the checked-in desired
configuration when the realm already exists. Run the configuration command
after every accepted realm configuration change.

The desired local configuration includes:

- the bearer-only `mic3-api` resource-server client;
- the public `mic3-local` browser client using authorization code flow with
  PKCE, the standard OIDC subject claim, and the `mic3-api` access-token
  audience;
- local self-registration without email verification, because the development
  stack has no SMTP service.

Open the [Admin Console](http://localhost:8080/admin/) and sign in with
`KEYCLOAK_ADMIN_USERNAME` and `KEYCLOAK_ADMIN_PASSWORD`. Select the `mic3`
realm to inspect its clients and local users. The master-realm administrator
is Keycloak infrastructure administration only and is not a MIC3 application
administrator.

Verify Keycloak readiness and its provider-independent OIDC discovery/JWKS
contract in PowerShell:

```powershell
Invoke-RestMethod http://localhost:9000/health/ready
$env:OIDC_ISSUER_URL = "http://localhost:8080/realms/mic3"
python -m pytest tests/smoke/test_oidc.py
```

The issuer is `http://localhost:8080/realms/mic3`. Realm settings, clients,
client scopes, and protocol mappers declared in
`deploy/local/keycloak/config/mic3-realm.json` are authoritative: a later
reconciliation can restore manual Admin Console edits to those resources.
Users, roles, and groups are deliberately absent from the file and are not
managed or deleted by reconciliation. You can experiment in the Admin Console,
then copy accepted configuration into the JSON and verify it by running the
same one-shot task again:

```text
docker compose run --rm keycloak-config
```

The task must exit successfully before treating a configuration change as
applied. Do not delete the realm, the Keycloak volume, or the MIC3 PostgreSQL
volume to refresh configuration.

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
Postman opens the `mic3` realm login page. Use the **Register** link to create a
local test identity, or sign in with one you previously registered. Use the token
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
starts and removes disposable PostgreSQL and Keycloak instances, including a
real `keycloak-config-cli` compatibility check. These containers are separate
from the persistent Compose databases and any EOSC resources:

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

This is a single-replica integration deployment. MIC3 and Keycloak have
separate PostgreSQL Services, credentials, databases, and PVCs. The API and
Keycloak use OpenShift edge-TLS Routes backed by the cluster's trusted wildcard
certificate, so this deployment does not need an Ingress or a custom TLS
certificate. Neither PostgreSQL Service nor Keycloak's management port is
public.

The EOSC realm intentionally disables public registration, email verification,
and password reset. Create the temporary integration user through the Keycloak
Admin Console. Mail delivery, verified registration, recovery, password policy,
and abuse protection are a later production-hardening change.

Run the following sections in order. Commands that modify EOSC are deliberately
manual: verify the selected project before every deployment session and stop at
the first failure. Never delete either PostgreSQL PVC as a recovery action.

### 1. Preflight

Log in using EOSC's supplied `oc login` command, select the intended project,
then confirm it and ask the API server to validate all resources without saving
them:

```powershell
oc project -q
oc apply --dry-run=server -f deploy/okd/keycloak/prerequisites.yaml
oc apply --dry-run=server -k deploy/okd/keycloak
oc create --dry-run=server -f deploy/okd/keycloak/configure.yaml -o yaml | Out-Null
oc apply --dry-run=server -f deploy/okd/application.yaml
oc apply --dry-run=server -f deploy/okd/migration.yaml
```

The existing one-time resources must remain available:

- `mic3-postgres-credentials` for database `mic3` and user `mic3_api`;
- `mic3-postgres-data`, preserving MIC3 application data;
- `ghcr-pull`, with `read:packages` access to the private GHCR image.

### 2. Create Keycloak Secrets once

Generate two distinct URL-safe passwords in local PowerShell. Save both in a
password manager before closing the terminal; the administrator password is
needed for the Admin Console, while the database password must remain stable
for the persisted Keycloak database.

```powershell
function New-UrlSafePassword {
  param([int]$ByteCount = 32)
  $bytes = New-Object byte[] $ByteCount
  $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
  [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

$keycloakDbPassword = New-UrlSafePassword
$keycloakAdminPassword = New-UrlSafePassword

$keycloakDbPassword | Set-Clipboard
Read-Host "Save the Keycloak database password, then press Enter"
$keycloakAdminPassword | Set-Clipboard
Read-Host "Save the Keycloak administrator password, then press Enter"
Set-Clipboard -Value ""

oc create secret generic mic3-keycloak-postgres-credentials `
  --from-literal=database="keycloak" `
  --from-literal=username="keycloak" `
  --from-literal=password="$keycloakDbPassword"

oc create secret generic mic3-keycloak-bootstrap-admin `
  --from-literal=username="admin" `
  --from-literal=password="$keycloakAdminPassword"

Remove-Variable keycloakDbPassword, keycloakAdminPassword
```

These deliberately use `oc create`, not an idempotent overwrite. If either
Secret already exists, stop and inspect it instead of silently rotating a
password that a persisted database still expects.

### 3. Create prerequisites and discover the public issuer

The prerequisites contain only the Keycloak PostgreSQL PVC, internal Services,
PostgreSQL StatefulSet, and public edge-TLS Route. They do not start Keycloak.

```powershell
oc apply -f deploy/okd/keycloak/prerequisites.yaml
oc rollout status statefulset/mic3-keycloak-postgres --timeout=180s

$keycloakHost = oc get route mic3-keycloak -o jsonpath='{.spec.host}'
$keycloakUrl = "https://$keycloakHost"
$oidcIssuer = "$keycloakUrl/realms/mic3"

oc create configmap mic3-keycloak-runtime `
  --from-literal=hostname="$keycloakUrl"

oc create secret generic mic3-oidc-config `
  --from-literal=issuer-url="$oidcIssuer" `
  --from-literal=audience="mic3-api"
```

The generated Route hostname is deliberately absent from Git. As with the
credential Secrets, stop and inspect an existing runtime ConfigMap or OIDC
Secret rather than overwriting it implicitly.

### 4. Deploy, configure, and verify Keycloak

```powershell
oc apply -k deploy/okd/keycloak
oc rollout status deployment/mic3-keycloak --timeout=300s
oc logs deployment/mic3-keycloak --tail=150

$configJob = oc create -f deploy/okd/keycloak/configure.yaml -o name
oc wait --for=condition=complete $configJob --timeout=180s
oc logs $configJob

$discovery = Invoke-RestMethod "$oidcIssuer/.well-known/openid-configuration"
if ($discovery.issuer -ne $oidcIssuer) {
  throw "OIDC issuer mismatch: expected $oidcIssuer, got $($discovery.issuer)"
}
Invoke-RestMethod "$oidcIssuer/protocol/openid-connect/certs"
```

Keycloak starts independently of realm configuration. The generated
configuration Job runs the pinned `keycloak-config-cli` image, waits for the
internal Keycloak Service, and then creates or reconciles the `mic3` realm
through the Admin API. A non-zero Job result is a deployment blocker: inspect
its logs and do not proceed to the API release. Completed configuration Jobs
are automatically removed after one day.

Run `Start-Process "$keycloakUrl/admin/"`, log in with the bootstrap
administrator, select
the `mic3` realm, and manually create a non-administrator test user. Set an
initial non-temporary password. The realm has no Keycloak application roles,
and its bootstrap administrator belongs to Keycloak's `master` realm rather
than MIC3.

Realm settings, the `mic3-api` and `mic3-postman` clients, client scopes, and
protocol mappers declared in `mic3-realm.json` are authoritative. Users, roles,
and groups are deliberately omitted, so reconciliation does not manage or
delete them. Manual Admin Console changes to managed resources can be restored
on the next run. After experimenting, record accepted changes in the JSON,
review them, update the ConfigMap with `oc apply -k deploy/okd/keycloak`, and run
a new generated configuration Job using the commands above. Do not delete the
realm or Keycloak PVC to apply a change.

In Postman, use **Authorization Code (With PKCE)** with:

| Setting | EOSC value |
| --- | --- |
| Callback URL | `https://oauth.pstmn.io/v1/browser-callback` |
| Auth URL | `$oidcIssuer/protocol/openid-connect/auth` |
| Access Token URL | `$oidcIssuer/protocol/openid-connect/token` |
| Client ID | `mic3-postman` |
| Client Secret | leave empty |
| Scope | `openid profile email` |
| Code Challenge Method | `SHA-256` |

Substitute the value of `$oidcIssuer` in the two URLs. The resulting access
token must contain a non-empty `sub`, `aud` containing `mic3-api`, and an `iss`
exactly equal to `$oidcIssuer`.

### 5. Publish and promote v0.1.4

Release `0.1.4` uses two commits because an image digest does not exist before
publication:

1. Merge the reviewed source/release commit, create and push tag `v0.1.4`, and
   wait for `.github/workflows/publish-image.yml` to pass. The workflow rejects
   a tag that differs from `pyproject.toml` and runs all unit/integration tests.
2. Copy the published linux/amd64 `sha256` manifest digest from GHCR. In a
   promotion commit, set this exact reference in both
   `deploy/okd/application.yaml` and `deploy/okd/migration.yaml`:

```text
ghcr.io/transience-mic3/mic3-api:0.1.4@sha256:<published-64-character-digest>
```

Before merging the promotion, confirm the placeholder is gone and both files
contain the same immutable image reference:

```powershell
rg "REPLACE_WITH_V0_1_4_DIGEST" deploy/okd
rg "ghcr.io/transience-mic3/mic3-api" deploy/okd/application.yaml deploy/okd/migration.yaml
python -m pytest tests/unit/infrastructure/test_okd_authentication_manifests.py
```

The first command must produce no output. Do not apply the migration or API
manifest from the source commit while its digest marker remains.

### 6. Run the database migration

Run the versioned one-shot Job before changing the API Deployment:

```powershell
oc apply -f deploy/okd/migration.yaml
oc wait --for=condition=complete `
  job/mic3-api-migrate-0-1-4 `
  --timeout=180s
oc logs job/mic3-api-migrate-0-1-4
```

The Job receives only MIC3 database settings and runs
`python -m alembic upgrade head`. If it fails, stop, inspect its logs, and do
not deploy the API or downgrade the database.

### 7. Deploy and verify the API

```powershell
oc apply -f deploy/okd/application.yaml
oc rollout status deployment/mic3-api --timeout=180s
oc logs deployment/mic3-api --tail=100

$apiHost = oc get route mic3-api -o jsonpath='{.spec.host}'
Invoke-RestMethod "https://$apiHost/health"
Invoke-RestMethod "https://$apiHost/ready"
```

Obtain a fresh Postman access token, then exercise discovery and the real
authenticated API path from the repository root:

```powershell
$keycloakHost = oc get route mic3-keycloak -o jsonpath='{.spec.host}'
$oidcIssuer = "https://$keycloakHost/realms/mic3"
$env:API_BASE_URL = "https://$apiHost"
$env:OIDC_ISSUER_URL = $oidcIssuer
$env:OIDC_ACCESS_TOKEN = Read-Host "Paste the access token"

python -m pytest `
  tests/smoke/test_oidc.py `
  tests/smoke/test_authenticated_user.py

Remove-Item Env:OIDC_ACCESS_TOKEN
```

Confirm MIC3 PostgreSQL now contains one application user, one external
identity, and one `member` assignment. Keycloak remains the credential owner;
MIC3 stores neither its password nor its token.

If only the API rollout fails, use `oc rollout undo deployment/mic3-api` and
leave the additive schema migration in place. Never recover by deleting either
PostgreSQL PVC.
