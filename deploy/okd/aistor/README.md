# AIStor on OKD

Standalone object storage evaluation. Stable resource names permit retaining the
instance after acceptance; production readiness is not yet established. This
manifest adapts MinIO's [container instructions](https://docs.min.io/aistor/installation/container/install/),
not an upstream Kubernetes template. It requires no operator or cluster-wide RBAC.

Provision Secret `mic3-aistor-credentials` separately in the selected namespace,
with keys `minio.license`, `MINIO_ROOT_USER`, and `MINIO_ROOT_PASSWORD`. Never store
their values in this repository. Confirm the issued license tier and expiry.

From the repository root, with `oc` targeting the intended project:

```powershell
oc apply -f deploy/okd/aistor/application.yaml
oc rollout status deployment/mic3-aistor --timeout=300s
oc port-forward service/mic3-aistor 9000:9000 9001:9001
```

Open http://localhost:9001 with the provisioned credentials. The S3 service is
`http://mic3-aistor:9000` within the namespace. No public Route is created.
The 5Gi `standard` PVC is mounted only by AIStor; clients access objects through
S3. CPU/memory settings are initial evaluation values, not measured requirements.
OKD assigns the permitted UID and filesystem group. Keep one replica; `Recreate`
avoids overlapping instances sharing the data volume and causes restart downtime.

Acceptance: upload/download the EU-MFA CSVs and compare checksums, restart the
Deployment and verify persistence, then test concurrent clients. Pod readiness
alone does not verify licensed S3 access. Backups, scoped client credentials,
network/TLS policy, and resource measurements remain required before broader use.

Reapply the manifest to redeploy; the existing PVC and Secret remain in use.
Deleting the PVC can destroy stored artifacts. Credentials/license recovery must
be managed separately from Git. No API/model integration is included here.
