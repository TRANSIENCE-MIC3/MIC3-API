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

**Pushing the tag migrates the live database and deploys all three services. Merging alone does not.**
Review migrations, back up data, and check compatibility with running execution
services first. Never move an existing release tag.

[Actions](../.github/workflows/publish-image.yml) tests and builds the API,
publisher and worker, publishes the tested images, and saves their immutable
references in the GitHub release's `release.json`. Preflight verifies existing
resources, permissions, release identities and Helm state before any mutation.
It then migrates PostgreSQL, upgrades `mic3-execution`, rolls out the API, checks
public endpoints and submits a small diagnostic run through the normal outbox,
Kafka, worker and storage path. Success requires acceptance, Job identity,
artifact bytes and published request/terminal events. Kafka, storage,
certificates and database identities remain independent.

Deployment tooling/charts follow the workflow commit; images follow the tagged
application commit. Both are recorded in the Actions summary. Reviewed values
live in [execution.values.yaml](../deploy/okd/execution.values.yaml); images come
only from the release record. Do not edit the old ignored values file for routine
delivery: upgrades reset values to chart defaults plus reviewed configuration.

Initial installation uses zero replicas; enabled operation is one publisher and
one worker with capacity one. `Recreate` updates briefly pause processing while
existing model Jobs continue. Migrations and message contracts must support both
the old and new processes throughout rollout; breaking changes need a separate
maintenance plan. Ordinary delivery never drains or deletes model Jobs.

The visible `mic3-deployment-check` catalog model uses the API image to write a
tiny file. Each workflow attempt submits one new run, waits up to fifteen minutes
including queue time, and retains runs/artifacts/model Jobs. The verification Job
uses the worker image, existing operator database and artifact-reader Secrets,
no Kubernetes token, and no Kafka credentials. It does not restart or resubmit.
After logs are collected its Job expires after one day. A timeout leaves its run
intact, so inspect the recorded run ID before retrying.

### One-time automation bootstrap

Using the operator kubeconfig, review the committed values against `helm get values
mic3-execution`. Preserve any deliberate operational overrides in the reviewed
file first. Confirm all chart resources already exist and the release is deployed.
The permanent API-server peer must work without a temporary diagnostic policy.

Set the GitHub `eosc-development` variable `EXECUTION_API_CIDRS` to a JSON array
of verified API Service/backend CIDRs (for example `["10.0.0.1/32"]`, using the
actual cluster addresses). The committed OpenShift API namespace selector is
combined with those addresses. No host/project identifier is baked into the chart.
For the existing verified installation, obtain the value from its saved Helm
configuration after selecting the EOSC kubeconfig:

```powershell
$env:KUBECONFIG = (Resolve-Path deploy/okd/secrets/eosc-kubeconfig).Path
$liveValues = helm get values mic3-execution -o json | ConvertFrom-Json
ConvertTo-Json -InputObject @($liveValues.networkPolicy.apiPeers | Where-Object ipBlock | ForEach-Object { $_.ipBlock.cidr }) -Compress
```

Copy the resulting nonempty JSON array into the environment variable in GitHub
Settings → Environments → `eosc-development` → Environment variables.

Apply the reviewed [deployer template](../deploy/okd/deployer-template.yaml) once
with an operator identity and the actual namespace:

```powershell
$env:KUBECONFIG = (Resolve-Path deploy/okd/secrets/eosc-kubeconfig).Path
$namespace = oc config view --minify -o jsonpath='{.contexts[0].context.namespace}'
oc process --local -f deploy/okd/deployer-template.yaml -p "NAMESPACE=$namespace" -o yaml | oc apply -f -
python scripts/bootstrap_execution_identity.py --record deploy/okd/secrets/release-0.1.9.json
python scripts/bootstrap_execution_identity.py --record deploy/okd/secrets/release-0.1.9.json --apply
```

The bootstrap verifies **all three live images** against the trusted record before
annotating publisher/worker metadata, using resource-version checks to reject
concurrent changes. It does not restart Pods. Replace the record path if the
current installed release differs. Never relabel an unknown/mixed installation.
Bootstrap seeds the annotations then releases only its ownership of those three
fields, leaving their values intact for Helm to adopt. Merely naming an annotation
writer `helm` is insufficient: Kubernetes distinguishes Update and Apply ownership.
Preflight uses server-side apply as Helm to detect conflicts before migrations.

