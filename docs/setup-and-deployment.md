# Setup and deployment

Local startup: [README](../README.md#local-quickstart). Kafka and worker setup:
[Kafka guide](../deploy/local/kafka/README.md). EOSC now runs the API, Kafka,
publisher, worker and AIStor. Submission currently uses the operator CLI;
authenticated API submission is the next milestone.

## Releases

1. Update `[project].version` in [pyproject.toml](../pyproject.toml) and `appVersion`
   in the [execution chart](../deploy/helm/execution-services/Chart.yaml).
2. Review and merge the feature branch through `staging` into `master`.
3. From a clean, updated `master`, tag that commit with the exact package version:

```powershell
git switch master
git pull --ff-only origin master
git tag vX.Y.Z
git push origin vX.Y.Z
```

**Pushing the tag migrates the live database and deploys the API. Merging alone does not.**
Review migrations, back up data, and check compatibility with running execution
services first. Never move an existing release tag.

[Actions](../.github/workflows/publish-image.yml) tests and builds the API,
publisher and worker, publishes the tested images, and saves their immutable
references in the GitHub release's `release.json`. It then migrates PostgreSQL,
rolls out only the API, and checks public endpoints. Publisher/worker updates
remain manual; Kafka, storage, certificates and database identities are independent.

For execution updates, copy the corresponding image references from `release.json`
into ignored `deploy/okd/secrets/execution.values.yaml`, review replica counts, then:

```powershell
helm upgrade mic3-execution deploy/helm/execution-services `
  --kubeconfig $kubeconfig --namespace $namespace `
  -f deploy/okd/secrets/execution.values.yaml --wait --timeout 5m
```

Initial installation uses zero replicas; enabled operation is one publisher and
one worker with capacity one. Configuration changes restart these workloads.

## Configuration and recovery

- Manual EOSC access: `deploy/okd/secrets/eosc-kubeconfig`; local kind uses
  `deploy/local/kubeconfig`. Keep credentials and local values out of Git.
- GitHub environment `eosc-development`: secret `OPENSHIFT_TOKEN`, optional
  `OPENSHIFT_CA_PEM`; variables `OPENSHIFT_SERVER`, `OPENSHIFT_NAMESPACE`,
  `API_BASE_URL`, `OIDC_ISSUER_URL`. Personal `oc login` does not refresh Actions.
- Runtime Secrets: separate API/publisher/worker PostgreSQL identities, OIDC
  configuration, `ghcr-pull`, separate Kafka TLS identities, and artifact reader/
  uploader credentials. Kubernetes Secrets do not provision database accounts;
  PostgreSQL roles/grants and matching Secret values must be maintained together.
- Only storage bootstrap receives administrator credentials. The worker reads
  artifacts; model copy containers upload them. Preserve PVCs, Kafka cluster ID
  and CA keys. Certificate renewal requires deliberate process restarts; see
  the [Kafka guide](../deploy/local/kafka/README.md).

Inspect the failed Actions step before retrying. A fresh manual workflow run from
`master` with the existing `release_tag` reuses a valid release record. Version-1
records support API-only retries; version-2 records contain all three images.
Partial publication without a complete record requires verified-evidence recovery
or a new version, never overwriting image tags. Code fixes require a new release.
There is no automatic database downgrade or rollback.

For runtime failures, inspect publisher/worker logs, the affected Job and namespace
events. Deployment readiness alone does not prove execution health. To pause:

```powershell
oc --kubeconfig $kubeconfig --namespace $namespace scale `
  deployment/mic3-publisher deployment/mic3-worker --replicas=0
```

Existing model Jobs continue. Align local Helm replica values before the next
upgrade. Do not resubmit a run merely because processing was interrupted.

## Kubernetes API networking

EOSC's worker could not reach the API with only its Service IP allowed. Adding
this peer on TCP 6443 restored connectivity; retain it in the Helm values alongside
verified Service-address access on 443:

```yaml
networkPolicy:
  apiPeers:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: openshift-kube-apiserver
      podSelector: {}
  apiPorts: [443, 6443]
```

The empty Pod selector includes host-networked API-server Pods under OpenShift's
policy handling. Verify connectivity after removing any temporary diagnostic
policy. Network access does not grant Kubernetes permissions. Existing EOSC
same-namespace allowances are additive; this fix does not prove full isolation.

## Object storage and model Jobs

Register [catalog input](../integrations/eu_mfa/release.json) and submit with
`python -m mic3_api.cli register-model-release --config FILE` and
`submit-run --release-id UUID --parameters FILE` in a configured operator environment.
Inspect with `show-run --run-id UUID`. The publisher and worker create Jobs;
**do not also launch a manual Job for the same run**.

Outputs use `mic3-artifacts/<model>/sha256-<digest>/<run-id>/outputs/`. New runs have
separate prefixes. Require a successful run, recorded artifacts and a published
terminal event; inspect scientific outputs separately. Preserve Job diagnostics
until inspection is complete. Failed runs require a new submission to rerun;
existing artifacts and historical records are not automatically removed.

## Login and smoke checks

Use Authorization Code with PKCE/SHA-256, no client secret, scope `openid profile
email`, and the configured Keycloak issuer. For EOSC Postman, use client
`mic3-postman` and callback `https://oauth.pstmn.io/v1/browser-callback`.
Send the access token to `/users/me`; first login grants only `member`.
Operator promotion: `python -m mic3_api.cli grant-admin --user-id MIC3_UUID`.
Never store tokens in Git or run events. Public run-submission endpoints are not
implemented yet.

## Tests

With Docker running: `python -m pytest tests/unit tests/integration`.
[Live smoke tests](../tests/smoke) cover authentication and deployment checks;
keep access tokens in the process environment. Packaged kind execution remains
opt-in and requires explicitly selected local cluster/storage configuration.
Local results do not establish EOSC recovery, isolation or certificate renewal.
