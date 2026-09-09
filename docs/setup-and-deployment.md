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

The EOSC realm enables registration with verified email, password recovery,
a 15-character password minimum, and temporary brute-force lockout. SMTP is
loaded from the `mic3-keycloak-smtp` Secret by the reconciliation Job. Local
Compose remains independent of SMTP. Registration/email request throttling and
bot protection still need assessment before public launch.

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

The configuration Job now authenticates with a service account in the existing
`mic3` realm, not the temporary master administrator. Before running it, ensure:

- The `mic3` realm exists. On a fresh installation, start Keycloak with the
  deployment commands below, then create the realm in the Admin Console first.
- In `mic3`, create the confidential client `mic3-realm-configurator`, enable
  service accounts, disable browser/direct-grant flows, and assign its service
  account the `realm-management` client role `realm-admin`. Keep this operational
  client outside the realm JSON; remote-state management preserves unmanaged
  clients. Normal users must never receive this role.
- Create the Opaque Secret `mic3-keycloak-reconciler` with key `client-secret`
  containing that client's credential.
- Create the Opaque Secret `mic3-keycloak-smtp` with keys `MIC3_SMTP_HOST`,
  `MIC3_SMTP_PORT`, `MIC3_SMTP_FROM`, `MIC3_SMTP_FROM_NAME`, `MIC3_SMTP_USER`, and
  `MIC3_SMTP_PASSWORD`. Use the Brevo SMTP login and SMTP key. The example `.env`
  is for local development and does not supply these EOSC values.

The Job uses `http://mic3-keycloak:8080`, the internal Service address. It does
not change Keycloak's configured public hostname or the API's trusted issuer.
It skips the master-only server-info endpoint, supplies the pinned server
version, and disables the import cache so each explicit run reconciles settings.
No image build is needed. Server-side dry-run does not verify client credentials
or Secret existence; those are checked when the Job actually runs.

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
internal Keycloak Service, and then reconciles the pre-created `mic3` realm
through the Admin API. A non-zero Job result is a deployment blocker: inspect
its logs and do not proceed to the API release. Completed configuration Jobs
are automatically removed after one day.

Log into the Admin Console with your permanent master administrator and verify
the `mic3` realm settings. Test registration and email verification, password
recovery, and authenticated `/users/me` through the existing Postman PKCE client.
Keycloak administrator privileges are separate from MIC3 application roles.
Keep the temporary administrator until permanent login and service-account
reconciliation both work. The deployment still references the bootstrap Secret;
remove those deployment references before retiring that Secret.

Realm settings, the `mic3-api` and `mic3-postman` clients, client scopes, and
protocol mappers declared in `mic3-realm.json` are authoritative. Users, roles,
and groups are deliberately omitted, so reconciliation does not manage or
delete them. Manual Admin Console changes to managed resources can be restored
on the next run. After experimenting, record accepted changes in the JSON,
review them, update only the realm ConfigMap, and run a new generated
configuration Job. For an existing deployment, use:

```powershell
oc create configmap mic3-keycloak-realm --from-file=mic3-realm.json=deploy/okd/keycloak/mic3-realm.json --dry-run=client -o yaml | oc apply --dry-run=server -f -
oc create configmap mic3-keycloak-realm --from-file=mic3-realm.json=deploy/okd/keycloak/mic3-realm.json --dry-run=client -o yaml | oc apply -f -
$configJob = oc create -f deploy/okd/keycloak/configure.yaml -o name
oc wait --for=condition=complete $configJob --timeout=300s
oc logs $configJob
```

Stop at a failure. Repeat the Job and functional checks to verify reconciliation
preserves users, policy, and mail delivery. Do not delete the
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

### 8. Validate custom-domain certificates with Let's Encrypt staging

This is an optional, separately applied certificate experiment. It requires
cert-manager already installed on the cluster and permission to create namespaced
Issuers, Certificates, and Ingresses. It does not require a new API image.
The existing application manifests do not include this template.

Let's Encrypt **staging** is a test certificate authority, not another EOSC
namespace or the Git branch. Its certificates are not trusted by browsers.
Never attach these test Secrets to the API or Keycloak Routes. Production
issuance and automatic delivery of renewed certificates to Routes are a later
step with separate resource and Secret names; do not change this Issuer's server
to the production endpoint.

