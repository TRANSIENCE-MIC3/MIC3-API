"""Create or adopt one deterministic Job and inspect its terminal state."""
from pathlib import Path
from typing import Any, Protocol

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from mic3_api.application.runs.execution import (
    ExecutionFailure, ExecutionWork, InfrastructureUnavailable, JobObservation,
)

REQUEST_TIMEOUT = (3, 10)
TRANSPORT_ERRORS = (ApiException, HTTPError, TimeoutError, OSError)
FAILURE_BY_CONTAINER = {"model": "model_failed", "copy": "upload_failed"}


class JobRenderer(Protocol):
    def render(self, work: ExecutionWork) -> dict: ...


def contains(actual: Any, expected: Any) -> bool:
    """Admission may add defaults, but must not alter our execution contract."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(contains(a, b) for a, b in zip(actual, expected)))
    return actual == expected


class KubernetesJobs:
    def __init__(self, api_client: client.ApiClient, renderer: JobRenderer, namespace: str) -> None:
        self.client = api_client
        self.renderer = renderer
        self.namespace = namespace
        self.batch = client.BatchV1Api(api_client)
        self.core = client.CoreV1Api(api_client)

    @classmethod
    def connect(cls, kubeconfig: Path | None, context: str | None, renderer: JobRenderer,
                namespace: str) -> "KubernetesJobs":
        configuration = client.Configuration()
        if kubeconfig is None and context is None:
            config.load_incluster_config(client_configuration=configuration)
        elif kubeconfig is not None and context is not None:
            config.load_kube_config(config_file=str(kubeconfig), context=context, client_configuration=configuration)
        else:
            raise ValueError("Kubeconfig and context must be provided together")
        configuration.retries = 0
        return cls(client.ApiClient(configuration), renderer, namespace)

    def close(self) -> None:
        self.client.close()

    def observe_or_create(self, work: ExecutionWork) -> JobObservation:
        if work.namespace != self.namespace:
            raise InfrastructureUnavailable("Execution namespace changed; restore its configuration.")
        expected = self.renderer.render(work)
        try:
            actual = self._find_or_create(work, expected)
            uid = actual["metadata"]["uid"]
            self._verify_contract(work, uid, actual, expected)
            conditions = {c["type"] for c in actual.get("status", {}).get("conditions", []) if c["status"] == "True"}
            if "Failed" in conditions:
                return JobObservation(uid, "failed", self._failure_reason(work, uid))
            return JobObservation(uid, "succeeded" if "Complete" in conditions else "active")
        except TRANSPORT_ERRORS as exc:
            raise InfrastructureUnavailable("Kubernetes operation unavailable.") from exc

    def _get(self, work: ExecutionWork) -> dict | None:
        try:
            job = self.batch.read_namespaced_job(work.job_name, work.namespace, _request_timeout=REQUEST_TIMEOUT)
            return self.client.sanitize_for_serialization(job)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def _find_or_create(self, work: ExecutionWork, expected: dict) -> dict:
        actual = self._get(work)
        if actual is not None:
            return actual
        if work.job_uid:
            raise ExecutionFailure("job_lost")  # It existed once; never recreate it.
        try:
            job = self.batch.create_namespaced_job(work.namespace, expected, _request_timeout=REQUEST_TIMEOUT)
            return self.client.sanitize_for_serialization(job)
        except TRANSPORT_ERRORS:
            # The create may have succeeded before the error; the name is deterministic.
            actual = self._get(work)
            if actual is None:
                raise InfrastructureUnavailable("Job creation unconfirmed; retry deterministic lookup.")
            return actual

    @staticmethod
    def _verify_contract(work: ExecutionWork, uid: str, actual: dict, expected: dict) -> None:
        if work.job_uid and uid != work.job_uid:
            raise ExecutionFailure("job_identity_conflict")
        metadata, wanted = actual["metadata"], expected["metadata"]
        if not (contains(metadata.get("labels", {}), wanted["labels"])
                and contains(metadata.get("annotations", {}), wanted["annotations"])
                and contains(actual["spec"], expected["spec"])):
            raise ExecutionFailure("job_identity_conflict")

    def _failure_reason(self, work: ExecutionWork, uid: str) -> str:
        reason = "job_failed"
        pods = self.core.list_namespaced_pod(work.namespace, label_selector=f"job-name={work.job_name}",
                                             _request_timeout=REQUEST_TIMEOUT)
        for pod in self.client.sanitize_for_serialization(pods).get("items", []):
            if not any(o.get("uid") == uid for o in pod["metadata"].get("ownerReferences", [])):
                continue
            status = pod.get("status", {})
            for container in status.get("initContainerStatuses", []) + status.get("containerStatuses", []):
                exit_code = container.get("state", {}).get("terminated", {}).get("exitCode")
                if exit_code not in (None, 0):
                    reason = FAILURE_BY_CONTAINER.get(container["name"], "job_failed")
        return reason
