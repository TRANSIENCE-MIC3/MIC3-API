"""Create or adopt one deterministic Job and inspect its terminal state."""
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from mic3_api.application.runs.execution import ExecutionFailure, InfrastructureUnavailable, JobObservation


def contains(actual, expected):
    """Admission may add defaults, but must not alter our execution contract."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(contains(a, b) for a, b in zip(actual, expected))
    return actual == expected


class KubernetesJobs:
    def __init__(self, api_client, renderer, namespace):
        self.client, self.renderer, self.namespace = api_client, renderer, namespace
        self.batch, self.core = client.BatchV1Api(api_client), client.CoreV1Api(api_client)

    @classmethod
    def connect(cls, kubeconfig, context, renderer, namespace):
        configuration = client.Configuration()
        config.load_kube_config(config_file=str(kubeconfig), context=context, client_configuration=configuration)
        configuration.retries = 0
        return cls(client.ApiClient(configuration), renderer, namespace)

    def close(self):
        self.client.close()

    def _get(self, work):
        try:
            job = self.batch.read_namespaced_job(work.job_name, work.namespace, _request_timeout=(3, 10))
            return self.client.sanitize_for_serialization(job)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def observe_or_create(self, work):
        if work.namespace != self.namespace:
            raise InfrastructureUnavailable("Execution namespace changed; restore its configuration.")
        expected = self.renderer.render(work)
        try:
            actual = self._get(work)
            if actual is None:
                if work.job_uid:
                    raise ExecutionFailure("job_lost")
                try:
                    job = self.batch.create_namespaced_job(work.namespace, expected, _request_timeout=(3, 10))
                    actual = self.client.sanitize_for_serialization(job)
                except (ApiException, HTTPError, TimeoutError, OSError):
                    actual = self._get(work)
                    if actual is None:
                        raise InfrastructureUnavailable("Job creation unconfirmed; retry deterministic lookup.")
            uid = actual["metadata"]["uid"]
            if work.job_uid and uid != work.job_uid:
                raise ExecutionFailure("job_identity_conflict")
            if not contains(actual["metadata"].get("labels", {}), expected["metadata"]["labels"]) or not contains(
                actual["metadata"].get("annotations", {}), expected["metadata"]["annotations"]
            ) or not contains(actual["spec"], expected["spec"]):
                raise ExecutionFailure("job_identity_conflict")
            conditions = {c["type"] for c in actual.get("status", {}).get("conditions", []) if c["status"] == "True"}
            if "Failed" in conditions:
                reason = "job_failed"
                pods = self.core.list_namespaced_pod(work.namespace, label_selector=f"job-name={work.job_name}", _request_timeout=(3, 10))
                for pod in self.client.sanitize_for_serialization(pods).get("items", []):
                    if not any(o.get("uid") == uid for o in pod["metadata"].get("ownerReferences", [])):
                        continue
                    status = pod.get("status", {})
                    for container in status.get("initContainerStatuses", []) + status.get("containerStatuses", []):
                        exit_code = container.get("state", {}).get("terminated", {}).get("exitCode")
                        if exit_code not in (None, 0):
                            reason = {"model": "model_failed", "copy": "upload_failed"}.get(container["name"], "job_failed")
                return JobObservation(uid, "failed", reason)
            return JobObservation(uid, "succeeded" if "Complete" in conditions else "active")
        except (ApiException, HTTPError, TimeoutError, OSError) as exc:
            raise InfrastructureUnavailable("Kubernetes operation unavailable.") from exc
