# EU-MFA buildings container qualification

This independent image packages the complete upstream source snapshot and bundled
datasets. No upstream source/configuration changes, adapter, or Kafka integration
are required. The API image is unaffected.

Validated locally on 2026-10-02: Linux/amd64 image built with Python 3.12;
two network-disabled buildings runs exited 0 in approximately 4.8 seconds each.
The repeat used UID 1001230000, GID 0. All 12 CSVs were byte-identical between
container runs and matched the host baseline after CRLF/LF normalization.
Outputs total 54,754,358 bytes. `pip check` passed. This does not establish
scientific acceptance, peak memory, or EOSC volume/security compatibility.
The user confirmed registry publication and successful EOSC buildings execution
on a dedicated `standard` PVC, with export messages in the logs. EOSC output
content has not yet been independently compared. The next test uses `shared`
storage and is not yet deployed.

Published image:
`ghcr.io/transience-mic3/eu-mfa@sha256:a96dd9d7e548b4abe7d3992b905bb605fc1a5f9206a7f65e75d497aa29e9268b`

The base image and upstream source are pinned. Upstream pins direct dependencies;
transitive dependencies are not locked. Local evidence, logs, comparisons, and
installed dependency versions are retained in
`%TEMP%/mic3-eumfa-20261002/`. Record the registry digest after publishing.

## Local test

Start Docker Desktop with Linux containers. From the MIC3 repository root, run
these PowerShell commands, checking each command succeeds before continuing:

```powershell
docker build --platform linux/amd64 -f integrations/eu_mfa/Dockerfile -t eu-mfa:87e94f4 integrations/eu_mfa
$outputDirectory = Join-Path $env:TEMP ('eu-mfa-buildings-' + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $outputDirectory
docker run --rm --network none --mount "type=bind,source=$outputDirectory,target=/opt/eu-mfa/data/baseline_buildings/output" eu-mfa:87e94f4
Get-ChildItem -LiteralPath (Join-Path $outputDirectory 'export/flows')
docker run --rm --entrypoint python eu-mfa:87e94f4 -m pip freeze
```

The current baseline is expected to export ten flow CSVs and two stock CSVs.
File presence alone does not establish scientific correctness. Compare headers,
dimensions, and numerical values with the host baseline before acceptance.

## Registry publication

The confirmed GitHub organization is `TRANSIENCE-MIC3`, repository `MIC3-API`.
Its existing API workflow publishes `ghcr.io/transience-mic3/mic3-api`.
The separate model package is `ghcr.io/transience-mic3/eu-mfa`.
No new Git repository or pre-created package is needed: the first authorized
push creates the package, private by default. Organization policy must allow
your account to create packages.

For a manual push, authenticate locally using your GitHub username and a classic
PAT with `write:packages`, authorized for organization SSO if required. Run
`docker login ghcr.io --username YOUR_GITHUB_USERNAME` interactively and supply
the PAT at the password prompt. Do not paste tokens into chat or commit them.
The image source label associates this package with `TRANSIENCE-MIC3/MIC3-API`;
separate labels retain the upstream source and commit.

```powershell
$imageRef = 'ghcr.io/transience-mic3/eu-mfa:87e94f4'
docker tag eu-mfa:87e94f4 $imageRef
docker push $imageRef
docker image inspect $imageRef --format '{{json .RepoDigests}}'
```

Retain the pushed digest and reference `ghcr.io/<owner>/eu-mfa@sha256:<digest>`
in the eventual Job. No duplicate Git repository is needed.

After publishing, inspect the organization's Packages page and verify repository
linkage and access settings. EOSC pull credentials must have access to this new
package; access to the API package alone does not prove that. Publishing does
not commit source changes or trigger the API's tag-based release workflow.

## Manual shared-storage test

`eosc-test-template.yaml` renders a model-wide `ReadWriteMany` claim and a
buildings-only Job. The claim name is independent of the Job. Its `shared`
storage class is documented for PSNC EU-1. Use a new claim; do not try to convert
the existing `standard` claim in place.

An init container creates `buildings/<job-name>/attempt-001/`. It fails if that
directory exists, including on an unexpected restart, to avoid mixing attempts.
Use a fresh Job name for another execution. The model container mounts only that
directory at `/opt/eu-mfa/data/baseline_buildings/output`; the scientific source
and command remain unchanged. Other submodules will need their own verified
command/output-path pairing; their execution is not qualified by this test.

The init container temporarily sees the whole claim. In the future, directory
allocation should be a trusted worker/platform responsibility, not access given
to arbitrary scientific code. Shared capacity and directory naming are not
per-model storage quotas or a complete security boundary.

Run from the MIC3 repository root in PowerShell. Check the selected project and
current quotas first. No Git commit, image rebuild, or registry push is needed.
The resource values below are initial test allocations, not measured sizing.

```powershell
oc whoami
oc project
oc get resourcequota storage-limits

$testName = 'eu-mfa-buildings-shared-001'
$pvcName = 'eu-mfa-shared-test-outputs'
$imageRef = 'ghcr.io/transience-mic3/eu-mfa@sha256:a96dd9d7e548b4abe7d3992b905bb605fc1a5f9206a7f65e75d497aa29e9268b'
$rendered = oc process --local -f integrations/eu_mfa/eosc-test-template.yaml `
  -p "TEST_NAME=$testName" -p "PVC_NAME=$pvcName" -p "IMAGE_REF=$imageRef" `
  -p STORAGE_CLASS=shared -p STORAGE_SIZE=1Gi `
  -p CPU_REQUEST=250m -p CPU_LIMIT=1 `
  -p MEMORY_REQUEST=512Mi -p MEMORY_LIMIT=2Gi -o json
if ($LASTEXITCODE -ne 0) { throw 'Template rendering failed' }
$resources = ($rendered | ConvertFrom-Json).items
$pvc = $resources | Where-Object kind -eq 'PersistentVolumeClaim'
$job = $resources | Where-Object kind -eq 'Job'

# Create the claim only once. For subsequent runs, reuse the existing claim.
$pvc | ConvertTo-Json -Depth 30 | oc create -f -
if ($LASTEXITCODE -ne 0) { throw 'PVC creation failed' }
oc wait --for=jsonpath='{.status.phase}'=Bound "pvc/$pvcName" --timeout=180s
if ($LASTEXITCODE -ne 0) { throw 'Inspect PVC events before continuing' }

$job | ConvertTo-Json -Depth 30 | oc create -f -
if ($LASTEXITCODE -ne 0) { throw 'Job creation failed' }
oc wait --for=condition=complete "job/$testName" --timeout=600s
oc logs "job/$testName" -c buildings
```

On failure, inspect `oc describe pods -l "job-name=$testName"` and
`oc logs "job/$testName" -c prepare-output-directory`. Do not remove output
directories to force a rerun; use a fresh Job name.

Acceptance still requires a second Pod mounting this claim read-only, ideally
on another node, to list/download files from
`buildings/<job-name>/attempt-001/export/flows/` and compare all 12 CSVs with the
local baseline. This also checks group permissions across different Pod UIDs.
The API deployment is not modified by this test. A read-only reader can later
stand in for the API during storage qualification.

After successful verification and saving any wanted evidence, manually delete
the test Job and any test reader before deleting this disposable claim. Deleting
the claim can destroy all outputs on it. Do not delete database PVCs or use broad
label-based deletion. No automatic Job/PVC cleanup is configured.

Local checks passed: template rendering, RWX settings, claim linkage, init
directory creation under a non-root UID, subPath alignment, and refusal to reuse
an existing directory. These do not establish EOSC shared-storage behavior.
