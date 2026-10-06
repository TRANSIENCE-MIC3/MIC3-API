"""Render real charts and check storage/security boundaries without a cluster."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import UUID

import pytest
import yaml

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.models.execution import ExecutionDefinition
from mic3_api.infrastructure.model_jobs import model_job_values
from tests.catalog_data import CONFIG, MODE

ROOT = Path(__file__).resolve().parents[3]
RUN_ID = UUID("12345678-1234-1234-1234-123456789abc")
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")
pytestmark = pytest.mark.skipif(not HELM, reason="Helm required; set HELM_BIN if not on PATH")


def render(chart, *args, mode=MODE):
    values = None
    if chart == "model-run":
        definition = ReleaseDefinition(CONFIG["model_id"], CONFIG["display_name"], CONFIG["image_digest"],
                                       ExecutionDefinition.from_dict(CONFIG["execution_definition"]))
        values = json.dumps(model_job_values(definition, {"mode": mode}, RUN_ID))
        args = ("-f", "-", *args)
    result = subprocess.run(
        [HELM, "template", "example-run", str(ROOT / "deploy/helm" / chart), *args],
        capture_output=True, text=True, check=True, input=values,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def test_storage_retains_pvc_and_only_bootstrap_gets_both_identities():
    docs = render("object-storage")
    assert {d["kind"] for d in docs} == {"Deployment", "Service", "PersistentVolumeClaim", "ConfigMap", "Job"}
    claim = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    deploy = next(d for d in docs if d["kind"] == "Deployment")
    assert deploy["spec"]["replicas"] == 1
    assert deploy["spec"]["strategy"]["type"] == "Recreate"
    job = next(d for d in docs if d["kind"] == "Job")
    assert job["metadata"]["annotations"]["helm.sh/hook"] == "post-install,post-upgrade"
    assert job["metadata"]["annotations"]["helm.sh/hook-delete-policy"] == "before-hook-creation"
    pod = job["spec"]["template"]["spec"]
    assert not pod["automountServiceAccountToken"]
    refs = {e["valueFrom"]["secretKeyRef"]["name"] for e in pod["containers"][0]["env"] if "valueFrom" in e}
    assert refs == {"mic3-aistor-credentials", "mic3-artifact-uploader-credentials"}
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    policy = json.loads(cm["data"]["upload-policy.json"])
    actions = {a for s in policy["Statement"] for a in s["Action"]}
    assert "s3:PutObject" in actions and "s3:GetBucketLocation" in actions
    assert not any("Delete" in a or a.startswith("admin:") or a == "s3:*" for a in actions)
    assert all("mic3-artifacts" in r for s in policy["Statement"] for r in s["Resource"])


@pytest.mark.parametrize("mode", list(CONFIG["execution_definition"]["modes"]))
def test_model_copy_is_sequential_isolated_and_not_a_helm_release_hook(mode):
    docs = render("model-run", mode=mode)
    job, = docs
    assert job["kind"] == "Job"
    assert not job["metadata"].get("annotations")
    assert job["spec"]["backoffLimit"] == 0
    assert "ttlSecondsAfterFinished" not in job["spec"]
    pod = job["spec"]["template"]["spec"]
    model, = pod["initContainers"]
    copy, = pod["containers"]
    invocation = CONFIG["execution_definition"]["modes"][mode]
    assert model["command"] == invocation["arguments"]
    assert model["workingDir"] == invocation["working_directory"]
    assert model["volumeMounts"][0]["mountPath"] == invocation["output_directory"]
    env = {e["name"]: e.get("value") for e in copy["env"]}
    assert env["MODEL_IMAGE_SHA"] == CONFIG["image_digest"].split("@sha256:")[1]
    assert env["RUN_ID"] == str(RUN_ID)
    assert not {"JOB_NAME", "POD_UID", "MODEL_MODE"} & env.keys()
    assert not model.get("env") and not model.get("envFrom")
    assert copy["volumeMounts"][0]["readOnly"] is True
    assert all("persistentVolumeClaim" not in v for v in pod["volumes"])
    assert 'mc cp --recursive' in copy["args"][0]
    assert '$MODEL_ID/sha256-$MODEL_IMAGE_SHA/$RUN_ID/outputs/' in copy["args"][0]
    refs = {e["valueFrom"]["secretKeyRef"]["name"] for e in copy["env"] if "secretKeyRef" in e.get("valueFrom", {})}
    assert refs == {"mic3-artifact-uploader-credentials"}
    assert copy["securityContext"]["readOnlyRootFilesystem"] is True


def test_configurable_bucket_and_deadline():
    cm = next(d for d in render("object-storage", "--set", "bucket=other-artifacts") if d["kind"] == "ConfigMap")
    assert "mic3-artifacts" not in cm["data"]["upload-policy.json"]
    job, = render("model-run", "--set", "activeDeadlineSeconds=3600")
    assert job["spec"]["activeDeadlineSeconds"] == 3600


@pytest.mark.parametrize("override", ["model.mode=../escape", "model.image=example:latest", "runId=../escape", "runId="])
def test_invalid_run_values_are_rejected(override):
    with pytest.raises(subprocess.CalledProcessError):
        render("model-run", "--set", override)


def test_image_versions_have_separate_cleanup_prefixes():
    for digest in ("a" * 64, "b" * 64):
        job, = render("model-run", "--set", "model.image=example/model@sha256:" + digest)
        copy = job["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e.get("value") for e in copy["env"]}
        assert env["MODEL_IMAGE_SHA"] == digest
        assert "$MODEL_ID/sha256-$MODEL_IMAGE_SHA/" in copy["args"][0]
