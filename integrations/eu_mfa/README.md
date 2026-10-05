# EU-MFA container integration

This independent image packages the complete upstream source snapshot and bundled
datasets. No upstream source/configuration changes, adapter, or Kafka integration
are required. The API image is unaffected.

Validated locally on 2026-10-02: Linux/amd64 image built with Python 3.12;
two network-disabled buildings runs exited 0 in approximately 4.8 seconds each.
The repeat used UID 1001230000, GID 0. All 12 CSVs were byte-identical between
container runs and matched the host baseline after CRLF/LF normalization.
Outputs total 54,754,358 bytes. `pip check` passed. This does not establish
scientific acceptance, peak memory, or EOSC volume/security compatibility.
The user confirmed EOSC buildings execution on both `standard` and `shared`/RWX
storage, with all 12 downloaded CSVs matching the baseline. Shared-storage
concurrency across nodes was not tested. Those disposable Jobs/PVCs were removed.
AIStor upload/download and file persistence after a Pod restart are user-confirmed;
the Helm/mc buildings Job also completed on 2026-10-05, with copied files
confirmed by the user. Remaining checks are listed in the Helm guide.

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

## Object storage through Helm

Follow the [Helm deployment and run guide](../../deploy/helm/README.md).
[values.yaml](values.yaml) supplies the existing model image, buildings command,
working directory, and output mount to the model-run chart. Other modes require
separate qualification before being exposed.

The model writes into a Pod-local temporary volume. The official MinIO Client
container copies the output tree to AIStor after the model exits successfully.
No custom uploader image, scientific code change, checksum inventory, or per-run
PVC is needed. No source push or API release is required to run the chart.

The Helm/mc copy path succeeded on EOSC. Exact downloaded-file comparison,
permissions, restart persistence for this installation, and concurrent executions
remain to be verified. Prior model baselines remain valid.