The template requests one certificate per hostname using HTTP-01. The hostnames
must already resolve to the cluster's public ingress, which must serve the
temporary validation path on port 80. cert-manager creates temporary solver
Pods, ClusterIP Services, and Ingresses. No ingress class is specified: this
experiment establishes whether the platform's default handling admits and
serves them. An Issuer becoming Ready only confirms ACME account registration;
both Certificates must become Ready to prove domain validation.

#### Select the project and render in memory

Run from the repository root in PowerShell after `oc login`. Enter bare DNS
hostnames, without `https://` or paths. The contact email is for the ACME account,
not an SMTP login. All three parameters are required.

```powershell
oc project -q
$certificateProject = Read-Host "Confirm the intended EOSC project name"
if ($certificateProject -ne (oc project -q)) {
  throw "Selected project does not match; select the intended project first"
}
$certificateEmail = Read-Host "ACME contact email"
$certificateApiHost = Read-Host "API custom hostname"
$certificateAuthHost = Read-Host "Authentication custom hostname"

$certificateManifest = oc process --local `
  -f deploy/okd/certificates/staging-template.yaml `
  -p "ACME_EMAIL=$certificateEmail" `
  -p "API_HOST=$certificateApiHost" `
  -p "AUTH_HOST=$certificateAuthHost" -o json
if ($LASTEXITCODE -ne 0) { throw "Certificate template rendering failed" }
$certificateManifest
```

Inspect the three resources and their hostnames. Keep the rendered configuration
in memory; do not commit environment-specific manifests or Secret exports.
Namespace selection is explicit on every cluster command below.

#### Validate, then apply manually

First ask the API server to validate without saving resources:

```powershell
$certificateManifest | oc -n $certificateProject apply --dry-run=server -f -
if ($LASTEXITCODE -ne 0) { throw "Certificate dry-run failed; do not apply" }
```

Dry-run does not prove the cert-manager controller can solve challenges. When
ready to start the experiment, apply exactly the reviewed resources:

```powershell
$certificateManifest | oc -n $certificateProject apply -f -
if ($LASTEXITCODE -ne 0) { throw "Certificate apply failed; inspect before retrying" }
oc -n $certificateProject get issuer mic3-letsencrypt-staging
oc -n $certificateProject get certificate mic3-api-staging mic3-auth-staging
```

#### Inspect issuance and failures

Use these read-only checks; issuance can take several minutes. If pending,
inspect the reported reason rather than repeatedly creating new requests.

```powershell
oc -n $certificateProject describe issuer mic3-letsencrypt-staging
oc -n $certificateProject describe certificate mic3-api-staging mic3-auth-staging
oc -n $certificateProject get certificaterequests,orders.acme.cert-manager.io,challenges.acme.cert-manager.io
oc -n $certificateProject get pods,services,ingresses -l acme.cert-manager.io/http01-solver=true
oc -n $certificateProject get routes
oc -n $certificateProject get events --sort-by=.metadata.creationTimestamp
```

For a failed resource, use `oc -n $certificateProject describe <kind> <name>`
with the exact name from the listing. Follow owner references from the
CertificateRequest to its Certificate and from Orders/Challenges to the request
before attributing failures. Check pod scheduling/resource limits, solver logs,
generated Ingress/Route admission, and external HTTP challenge reachability.
Do not install controllers, broaden permissions, or switch validation methods
without reviewing a revised plan. Stop if existing application health regresses.

#### Verify the certificate without exposing private keys

Both Certificates must report `Ready=True`. Inspect status and only the public
`tls.crt` field of each Secret (never print the whole Secret or `tls.key`):

```powershell
oc -n $certificateProject get certificate mic3-api-staging mic3-auth-staging `
  -o custom-columns='NAME:.metadata.name,DNS:.spec.dnsNames,READY:.status.conditions[?(@.type=="Ready")].status,NOT_BEFORE:.status.notBefore,NOT_AFTER:.status.notAfter,RENEWAL:.status.renewalTime'

