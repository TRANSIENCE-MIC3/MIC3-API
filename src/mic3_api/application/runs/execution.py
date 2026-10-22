"""Durable execution coordination; no Kubernetes, storage, or database imports."""
from contextlib import AbstractContextManager
import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.runs.fingerprint import fingerprint

LOG = logging.getLogger(__name__)


class InfrastructureUnavailable(Exception):
    """Keep work recoverable and retry an infrastructure operation later."""


class ExecutionFailure(Exception):
    def __init__(self, code: str, artifacts=()):
        self.code = code
        self.artifacts = artifacts
        super().__init__(code)


@dataclass(frozen=True)
class ExecutionWork:
    id: UUID
    release: ReleaseDefinition
    parameters: dict
    fingerprint: str
    namespace: str
    job_uid: str | None

    @property
    def job_name(self) -> str:
        return f"mic3-run-{self.id}"

    @property
    def output_prefix(self) -> str:
        sha = self.release.image_digest.split("@sha256:")[1]
        return f"{self.release.model_id}/sha256-{sha}/{self.id}/outputs/"


@dataclass(frozen=True)
class JobObservation:
    uid: str
    state: str  # active, succeeded, failed
    failure_code: str | None = None


@dataclass(frozen=True)
class Artifact:
    bucket: str
    object_key: str
    size_bytes: int


class ExecutionRecord(Protocol):
    def load(self) -> ExecutionWork | None: ...
    def save_job_uid(self, uid: str) -> None: ...
    def finish(self, failure_code: str | None, artifacts: tuple[Artifact, ...] = ()) -> None: ...


class ExecutionStore(Protocol):
    def running_ids(self) -> list[UUID]: ...
    def claim_next(self, namespace: str, capacity: int) -> UUID | None: ...
    def locked(self, run_id: UUID) -> AbstractContextManager[ExecutionRecord | None]: ...


class ModelJobs(Protocol):
    def observe_or_create(self, work: ExecutionWork) -> JobObservation: ...


class OutputCollector(Protocol):
    def collect(self, work: ExecutionWork) -> tuple[Artifact, ...]: ...


class ReconcileExecutions:
    def __init__(self, store: ExecutionStore, jobs: ModelJobs, outputs: OutputCollector,
                 namespace: str, capacity: int):
        self.store, self.jobs, self.outputs = store, jobs, outputs
        self.namespace, self.capacity = namespace, capacity

    def reconcile(self, run_id: UUID) -> None:
        with self.store.locked(run_id) as record:
            if record is None:
                return
            work = record.load()
            if work is None:
                return
            try:
                if fingerprint(work.release, work.parameters) != work.fingerprint:
                    raise ExecutionFailure("execution_definition_changed")
                observation = self.jobs.observe_or_create(work)
                record.save_job_uid(observation.uid)
                if observation.state == "failed":
                    record.finish(observation.failure_code or "job_failed")
                    LOG.info("Execution failed run=%s reason=%s", work.id, observation.failure_code)
                elif observation.state == "succeeded":
                    record.finish(None, self.outputs.collect(work))
                    LOG.info("Execution succeeded run=%s", work.id)
            except ExecutionFailure as exc:
                record.finish(exc.code, exc.artifacts)
                LOG.info("Execution failed run=%s reason=%s", work.id, exc.code)

    def tick(self) -> None:
        # Recover all existing work before allocating another slot.
        for run_id in self.store.running_ids():
            self.reconcile(run_id)
        run_id = self.store.claim_next(self.namespace, self.capacity)
        if run_id is not None:
            self.reconcile(run_id)
