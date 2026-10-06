# Setup and deployment

For local startup, use the [README quickstart](../README.md#local-quickstart).
This guide covers releases, debugging, and essential operations.

## Prepare a release

1. Update `[project].version` in [pyproject.toml](../pyproject.toml) to the next
   unused stable version. No other version file needs editing.
2. Commit reviewed changes, push to `staging`, and merge into `master`.
3. Tag the merged commit with `v` followed by that exact version and push the tag
   using the [commands below](#deploy-commands). Never move an existing release tag.

**Merging does not deploy. Pushing a version tag triggers deployment.**
The [release workflow](../.github/workflows/publish-image.yml) also supports
manual runs from GitHub Actions. EOSC infrastructure and the GitHub environment
must already be configured.

## What deployment does

**Actions → Release and deploy API**:

1. Validates the tag against the package version.
2. Runs unit/integration tests, publishes the image, and saves its source commit
   and immutable digest in the release's `release.json`. An existing valid release
   is reused without rebuilding or rerunning those tests.
3. Validates manifests and runs `alembic upgrade head` in a migration Job.
4. Updates the API only after migration succeeds and waits for rollout.
5. Checks public health/readiness, unauthenticated `401`, and OIDC discovery/keys.

The Actions summary records the image, source/workflow commits, migration Job,
and outcome. Authenticated login/admin acceptance is a separate smoke check.
Database provisioning, Keycloak reconciliation, networking, certificates, and
RBAC are separate operations, not part of normal API releases.

## Debugging and retry

Start with the failed step and summary in GitHub Actions.

| Failure | Next action |
| --- | --- |
| Tests or publication | Inspect test/build logs. Application/dependency fixes need a new patch release. For partial publication, recover the missing release record from verified build evidence or use a new version. |
| Migration | Inspect the named Job's logs collected by Actions. Resolve the failure before retrying; unfinished earlier migrations must terminate first. |
| Rollout | Inspect rollout output and API Pod logs/events for startup, image-pull, or configuration errors. |
| Public checks | Check the configured API URL, issuer, database connectivity, and OIDC discovery. Readiness alone does not prove authenticated login. |

After resolving an operational failure, select **Run workflow**, choose `master`,
and enter the existing `release_tag`. It reuses the release record; if neither
record nor image exists, it tests/publishes the tagged source first. An image
without its record requires recovery, not overwrite.

For workflow/template fixes, start a **fresh manual run** from updated `master`;
rerunning an old failed run retains its old workflow. Application and migration
code still come from the tag/image, so code fixes require a new patch version.

There is no automatic rollback or database downgrade. Successful migrations remain
applied even if rollout fails and must support the running API. Never overwrite
version images or delete database PVCs. Normal automation blocks older versions.
Manual image recovery requires schema compatibility, rollout/login checks, and
matching Deployment release annotations/version label; do not run older migrations.
Recover `release.json` only from verified tag, commit, repository, and image digest;
never replace a valid record.

## Operational reference

### Configuration and infrastructure

| Area | Configuration / source |
| --- | --- |
| Local | [.env.example](../.env.example), [Compose](../compose.yaml); distinct local-only passwords in untracked `.env` |
| GitHub `eosc-development` | Secrets: `OPENSHIFT_TOKEN`, optional `OPENSHIFT_CA_PEM`. Variables: `OPENSHIFT_SERVER`, `OPENSHIFT_NAMESPACE`, `API_BASE_URL`, `OIDC_ISSUER_URL` |
| API runtime | Secrets `mic3-postgres-credentials` (database, username, password), `mic3-oidc-config` (issuer-url, audience), `ghcr-pull` (`read:packages`) |
| Infrastructure | [PostgreSQL](../deploy/okd/postgres.yaml), [networking](../deploy/okd/networking.yaml), [deployer template](../deploy/okd/deployer-template.yaml) (`NAMESPACE`) |
| Release resources | [API template](../deploy/okd/application-template.yaml), [migration template](../deploy/okd/migration-template.yaml) |

For manual EOSC operations, log in and select the intended project first.
Preserve database credentials/PVCs; never silently rotate credentials against
persisted databases. Keep secrets and user tokens out of Git and Actions logs.

### Object storage and model Jobs

[Object-storage chart](../deploy/helm/object-storage) manages AIStor, a retained
5Gi PVC, and bucket/user bootstrap. It is independent of API releases; merging
chart changes does not deploy them. Bump the affected `Chart.yaml` version when
changing its templates/defaults; this is not an API version or Git release tag.

Existing Secrets: `mic3-aistor-credentials` (`MINIO_ROOT_USER`,
`MINIO_ROOT_PASSWORD`, `minio.license`), `mic3-artifact-uploader-credentials`
(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`), and `ghcr-pull` for model pulls.
The transfer identity is shared across models and scoped to `mic3-artifacts`.
Only bootstrap receives administrator credentials. Keep Secret values outside Git.

From the repository root, with Helm installed and `oc` logged into the project:

```powershell
helm upgrade --install mic3-aistor ./deploy/helm/object-storage --wait --timeout 10m
oc logs job/mic3-aistor-bootstrap -c configure
oc port-forward service/mic3-aistor 9000:9000 9001:9001
```

Console: http://localhost:9001, using the server Secret's administrator credentials.
After changing that Secret's root credentials, restart `deployment/mic3-aistor`
and wait for rollout before rerunning Helm. Never delete the PVC to retry setup.

The [model-run chart](../deploy/helm/model-run) renders individual Jobs, not Helm
releases. Model-specific configuration is registered from the
[EU-MFA catalog input](../integrations/eu_mfa/release.json); export the chosen
queued run as chart values. Replace `RUN_UUID` below with the ID returned
by submission. Each run is executed once; a retry requires a new submission.
In another terminal:

```powershell
$runName = 'model-run-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
python -m mic3_api.cli model-job-values --run-id RUN_UUID | helm template $runName ./deploy/helm/model-run -f - | oc create -f -
oc wait --for=condition=complete "job/$runName" --timeout=600s
oc logs "job/$runName" -c model
oc logs "job/$runName" -c copy
```

The model writes into `emptyDir`; official `mc` copies the tree to
`mic3-artifacts/<model>/sha256-<full-image-digest>/<run-id>/outputs/`.
All modes of a deprecated image share one cleanup prefix. This applies to new
Jobs; existing objects keep their original prefixes. Deleting objects does not
delete PostgreSQL run records. Only the copy container
receives transfer credentials. New values affect new Jobs, not existing runs.
Delete inspected Jobs with `oc delete job $runName`; temporary outputs and Pod
logs disappear, uploaded objects remain. Failed copies can leave partial prefixes.
Only AIStor mounts its PVC; Helm retains it on uninstall.

### MIC3 administrator access

After migrations and first login, obtain the intended user's internal UUID from
`/users/me`. Replace `MIC3_UUID` below with that UUID. Promotion requires an
existing active account, preserves other roles, and is safe to repeat. It uses
MIC3 database credentials, not Keycloak administrator privileges or token claims.

Local environment:

```sh
python -m mic3_api.cli grant-admin --user-id MIC3_UUID
```

EOSC, after deploying the command and migration:

```sh
oc exec deployment/mic3-api -c api -- python -m mic3_api.cli grant-admin --user-id MIC3_UUID
```

Verify admin access and ordinary-member rejection separately on each environment.
Login still grants only `member`; promotion is effective on the next request.
Downgrading the admin migration removes the admin role and assignments.

### Login and smoke checks

In Postman use Authorization Code with PKCE, SHA-256, no client secret,
scope `openid profile email`, and callback `https://oauth.pstmn.io/v1/browser-callback`.
Use client `mic3-local` locally or `mic3-postman` on EOSC. Auth/token URLs are
`<active-issuer>/protocol/openid-connect/auth` and `.../token`; local issuer is
`http://localhost:8080/realms/mic3`. Use the access token with the API.
Swagger at `/docs` describes endpoints and request/response schemas.

Set smoke variables in the process/IDE: `API_BASE_URL`, `OIDC_ISSUER_URL`,
and `OIDC_ACCESS_TOKEN` for authenticated profile checks; `OIDC_ADMIN_ACCESS_TOKEN`
and `OIDC_MEMBER_ACCESS_TOKEN` for admin checks. Run the relevant files in
[tests/smoke](../tests/smoke); clear temporary tokens afterward. Do not commit tokens.

### Tests

With Docker running: `python -m pytest tests/unit tests/integration`.
Testcontainers uses disposable databases/Keycloak, separate from Compose/EOSC.
Without Docker: `python -m pytest tests/unit tests/integration/api/test_health.py tests/integration/api/test_readiness.py`.
Use [admin smoke checks](../tests/smoke/test_admin_authorization.py) for live
admin `200`, member `403`, and missing/invalid-token `401` acceptance.

### Keycloak and certificates

- Local realm changes: update [local JSON](../deploy/local/keycloak/config/mic3-realm.json)
  and run `docker compose run --rm keycloak-config`.
- EOSC: update the realm ConfigMap from [realm JSON](../deploy/okd/keycloak/mic3-realm.json)
  and create the [configuration Job](../deploy/okd/keycloak/configure.yaml).
  It requires an existing `mic3` realm, confidential service-account client
  `mic3-realm-configurator` with `realm-management/realm-admin`, Secret
  `mic3-keycloak-reconciler` (`client-secret`), and `mic3-keycloak-smtp`
  (keys `MIC3_SMTP_HOST`, `MIC3_SMTP_PORT`, `MIC3_SMTP_FROM`,
  `MIC3_SMTP_FROM_NAME`, `MIC3_SMTP_USER`, `MIC3_SMTP_PASSWORD`).
  Users/roles/groups remain outside reconciliation. Verify repeated reconciliation,
  permanent admin access, and login/mail/recovery before retiring bootstrap admin
  references or Secrets. Never delete the realm/PVC to refresh configuration.
- [Staging](../deploy/okd/certificates/staging-template.yaml) and
  [production certificates](../deploy/okd/certificates/production-template.yaml)
  require cert-manager, HTTP-01 reachability, and `ACME_EMAIL`, `API_HOST`,
  `AUTH_HOST`. Staging certificates are untrusted; never use them on live Routes.
- [Custom domains](../deploy/okd/custom-domains-template.yaml) connect production
  TLS Secrets through parent Ingresses. cert-manager owns renewal; OKD manages
  generated Routes. Edit parents, preserve production resources, and inspect only
  public certificates. Scheduled renewal does not prove completed propagation.
  Issuer changes require coordinated API/Keycloak/client configuration and existing
  identity mapping handling; routing rollback alone does not undo them.

## Deploy commands

After the reviewed version change is merged, replace `X.Y.Z` with the exact
`[project].version`. Run from a clean checkout, one command at a time; stop on
failure. Then watch **Actions → Release and deploy API**.

```sh
git switch master
git pull --ff-only origin master
git tag vX.Y.Z
git push origin vX.Y.Z
```
