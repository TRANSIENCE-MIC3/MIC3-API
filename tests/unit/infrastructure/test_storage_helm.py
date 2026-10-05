"""Render real charts and check storage/security boundaries without a cluster."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")
pytestmark = pytest.mark.skipif(not HELM, reason="Helm required; set HELM_BIN if not on PATH")


def render(chart, *args):
    result = subprocess.run(
        [HELM, "template", "example-run", str(ROOT / "deploy/helm" / chart), *args],
        capture_output=True, text=True, check=True,
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


def test_model_copy_is_sequential_isolated_and_not_a_helm_release_hook():
    docs = render("model-run", "-f", str(ROOT / "integrations/eu_mfa/values.yaml"))
    job, = docs
    assert job["kind"] == "Job"
    assert not job["metadata"].get("annotations")
    assert job["spec"]["backoffLimit"] == 0
    assert "ttlSecondsAfterFinished" not in job["spec"]
    pod = job["spec"]["template"]["spec"]
    model, = pod["initContainers"]
    copy, = pod["containers"]
    assert model["command"] == ["python", "eumfa_buildings.py"]
    assert not model.get("env") and not model.get("envFrom")
    assert copy["volumeMounts"][0]["readOnly"] is True
    assert all("persistentVolumeClaim" not in v for v in pod["volumes"])
    assert 'mc cp --recursive' in copy["args"][0]
    assert '$MODEL_ID/$MODEL_MODE/$JOB_NAME/$POD_UID/' in copy["args"][0]
    refs = {e["valueFrom"]["secretKeyRef"]["name"] for e in copy["env"] if "secretKeyRef" in e.get("valueFrom", {})}
    assert refs == {"mic3-artifact-uploader-credentials"}
    assert copy["securityContext"]["readOnlyRootFilesystem"] is True


def test_configurable_bucket_and_deadline():
    cm = next(d for d in render("object-storage", "--set", "bucket=other-artifacts") if d["kind"] == "ConfigMap")
    assert "mic3-artifacts" not in cm["data"]["upload-policy.json"]
    job, = render("model-run", "-f", str(ROOT / "integrations/eu_mfa/values.yaml"), "--set", "activeDeadlineSeconds=3600")
    assert job["spec"]["activeDeadlineSeconds"] == 3600


@pytest.mark.parametrize("override", ["model.mode=../escape", "model.image=example:latest"])
def test_invalid_run_values_are_rejected(override):
    with pytest.raises(subprocess.CalledProcessError):
        render("model-run", "-f", str(ROOT / "integrations/eu_mfa/values.yaml"), "--set", override)