If bootstrap was run with the older script and an upgrade reports conflicts with
`kubectl-annotate` on the three `delivery.mic3.io/*` annotations, wait until the
Actions run has stopped. With the corrected local script and the record matching
the **currently installed images**, run:

```powershell
python scripts/bootstrap_execution_identity.py --record deploy/okd/secrets/release-0.1.9.json --apply --repair-ownership
```

This revalidates all three live images and removes only the three verified keys'
bootstrap ownership entries from `managedFields`, guarded by resource version.
Annotation values, Pod templates, images, unrelated ownership and annotations stay
unchanged. Unknown owners are rejected; existing Helm Apply ownership is preserved.
This is a narrowly scoped recovery, not a reset of `managedFields`. If interrupted,
rerun the command. Do not run concurrent delivery during the handoff. Then rerun the failed
Actions jobs for the same release: a terminal `failed` Helm revision is retryable.
No image rebuild, tag change, database downgrade or blanket `--force-conflicts`
is needed for this one-time repair.

The deployer can update only named execution resources and the API Deployment.
Helm Secret storage requires namespace-wide Secret get/list/create/update/delete;
RBAC cannot restrict those operations by release prefix or label. This exposes
namespace credentials to this deployment identity. Job creation already permits
mounting namespace Secrets; treat the deployer as a trusted namespace operator.
It receives no cluster-wide, `bind`, or `escalate` grant. Missing resources require
operator repair rather than implicit installation. Keep `OPENSHIFT_TOKEN` for the
existing deployer account; changing its Role does not require replacing its token.

After merging the prepared 0.1.10 release to `master`, push `v0.1.10` using the
release commands above. Check every stage in the Actions summary and retain its
diagnostic run ID. Then start a fresh manual workflow run from `master` with
`release_tag=v0.1.10`: it must reuse the published digests and pass a fresh
diagnostic run. This is the first live acceptance check; local kind success does
not establish EOSC deployment or recovery. No new Kafka/storage setup is needed.

## Configuration and recovery

- Manual EOSC access: `deploy/okd/secrets/eosc-kubeconfig`; local kind uses
  `deploy/local/kubeconfig`. Keep credentials and local values out of Git.
- GitHub environment `eosc-development`: secret `OPENSHIFT_TOKEN`, optional
  `OPENSHIFT_CA_PEM`; variables `OPENSHIFT_SERVER`, `OPENSHIFT_NAMESPACE`,
  `API_BASE_URL`, `OIDC_ISSUER_URL`, `EXECUTION_API_CIDRS`. Personal `oc login` does not refresh Actions.
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
records support API-only retries and explicitly report execution unverified;
version-2 records deploy all three images. Older versions are refused. A same-version
retry must match live images/source wherever that version is already recorded.
Partial publication without a complete record requires verified-evidence recovery
or a new version, never overwriting image tags. Code fixes require a new release.
There is no automatic database downgrade or rollback.

On failure the workflow stops, records each stage separately, and collects bounded
logs/events, Helm status, live/intended images and the diagnostic run ID. A partial
rollout is not success. A terminal `failed` Helm revision can be retried after fixing
the cause; `pending-*` revisions block delivery. For pending state, first establish
that no deployment is still running, inspect history and live state, then perform
operator recovery with schema-compatible images. Never blindly delete Helm Secrets
or use rollback to undo a database migration. API failure after execution upgrade
leaves the newer worker/publisher running; retry the same release or fix forward.

For runtime failures, inspect publisher/worker logs, the affected Job and namespace
events. Deployment readiness alone does not prove execution health. To pause:

```powershell
oc --kubeconfig $kubeconfig --namespace $namespace scale `
  deployment/mic3-publisher deployment/mic3-worker --replicas=0
```

Existing model Jobs continue. Automation refuses paused services; explicitly
restore reviewed operation after resolving the incident before retrying delivery.
Do not resubmit a run merely because processing was interrupted.

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
