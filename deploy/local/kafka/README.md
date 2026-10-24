# Kafka

From the repository root, with `.env` configured:

```powershell
docker compose up -d --wait postgres kafka
docker compose run --rm kafka-init
python -m pip install -e ".[messaging,execution]"
python -m alembic upgrade head
python -m mic3_api.publisher
# In a second terminal:
python -m mic3_api.worker
```

Host clients use `127.0.0.1:9092`; Docker clients use `kafka:19092`.
Stop either process with Ctrl+C. Add `--execute` to the worker only after local
kind/AIStor and execution settings are configured; otherwise it only accepts runs.

The [deployment chart](../../helm/kafka) is independent of API releases. Its
default stage defines a private CA and four cert-manager identities; the broker
is disabled. Enabling the broker requires a saved cluster ID and a separate
ConfigMap containing the verified public CA certificate. Certificates/Secrets
use `<name>-broker-tls`, `-publisher-tls`, `-worker-tls`, and `-operator-tls`.
The CA signing Secret is never mounted by applications.

The single-node chart retains its PVC, requires mTLS on client/controller ports,
and initializes two bounded-retention topics plus application ACLs. It is a
development deployment without high availability. Network policies are additive:
EOSC's existing same-namespace allowance must be reviewed before claiming network
isolation. EOSC deployment and cert-manager issuance remain pending.

Leaf certificates default to 90 days with renewal 15 days before expiry. After
renewal, deliberately restart Kafka and the publisher/worker to load new keys;
Secret updates alone do not reload these processes. The CA has a ten-year life
and retains its signing key on renewal. Its independently distributed trust
bundle is not automatically replaced: track CA expiry and plan trust overlap,
certificate reissuance and restarts before CA rotation. Preserve the CA Secret,
cluster ID and PVC during recovery; never format nonempty storage to fix startup.

The [execution services chart](../../helm/execution-services) installs disabled
publisher/worker Deployments. Enable one of each only with immutable images,
verified Kubernetes API network destinations, migrated data and resolved queued
work. It references distinct database Secrets (`database`, `username`, `password`),
client TLS Secrets and public trust; it creates no credentials. The worker mounts
only the artifact reader credentials; model copy containers use the uploader.
Operational model overrides are mounted from a ConfigMap; invocation still comes
from the catalog. Configuration changes roll workloads; credential renewal needs
an explicit restart. Logs, exit status and Kubernetes events are the diagnostics.

Network peer selectors must match the deployed database, Kafka and storage Pods.
API peers must include verified destinations appropriate to the cluster's service
translation. Existing EOSC same-namespace allowances are unchanged and additive.
The worker Role allows Job get/create and Pod list; permission to create Jobs also
allows specifying their Pod configuration, so namespace/RBAC admission remains an
important trust boundary even though direct Secret reads are denied.

`tests/smoke/test_execution_services.py` is opt-in via
`MIC3_EXECUTION_SERVICES_SMOKE=1`, `MIC3_KIND_KUBECONFIG` and `MIC3_KIND_CONTEXT`.
It refuses non-local/non-kind clusters, uses a random disposable namespace and
temporary database/TLS identities, and preserves AIStor objects under fresh run
UUIDs. It requires local packaged images and existing `mic3-local` storage Secrets.
Kind results do not establish EOSC admission, cert-manager issuance or network
policy enforcement. Release-workflow review and the 0.1.9 checkpoint precede live
deployment; the operator walkthrough is provided separately, two steps at a time.