foreach ($certificateSecret in @('mic3-api-staging-tls', 'mic3-auth-staging-tls')) {
  $certificateBase64 = oc -n $certificateProject get secret $certificateSecret `
    -o jsonpath='{.data.tls\.crt}'
  if ($LASTEXITCODE -ne 0) { throw "Cannot read public certificate" }
  $certificatePem = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String($certificateBase64)
  )
  $certificateMatch = [regex]::Match(
    $certificatePem, '(?s)-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----'
  )
  if (-not $certificateMatch.Success) { throw "Missing PEM certificate" }
  $certificateDer = [Convert]::FromBase64String($certificateMatch.Groups[1].Value)
  $publicCertificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($certificateDer)
  try {
    $publicCertificate | Select-Object Subject, Issuer, NotBefore, NotAfter, Thumbprint
    $publicCertificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } |
      ForEach-Object { $_.Format($true) }
  } finally { $publicCertificate.Dispose() }
}
```

Confirm each public certificate's subject alternative name matches its requested
hostname, its validity contains the current time, and its issuer is a Let's
Encrypt staging issuer. `renewalTime` demonstrates a scheduled renewal, not an
observed successful renewal. These Secrets are not yet connected to any Route.

Check the original application endpoints with normal HTTPS verification:

```powershell
$originalApiHost = oc -n $certificateProject get route mic3-api -o jsonpath='{.spec.host}'
$originalAuthHost = oc -n $certificateProject get route mic3-keycloak -o jsonpath='{.spec.host}'
Invoke-RestMethod "https://$originalApiHost/health"
Invoke-RestMethod "https://$originalApiHost/ready"
$originalDiscovery = Invoke-RestMethod "https://$originalAuthHost/realms/mic3/.well-known/openid-configuration"
if ($originalDiscovery.issuer -ne "https://$originalAuthHost/realms/mic3") {
  throw "Unexpected change to the original OIDC issuer"
}
```

Record the results before planning production issuance. This experiment does not
change Keycloak's hostname, the API's trusted issuer, or application user mappings.

#### Retire the experiment

When no longer needed, remove only these test resources. Deleting Certificates
allows their owned requests and challenges to be garbage-collected; certificate
Secrets may remain depending on controller settings, so they are named explicitly.
Do not use a namespace-wide or broad label-based delete.

```powershell
oc -n $certificateProject delete certificate mic3-api-staging mic3-auth-staging --ignore-not-found
if ($LASTEXITCODE -ne 0) { throw "Certificate cleanup failed" }
oc -n $certificateProject delete issuer mic3-letsencrypt-staging --ignore-not-found
if ($LASTEXITCODE -ne 0) { throw "Issuer cleanup failed" }
oc -n $certificateProject delete secret mic3-api-staging-tls mic3-auth-staging-tls mic3-letsencrypt-staging-account --ignore-not-found
```

If solver resources remain, inspect their owner references and deletion events
before acting. Keep production resources, existing Routes, and database PVCs intact.

### 9. Request production certificates

Use this after both staging certificates have been issued and inspected. Keep
the staging template in Git as a repeatable infrastructure test; its live
resources can be retired using the cleanup above after production succeeds.
The production template is independent and does not modify staging resources.

1. In the same PowerShell window, confirm `$certificateProject`,
   `$certificateEmail`, `$certificateApiHost`, and `$certificateAuthHost` still
   contain the intended values. If starting a new window, use the project and
   parameter prompts from section 8 first. Confirm `oc project -q` matches
   `$certificateProject`.
2. Render the production configuration in memory:

```powershell
$productionCertificateManifest = oc process --local -f deploy/okd/certificates/production-template.yaml -p "ACME_EMAIL=$certificateEmail" -p "API_HOST=$certificateApiHost" -p "AUTH_HOST=$certificateAuthHost" -o json
if ($LASTEXITCODE -ne 0) { throw "Production rendering failed" }
$productionCertificateManifest
```

3. Validate without saving anything:

```powershell
$productionCertificateManifest | oc -n $certificateProject apply --dry-run=server -f -
if ($LASTEXITCODE -ne 0) { throw "Production dry-run failed; do not apply" }
```

4. Apply only after reviewing the hostnames and the production ACME endpoint:

```powershell
$productionCertificateManifest | oc -n $certificateProject apply -f -
if ($LASTEXITCODE -ne 0) { throw "Production apply failed" }
oc -n $certificateProject get issuer mic3-letsencrypt-production
oc -n $certificateProject get certificate mic3-api-production mic3-auth-production
```

There are **no Secrets to create manually**. cert-manager generates:

| Secret | Contents |
| --- | --- |
| `mic3-letsencrypt-production-account` | Production ACME account private key |
| `mic3-api-production-tls` | API certificate chain and private key |
| `mic3-auth-production-tls` | Authentication certificate chain and private key |

Do not copy staging keys or certificates into these Secrets. Keep generated
Secrets out of Git. No DNS-provider credentials or SMTP credentials are involved.

5. Require the Issuer and both Certificates to report `Ready=True`. Use the
   section 8 diagnostics if pending, replacing the three resource names with
   their production names. Do not repeatedly delete/recreate production requests
   to troubleshoot; production issuance is subject to CA rate limits.
6. Repeat the public-certificate inspection from section 8 with Secret names
   `mic3-api-production-tls` and `mic3-auth-production-tls`. Confirm the requested
   DNS names, current validity, and a production rather than staging issuer.
   Inspect `status.renewalTime` on each production Certificate. Recheck the
   original health, readiness, and OIDC discovery endpoints.

Issuance alone does not enable HTTPS at the custom hostnames. A separate change
must connect these Secrets to the public Routes and ensure renewed certificates
are served automatically, then coordinate the Keycloak/API issuer transition.
No application Route, Deployment, or OIDC configuration is changed by this
template. Do not retire the production Issuer or Certificates after issuance:
cert-manager needs them to manage renewal.

### 10. Connect the custom hostnames to production certificates

Apply this only after both production Certificates are Ready. The separate
`deploy/okd/custom-domains-template.yaml` creates two standard Ingresses. OKD
creates owned Routes from them, using the referenced TLS Secrets. Its
Ingress-to-Route controller watches Secret updates and updates the generated
Routes when cert-manager renews certificates. No extra controller or router
RBAC is installed. Keep generated Routes and their certificate/key contents out
of Git; edit the parent Ingress configuration rather than its generated Routes.

The existing cloud-hostname Routes remain available and continue to provide the
DNS alias targets. This step does not restart applications or change OIDC settings.

Use the same project and hostname variables from the certificate steps:

```powershell
if ([string]::IsNullOrWhiteSpace($certificateProject) -or $certificateProject -ne (oc project -q)) { throw "Confirm the intended project first" }
$customDomainManifest = oc process --local -f deploy/okd/custom-domains-template.yaml -p "API_HOST=$certificateApiHost" -p "AUTH_HOST=$certificateAuthHost" -o json
if ($LASTEXITCODE -ne 0) { throw "Custom domain rendering failed" }
$customDomainManifest
$customDomainManifest | oc -n $certificateProject apply --dry-run=server -f -
if ($LASTEXITCODE -ne 0) { throw "Custom domain validation failed" }
```

After reviewing the render and successful dry-run, apply manually:

```powershell
$customDomainManifest | oc -n $certificateProject apply -f -
if ($LASTEXITCODE -ne 0) { throw "Custom domain apply failed" }
oc -n $certificateProject get ingress mic3-api-custom mic3-auth-custom
oc -n $certificateProject get routes
Invoke-RestMethod "https://$certificateApiHost/health"
Invoke-RestMethod "https://$certificateApiHost/ready"
$customDiscovery = Invoke-RestMethod "https://$certificateAuthHost/realms/mic3/.well-known/openid-configuration"
$customDiscovery.issuer
```

Require HTTPS validation to succeed without bypass flags. Check the generated
Routes are Admitted, use edge TLS, and redirect insecure traffic (the controller's
default for newly generated TLS Routes). Verify each served public certificate
matches the corresponding production Secret's public certificate, and recheck
the original cloud endpoints. Do not print whole generated Route YAML: it can
contain private keys copied by the controller.

Keycloak discovery will still advertise the original cloud issuer at this stage;
login pages may link or redirect there. The next step is a coordinated hostname
and trusted-issuer change with explicit handling of existing MIC3 issuer/subject
mappings. Neither frontend CORS nor email configuration is part of this template.

Secret update propagation uses OKD's existing controller, but an observed
production renewal remains a later check. Do not force repeated production
issuance just to test it. After renewal, compare the served public certificate
with the renewed Secret and confirm HTTPS remains valid.

To roll back only this routing step, delete the two parent Ingresses; their
owned Routes are garbage-collected. Preserve the original Routes and production
Certificates/Secrets:

```powershell
oc -n $certificateProject delete ingress mic3-api-custom mic3-auth-custom --ignore-not-found
```
