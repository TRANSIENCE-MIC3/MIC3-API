# Setup and deployment

Local startup: [README](../README.md#local-quickstart). Kafka and worker setup:
[Kafka guide](../deploy/local/kafka/README.md). EOSC now runs the API, Kafka,
publisher, worker and AIStor. Authenticated API submission and the operator CLI
share the same durable execution flow.

## Run API through Postman

Obtain an access token using the login instructions below. Set Bearer Token
authorization on the run requests and use your configured API base URL.

1. Call public `GET /models` and copy the desired EU-MFA release UUID.
2. Send `POST /runs` with JSON content type and this body:

   ```json
   {"model_release_id": "<release UUID>", "parameters": {"mode": "buildings"}}
   ```

3. A `202` response supplies the queued run ID and a relative `Location` header.
   Call `GET /runs/{id}` to inspect it. A successful submission means the run and
   outbox event committed, even if Kafka or the worker is temporarily unavailable.
4. Call `GET /runs?limit=50&offset=0` for your own history, newest first. The maximum
   page size is 100. Concurrent submissions can shift offset-based pages.

Run detail is available to its owner or an active admin; admins can also inspect
unattributed CLI runs. Listing always returns only the caller's runs. Ownership
comes from the token's resolved MIC3 account, never from request JSON. Unknown
request fields are rejected; parameters currently support only `mode`.

Statuses are `queued`, `running`, `succeeded`, and `failed`. `accepted_at` identifies
worker acceptance while a run can still be queued; `running` begins at dispatch
intent, before the model process necessarily starts. Detail includes lifecycle
timestamps, a controlled failure code, and recorded artifact names/sizes. File
names are basenames and may repeat for different output directories. Metadata
does not check current storage availability, and failed runs may include collected
files. Storage locations, download links, cluster identifiers and outbox records
are not exposed. List items omit artifacts.

`RUN_MAX_ACTIVE_PER_USER` (default 5) and `RUN_MAX_ACTIVE_GLOBAL` (default 20) bound
API admission of queued/running work, including work not yet accepted by the
worker. Configure positive integers in the API environment; the OKD application
template exposes matching parameters. All existing runs count toward the global
bound. CLI submissions retain an operator bypass and can exceed these bounds.
Admins submitting through the API obey the same limits. Limit rejection returns
`429`; terminal runs release capacity. Repeating POST creates a new run, so do not
automatically retry an ambiguous submission response.

Missing/invalid authentication returns `401`, inactive accounts or forbidden
run access `403`, missing runs/releases `404`, invalid input `422`, and unavailable
authentication/database dependencies `503`. No schema migration is needed for
these REST routes. The SSE endpoint below requires migration 0006_run_notifications.

An opt-in smoke test submits **one real buildings execution** and waits for success:
set `API_BASE_URL`, `OIDC_ACCESS_TOKEN`, `RUN_MODEL_RELEASE_ID`, and
`RUN_EXECUTION_SMOKE=1`, then run `python -m pytest tests/smoke/test_runs.py -v -s`.
`RUN_SMOKE_TIMEOUT_SECONDS` defaults to 1800. A timeout does not cancel the run;
keep its printed ID for inspection. Run this only against an intended environment
after release approval.

## Personal SSE feed through Postman

Apply `python -m alembic upgrade head` before starting/deploying the new API.
Migration `0006_run_notifications` adds one security-invoker notification function
and row triggers on `runs` and `run_artifacts`. It adds no tables, columns, indexes,
extensions or stored history. Downgrade removes only the triggers/function.

Open `GET {{base_url}}/runs/events` with your Bearer token (optionally set
`Accept: text/event-stream`). Leave it open and submit runs from another Postman
tab. One connection receives all of your runs, including new submissions. Admins
also receive only their own runs. Postman displays named SSE events:

- `runs.snapshot`: `{"items": [...]}` containing full run-detail objects for all
  owned queued/running runs and the newest 50 terminal runs, newest first.
- `run.updated`: one full run-detail object, matching `GET /runs/{id}`.
- `stream.error`: `{"code": "..."}` followed by closure. Codes are `unavailable`,
  `resync_required`, `token_expired`, and `access_denied`.

Heartbeat comments arrive every 15 seconds; they do not query run statuses.
The feed stays open after individual runs finish. Notifications are commit-only
wakeups, not durable events: intermediate transitions can coalesce, and updates
can repeat. On reconnect a new snapshot restores current state; older history
is available through `GET /runs`. `Last-Event-ID` is ignored and no replay is promised.

When the token expires, reconnect with a refreshed token. Local account activity
is checked before sending data and at heartbeat intervals; this does not perform
OIDC introspection or detect provider-side token revocation before expiry.
A future browser client should use streaming `fetch` with an Authorization header,
not a token in the URL. Refresh the snapshot window on reconnect; fetch older
history again if it was displayed and needs reconciliation.

New connections receive `401` for invalid/expired tokens, `403` for inactive
accounts, `429` for connection limits, or `503` when the listener/database is
unavailable. After headers are sent, failures use `stream.error` where possible.
On transient failure, reconnect with exponential backoff (1 to 30 seconds).
Do not retry `401`/`403` until authentication/access is resolved.

One dedicated autocommit PostgreSQL connection and listener thread serves each
API process. The API still has no Kafka consumer. Listener failures close streams;
the listener retries independently. REST remains available, `/health` stays public,
and `/ready` continues to check PostgreSQL only. Notification migration absence
leaves SSE unavailable. Logs report listener outages without credentials.

Settings (environment and OKD template parameters), all positive integers:

| Setting | Default | Meaning |
| --- | --- | --- |
| `SSE_MAX_STREAMS_PER_USER` | 5 | Connections per user, per API process |
| `SSE_MAX_STREAMS` | 100 | Total connections per API process |
| `SSE_PENDING_RUNS` | 100 | Distinct pending run updates per connection |

Slow clients exceeding the pending bound receive `resync_required` and must
reconnect. A blocked send is cut off after 30 seconds; an error frame may not be
deliverable. Connections and transactions for ordinary queries are short-lived;
the listener does not hold a transaction open while waiting. Notification payloads
contain only run UUIDs; notification failure can roll back the originating write.

After release approval, verify immediate events and 15-second heartbeats through
the actual EOSC route, then observe two buildings runs in one feed and reconnect.
No route timeout change is currently required by local evidence. Do not treat
local streaming tests as confirmation of EOSC proxy behavior.

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
