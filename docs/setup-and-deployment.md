# Setup and deployment

Use this runbook for procedures; [local project status](../PROJECT_STATUS.md)
(Git-ignored) records acceptance and unverified work. Run commands from the
repository root. Examples use PowerShell and stop-at-failure operation.

## Local development

Use Python 3.13 in an activated Conda/venv environment and Docker with Linux
containers. Copy `.env.example` to `.env` if absent; for an existing file, add
missing `OIDC_*` and `KEYCLOAK_*` settings. Set distinct local-only passwords
for MIC3 PostgreSQL, Keycloak PostgreSQL, and the Keycloak administrator.
Do not commit `.env` or use EOSC credentials locally.

```powershell
python -m pip install -r requirements-dev.txt
docker compose up -d --wait postgres keycloak
docker compose run --rm keycloak-config
python -m alembic upgrade head
python -m uvicorn mic3_api.main:create_app --factory --reload
```

The API runs on the host at [localhost:8000/docs](http://localhost:8000/docs).
MIC3 PostgreSQL defaults to `127.0.0.1:5433`, database `mic3`, user `mic3_api`.
Keycloak has its own database/user/volume. Preserve both named volumes and their
credentials; do not delete them to refresh configuration. Alembic runs explicitly,
not at API startup.

Keycloak's [Admin Console](http://localhost:8080/admin/) uses the local
`KEYCLOAK_ADMIN_USERNAME` / `KEYCLOAK_ADMIN_PASSWORD`. Its infrastructure
administrator is not a MIC3 application administrator. Local registration needs
no SMTP or email verification. Check readiness with
`Invoke-RestMethod http://localhost:9000/health/ready`.

Local realm settings, clients, scopes, and mappers in
`deploy/local/keycloak/config/mic3-realm.json` are authoritative; users, roles,
and groups remain operational data outside reconciliation. After accepted JSON
changes, rerun `docker compose run --rm keycloak-config` and require success.
Manual edits to managed resources may be restored on reconciliation.

### Tests

Without running services:

```powershell
python -m pytest tests/unit tests/integration/api/test_health.py tests/integration/api/test_readiness.py
```

With Docker running:

```powershell
python -m pytest tests/unit tests/integration
```

Testcontainers uses disposable PostgreSQL and Keycloak instances, including a
real realm-reconciler compatibility check; it does not use Compose/EOSC databases.

## Login and smoke checks

Choose the API URL and **exact configured issuer**, not an issuer inferred from
an old Route. For local defaults:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
$env:OIDC_ISSUER_URL = "http://localhost:8080/realms/mic3"
```

For EOSC, enter the current public configuration:

```powershell
$env:API_BASE_URL = Read-Host "Active public HTTPS API URL"
$env:OIDC_ISSUER_URL = Read-Host "Active HTTPS OIDC issuer URL"
```

In Postman choose **OAuth 2.0 → Authorization Code (With PKCE)**:

| Setting | Value |
| --- | --- |
| Callback URL | `https://oauth.pstmn.io/v1/browser-callback` |
| Auth URL | `<issuer>/protocol/openid-connect/auth` |
| Access Token URL | `<issuer>/protocol/openid-connect/token` |
| Client ID | `mic3-local` locally; `mic3-postman` on EOSC |
| Client Secret / authentication | Empty / no secret |
| Scope | `openid profile email` |
| Code Challenge Method | `SHA-256` |

Replace `<issuer>` with the selected issuer. Obtain an **access token** and use
it as a bearer token on `GET /users/me`. It must have a non-empty `sub`, the
exact `iss`, and an `aud` containing `mic3-api`. First use provisions a MIC3
member; repeated use resolves the same internal user. The API is a resource
server, not a login callback, and needs no Keycloak client secret. A future
frontend will need its own public client/callback and origin-specific CORS.

```powershell
python -m pytest tests/smoke/test_health.py tests/smoke/test_oidc.py
$env:OIDC_ACCESS_TOKEN = Read-Host "Temporary Postman access token"
try {
  python -m pytest tests/smoke/test_authenticated_user.py
} finally {
  Remove-Item Env:OIDC_ACCESS_TOKEN
}
```

Smoke-test variables must be set in the process/IDE; `API_BASE_URL` is not read
from `.env`. Never save user tokens in Git, profiles, Postman exports, or Actions.
Readiness proves database connectivity, not schema compatibility or login success.

## MIC3 administrator authorization

MIC3 administrator access is a local database role, independent of Keycloak
administrator accounts and token role claims. Login provisions only `member`.
The operator command requires access to the application runtime and MIC3 database
credentials; it needs neither a running API nor OIDC settings. It is not an HTTP
endpoint. Use the existing `DB_*` environment configuration (or local `.env`).

After `python -m alembic upgrade head`, log in as the intended ordinary MIC3 user
and obtain its internal UUID from `GET /users/me`. Confirm the target environment
and UUID, then run:

```powershell
python -m mic3_api.cli grant-admin --user-id <MIC3_UUID>
```

The command adds `admin` without removing other roles. Repeating it is safe.
It rejects unknown or inactive users and missing role configuration. Exit codes
are `0` for granted/already present, `2` for invalid arguments, and `1` for
application/database failures. No username/email lookup or implicit account
creation occurs. Operator database credentials must be kept private.

`GET /users?limit=50&offset=0` returns `{items, limit, offset}`. Items contain
`id`, nullable `email`, nullable `display_name`, `is_active`, and sorted `roles`.
The directory includes inactive accounts, orders by UUID, and allows limits
1-100 with a nonnegative offset. Active MIC3 admins receive `200`; ordinary or
inactive users receive `403`; missing/invalid tokens receive `401`. Promotion
is visible on the next request with any still-valid access token.

For local acceptance, verify the intended member receives `403` before granting,
run the command, then verify `/users/me` includes `admin` and `/users` returns
`200`. Keep a separate member for the `403` check. Run the read-only smoke test
with temporary process environment values:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
$env:OIDC_ADMIN_ACCESS_TOKEN = Read-Host "Temporary admin access token"
$env:OIDC_MEMBER_ACCESS_TOKEN = Read-Host "Temporary ordinary-member access token"
try {
  python -m pytest tests/smoke/test_admin_authorization.py
} finally {
  Remove-Item Env:OIDC_ADMIN_ACCESS_TOKEN, Env:OIDC_MEMBER_ACCESS_TOKEN
}
```

For EOSC, first deploy the version containing this command and migration using
the normal release procedure. Obtain the intended MIC3 user's UUID from the live
`/users/me`, confirm the selected project and deployment, and run the same command
inside the deployed API container using its existing database environment:

```powershell
oc project -q
oc exec deployment/mic3-api -c api -- python -m mic3_api.cli grant-admin --user-id <MIC3_UUID>
```

Run the smoke checks with the live API URL and separate live admin/member tokens.
Local acceptance does not establish live acceptance. Downgrading the admin-role
migration removes admin assignments and the admin role, while preserving users
and their other roles.

## EOSC routine operations

The integration deployment has separate MIC3/Keycloak PostgreSQL Services,
credentials, and PVCs. PostgreSQL and Keycloak's management port are internal.
Original cloud-hostname Routes provide edge TLS; custom domains use
certificate-backed Ingresses and controller-managed Routes.

Log in with the supplied `oc login` command and confirm the selected project.
Inspect current quotas before sizing workloads; ceilings do not guarantee capacity:

```powershell
oc project -q
oc get resourcequota
```

Never delete database PVCs as recovery. API releases are automated;
database, Keycloak, networking, certificates, and RBAC remain separate operations.

### Release the API

1. Set the next unused stable version in `pyproject.toml`, commit reviewed work,
   push to `staging`, and merge into `master`.
2. Update local `master`, confirm its version is the intended new release,
   then tag and push it:

   ```powershell
   git switch master
   if ($LASTEXITCODE -ne 0) { throw "Cannot switch to master" }
   git pull --ff-only origin master
   if ($LASTEXITCODE -ne 0) { throw "Cannot update master" }
   $releaseVersion = python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
   if ($LASTEXITCODE -ne 0) { throw "Cannot read release version" }
   git tag "v$releaseVersion"
   if ($LASTEXITCODE -ne 0) { throw "Tag creation failed; do not move an existing tag" }
   git push origin "v$releaseVersion"
   ```

3. Watch **Actions → Release and deploy API**: tests, image publication,
   immutable `release.json`, migration Job, API rollout, and public checks.
   Use [login and smoke checks](#login-and-smoke-checks) for authenticated acceptance.

The `eosc-development` environment supplies `OPENSHIFT_TOKEN`, optional
`OPENSHIFT_CA_PEM`, and variables `OPENSHIFT_SERVER`, `OPENSHIFT_NAMESPACE`,
`API_BASE_URL`, and `OIDC_ISSUER_URL`. Runtime Secrets remain in EOSC. Routine
releases need no manual digest edits, token creation, or `oc apply`.

### Retry or recover a release

For a resolved failure, use **Actions → Release and deploy API → Run workflow**,
select `master`, and enter the existing application `release_tag`. If its release
record exists, the workflow reuses the recorded image without rebuilding. If no
release or image exists yet, it tests and publishes the tagged application source
before deploying. A partial publication requires recovery as described below.

The workflow and deployment templates come from the selected workflow commit,
fixed for that run; application and migration code come from the release tag/image.
After correcting workflow or template code on `master`, start a **fresh manual
run** for the same tag, rather than rerunning the old failed run. No version bump
is needed for deployment-only fixes. Tag pushes still use the workflow at the tag.
The Actions summary identifies application and deployment-workflow commits
separately. Keep current templates compatible with any application being retried.

Every deployment creates a fresh migration Job. `alembic upgrade head` is a
no-op when that image's migrations are already applied. An unfinished previous
migration must terminate before another starts. There is no automatic whole-run
retry, image rollback, or database downgrade.

If application or migration code needs fixing, publish the next patch version.
Never move an existing release tag or overwrite its image. A failed migration
blocks API apply; inspect the named Job's logs before retrying. A successful
migration remains applied even if rollout or smoke checks fail. Migrations must
remain compatible with the currently running API. Delete only an exact reviewed
migration Job when intervention is necessary, never all namespace Jobs.

If publication stopped after uploading the image but before saving `release.json`,
recover the record from the successful build output and verified source/digest,
or use a new version. The record contains `format_version: 1`, `release_tag`,
`commit`, `image_repository`, and `image_ref` (repository:version@sha256:digest).
Attach it to that tag's GitHub Release; do not replace an existing valid record.

Older releases are blocked in normal automation. For deliberate recovery, stop
release activity, verify that the previous recorded image supports the current
schema, and restore only the API image with `oc set image deployment/mic3-api
api=<recorded-image-ref>`. Wait with `oc rollout status deployment/mic3-api
--timeout=300s`, verify endpoints and authenticated login, then record the restored
version, commit, and image in the Deployment's `delivery.mic3.io/version`,
`delivery.mic3.io/commit`, and `delivery.mic3.io/image` annotations and version
label. Do not execute the older image's migrations or delete database PVCs.


### Reconcile Keycloak

EOSC enables verified-email registration, password recovery, a 15-character
password minimum, and temporary brute-force lockout. SMTP comes from a Secret;
local Compose needs none. Abuse controls still require assessment before launch.

Ensure the [reconciler prerequisites](#keycloak-provisioning) exist. Realm settings,
`mic3-api` / `mic3-postman` clients, scopes, and mappers in
`deploy/okd/keycloak/mic3-realm.json` are authoritative; users, roles, and groups
are not managed. Record accepted Admin Console changes in JSON before reconciling.

For an existing deployment, validate and update only the realm ConfigMap:

```powershell
oc create configmap mic3-keycloak-realm --from-file=mic3-realm.json=deploy/okd/keycloak/mic3-realm.json --dry-run=client -o yaml | oc apply --dry-run=server -f -
if ($LASTEXITCODE -ne 0) { throw "Realm validation failed" }
oc create configmap mic3-keycloak-realm --from-file=mic3-realm.json=deploy/okd/keycloak/mic3-realm.json --dry-run=client -o yaml | oc apply -f -
if ($LASTEXITCODE -ne 0) { throw "Realm update failed" }
$configJob = oc create -f deploy/okd/keycloak/configure.yaml -o name
if ($LASTEXITCODE -ne 0) { throw "Configuration Job creation failed" }
oc wait --for=condition=complete $configJob --timeout=300s
$configWaitResult = $LASTEXITCODE
oc logs $configJob
if ($configWaitResult -ne 0) { throw "Inspect the failed or unfinished configuration Job" }
```

The Job connects to the internal Service using the realm service account, skips
the master-only server-info endpoint, and disables import caching. It does not
change public hostname/issuer configuration. Completed configuration Jobs have
a one-day TTL. Repeat reconciliation and verify preserved users, registration,
verification mail, password recovery, and login before considering it accepted.

Verify permanent master-administrator access and service-account reconciliation
before retiring the temporary administrator. Remove bootstrap references from
the Deployment before retiring its Secret. Never delete the realm/PVC to reconcile.

## EOSC provisioning

These are one-time infrastructure procedures, not steps to repeat for each release.
Stop on failure; inspect existing resources rather than silently overwriting them.

### Existing API prerequisites

Retain `mic3-postgres-credentials` (database, username, password),
`mic3-postgres-data`, `ghcr-pull` with `read:packages`, and `mic3-oidc-config`
(issuer-url, audience). Infrastructure is described by `deploy/okd/postgres.yaml`,
`networking.yaml`, and `deployer-template.yaml` (required `NAMESPACE` parameter).
The latter separates deployer, API runtime, and migration service accounts.
Credential issuance and GitHub environment setup are external operator tasks.

Preflight the relevant resources; release templates are validated by Actions:

```powershell
oc apply --dry-run=server -f deploy/okd/keycloak/prerequisites.yaml
oc apply --dry-run=server -k deploy/okd/keycloak
oc create --dry-run=server -f deploy/okd/keycloak/configure.yaml -o yaml | Out-Null
oc apply --dry-run=server -f deploy/okd/networking.yaml
```

Dry-run does not establish that referenced Secrets exist or credentials work.

### Keycloak provisioning

Create credentials only for a fresh installation. Save distinct generated
passwords in a password manager; the database password must remain consistent
with its persisted database. Existing Secrets must not be implicitly rotated.

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

Create Keycloak's database, Services, and initial cloud-hostname Route:

```powershell
oc apply -f deploy/okd/keycloak/prerequisites.yaml
oc rollout status statefulset/mic3-keycloak-postgres --timeout=180s
$keycloakHost = oc get route mic3-keycloak -o jsonpath='{.spec.host}'
$keycloakUrl = "https://$keycloakHost"
$oidcIssuer = "$keycloakUrl/realms/mic3"
oc create configmap mic3-keycloak-runtime --from-literal=hostname="$keycloakUrl"
oc create secret generic mic3-oidc-config --from-literal=issuer-url="$oidcIssuer" --from-literal=audience="mic3-api"
oc apply -k deploy/okd/keycloak
oc rollout status deployment/mic3-keycloak --timeout=300s
oc logs deployment/mic3-keycloak --tail=150
```

The cloud issuer above is for **initial provisioning only**. Do not replace an
existing custom-domain issuer. Changing an issuer requires coordinated Keycloak,
API, client/Actions configuration and handling of existing issuer/subject mappings.

Before the first EOSC configuration Job:

- Create the `mic3` realm in the Admin Console if absent.
- Create confidential client `mic3-realm-configurator` in that realm, enable
  service accounts, disable browser/direct-grant flows, and assign its service
  account `realm-management` → `realm-admin`. Keep this operational client
  outside declarative JSON; normal users must not receive the role.
- Create Opaque Secret `mic3-keycloak-reconciler`, key `client-secret`.
- Create Opaque Secret `mic3-keycloak-smtp` with `MIC3_SMTP_HOST`,
  `MIC3_SMTP_PORT`, `MIC3_SMTP_FROM`, `MIC3_SMTP_FROM_NAME`, `MIC3_SMTP_USER`,
  and `MIC3_SMTP_PASSWORD`, using the Brevo SMTP login/key.

Run [reconciliation](#reconcile-keycloak), then the
[login/smoke checks](#login-and-smoke-checks). A failed configuration Job blocks
acceptance; inspect logs before proceeding to API deployment.

### Certificates and custom domains

Requires cert-manager and permission for namespaced Issuers, Certificates, and
Ingresses. DNS must resolve to public ingress and HTTP-01 must be reachable on
port 80. No new API image, DNS-provider credentials, or manual TLS Secret seeding
is needed.

Use the same procedure for either environment, with `staging` first when
validating an unfamiliar setup. Staging is an untrusted test CA, not a namespace
or Git branch; never attach its certificates to live Routes. Production has
separate resources and is required for browser-trusted custom HTTPS.

```powershell
$certificateProject = Read-Host "Confirm intended EOSC project"
if ($certificateProject -ne (oc project -q)) { throw "Select the intended project first" }
$certificateEmail = Read-Host "ACME contact email"
$certificateApiHost = Read-Host "API hostname without scheme/path"
$certificateAuthHost = Read-Host "Authentication hostname without scheme/path"
$certificateEnvironment = Read-Host "Certificate environment: staging or production"
if ($certificateEnvironment -notin @('staging', 'production')) { throw "Invalid certificate environment" }
$certificateManifest = oc process --local -f "deploy/okd/certificates/$certificateEnvironment-template.yaml" -p "ACME_EMAIL=$certificateEmail" -p "API_HOST=$certificateApiHost" -p "AUTH_HOST=$certificateAuthHost" -o json
if ($LASTEXITCODE -ne 0) { throw "Certificate rendering failed" }
$certificateManifest
$certificateManifest | oc -n $certificateProject apply --dry-run=server -f -
if ($LASTEXITCODE -ne 0) { throw "Certificate validation failed" }
```

Review hostnames and ACME endpoint before applying the exact rendered resources:

```powershell
$certificateManifest | oc -n $certificateProject apply -f -
if ($LASTEXITCODE -ne 0) { throw "Certificate apply failed" }
oc -n $certificateProject get issuer "mic3-letsencrypt-$certificateEnvironment"
oc -n $certificateProject get certificate "mic3-api-$certificateEnvironment" "mic3-auth-$certificateEnvironment"
```

cert-manager creates the ACME account Secret
`mic3-letsencrypt-<environment>-account` and TLS Secrets
`mic3-api-<environment>-tls` / `mic3-auth-<environment>-tls`. Keep them out of
Git; never copy staging keys to production. Keep production Issuer/Certificates
for renewal.

For pending issuance, inspect the exact resource and its owner references:

```powershell
oc -n $certificateProject describe issuer "mic3-letsencrypt-$certificateEnvironment"
oc -n $certificateProject describe certificate "mic3-api-$certificateEnvironment" "mic3-auth-$certificateEnvironment"
oc -n $certificateProject get certificaterequests,orders.acme.cert-manager.io,challenges.acme.cert-manager.io
oc -n $certificateProject get pods,services,ingresses -l acme.cert-manager.io/http01-solver=true
oc -n $certificateProject get routes
oc -n $certificateProject get events --sort-by=.metadata.creationTimestamp
```

Check solver scheduling/logs, Route admission, and public HTTP challenge access.
Issuer readiness alone proves only account registration; require both
Certificates Ready. Do not repeatedly recreate production requests or change
controllers/RBAC to bypass a failure.

Inspect validity/renewal status and **only public certificates**, never whole
Secrets or `tls.key`:

```powershell
oc -n $certificateProject get certificate "mic3-api-$certificateEnvironment" "mic3-auth-$certificateEnvironment" `
  -o custom-columns='NAME:.metadata.name,DNS:.spec.dnsNames,READY:.status.conditions[?(@.type=="Ready")].status,NOT_BEFORE:.status.notBefore,NOT_AFTER:.status.notAfter,RENEWAL:.status.renewalTime'

foreach ($certificateSecret in @("mic3-api-$certificateEnvironment-tls", "mic3-auth-$certificateEnvironment-tls")) {
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

Check requested DNS names, validity, and the selected CA issuer. A scheduled
`renewalTime` is not proof of successful renewal.

#### Connect production certificates

After both production Certificates are Ready, reuse the confirmed project/hostname
variables above. The template creates parent Ingresses; OKD manages their Routes
and propagates TLS Secret changes. Edit parents, not generated Routes.

```powershell
if ([string]::IsNullOrWhiteSpace($certificateProject) -or $certificateProject -ne (oc project -q)) { throw "Confirm the intended project first" }
$customDomainManifest = oc process --local -f deploy/okd/custom-domains-template.yaml -p "API_HOST=$certificateApiHost" -p "AUTH_HOST=$certificateAuthHost" -o json
if ($LASTEXITCODE -ne 0) { throw "Custom domain rendering failed" }
$customDomainManifest
$customDomainManifest | oc -n $certificateProject apply --dry-run=server -f -
if ($LASTEXITCODE -ne 0) { throw "Custom domain validation failed" }
```

After review:

```powershell
$customDomainManifest | oc -n $certificateProject apply -f -
if ($LASTEXITCODE -ne 0) { throw "Custom domain apply failed" }
oc -n $certificateProject get ingress mic3-api-custom mic3-auth-custom
oc -n $certificateProject get routes
Invoke-RestMethod "https://$certificateApiHost/health"
Invoke-RestMethod "https://$certificateApiHost/ready"
```

Require trusted HTTPS without bypass flags, admitted edge-TLS Routes, and HTTP
redirects. Compare served certificates with the production Secrets' public
certificates. Do not print full generated Route YAML; it can contain private keys.

This template changes routing only, not OIDC configuration. A fresh hostname
transition needs coordinated identity handling; an existing deployment must use
its **active configured issuer**. Recheck discovery without assuming a cloud issuer:

```powershell
$activeIssuer = Read-Host "Active configured HTTPS OIDC issuer"
$discovery = Invoke-RestMethod "$activeIssuer/.well-known/openid-configuration"
if ($discovery.issuer -ne $activeIssuer) { throw "OIDC issuer mismatch" }
Invoke-RestMethod $discovery.jwks_uri
```

Run [smoke checks](#login-and-smoke-checks) against the active public URLs. Preserve
original Routes/DNS alias targets and check their health/readiness where still
supported, without expecting discovery to advertise their hostname. After a real
renewal, compare the served certificate with the renewed Secret and verify HTTPS;
do not force repeated production issuance merely to test propagation.

#### Cleanup and routing rollback

When staging experiments are no longer needed, confirm the namespace and remove
only the named staging resources. Secrets may survive Certificate deletion:

```powershell
if ([string]::IsNullOrWhiteSpace($certificateProject) -or $certificateProject -ne (oc project -q)) { throw "Confirm the intended project first" }
oc -n $certificateProject delete certificate mic3-api-staging mic3-auth-staging --ignore-not-found
if ($LASTEXITCODE -ne 0) { throw "Certificate cleanup failed" }
oc -n $certificateProject delete issuer mic3-letsencrypt-staging --ignore-not-found
if ($LASTEXITCODE -ne 0) { throw "Issuer cleanup failed" }
oc -n $certificateProject delete secret mic3-api-staging-tls mic3-auth-staging-tls mic3-letsencrypt-staging-account --ignore-not-found
```

Inspect owner references/events if solver resources remain. Preserve production
resources, original Routes, and database PVCs; never use namespace-wide deletion.

To undo **only custom routing**, delete the two parent Ingresses; their owned
Routes are garbage-collected:

```powershell
oc -n $certificateProject delete ingress mic3-api-custom mic3-auth-custom --ignore-not-found
```

This removes custom-hostname access and does not revert an issuer transition.
If the active issuer uses that hostname, coordinate identity recovery first.
