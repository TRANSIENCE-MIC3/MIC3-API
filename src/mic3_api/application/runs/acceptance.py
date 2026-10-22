"""Accept existing runs without coupling their rules to Kafka or persistence."""

from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from mic3_api.application.models.catalog import ModelError, ReleaseDefinition
from mic3_api.application.runs.fingerprint import fingerprint


class RunNotFound(ModelError):
    """The requested run has no durable submission."""


@dataclass(frozen=True)
class AcceptanceResult:
    run: "RunForAcceptance"
    outcome: str


@dataclass(frozen=True)
class RunForAcceptance:
    id: UUID
    parameters: dict
    fingerprint: str
    status: str
    accepted_at: datetime | None
    failure_code: str | None


class AcceptanceTransaction(Protocol):
    @property
    def run(self) -> RunForAcceptance: ...

    def load_release(self) -> ReleaseDefinition: ...

    def save_outcome(self, run: RunForAcceptance) -> None: ...


class RunAcceptanceStore(Protocol):
    def transaction(self, run_id: UUID) -> AbstractContextManager[AcceptanceTransaction]:
        """Lock release before run; commit on success and roll back on error."""
        ...


class AcceptRun:
    def __init__(self, store: RunAcceptanceStore) -> None:
        self._store = store

    def execute(self, run_id: UUID) -> RunForAcceptance:
        return self.execute_with_outcome(run_id).run

    def execute_with_outcome(self, run_id: UUID) -> AcceptanceResult:
        with self._store.transaction(run_id) as transaction:
            run = transaction.run
            if run.accepted_at is not None or run.status != "queued":
                return AcceptanceResult(run, "unchanged")
            release = transaction.load_release()
            try:
                compatible = fingerprint(release, run.parameters) == run.fingerprint
            except ModelError:
                # The selected mode may have been removed before acceptance.
                compatible = False
            if compatible:
                outcome = replace(run, accepted_at=datetime.now(timezone.utc))
            else:
                outcome = replace(run, status="failed", failure_code="execution_definition_changed")
            transaction.save_outcome(outcome)
        return AcceptanceResult(outcome, "accepted" if compatible else "failed-run")
