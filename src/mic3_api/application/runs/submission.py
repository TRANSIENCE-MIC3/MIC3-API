"""Prepare and persist a queued run, independently of HTTP and messaging."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from mic3_api.application.models.catalog import Catalog
from mic3_api.application.runs.fingerprint import fingerprint
from mic3_api.application.users import CurrentUser


@dataclass(frozen=True)
class QueuedRun:
    id: UUID
    model_release_id: UUID
    requested_by: UUID | None
    parameters: dict
    fingerprint: str
    status: str = "queued"


class RunSubmissionStore(Protocol):
    def save_requested(self, run: QueuedRun) -> None:
        """Commit run and event together, or roll back both."""
        ...


class SubmitRun:
    def __init__(self, catalog: Catalog, store: RunSubmissionStore) -> None:
        self._catalog = catalog
        self._store = store

    def execute(self, release_id: UUID, parameters: object,
                requested_by: UUID | None = None) -> QueuedRun:
        release = self._catalog.get_release(release_id)
        effective = release.definition.execution_definition.prepare(parameters)
        run = QueuedRun(uuid4(), release.id, requested_by, effective,
                        fingerprint(release.definition, effective))
        self._store.save_requested(run)
        return run


class SubmitOwnRun:
    """Derive ownership only from the resolved, active MIC3 identity."""

    def __init__(self, submit: SubmitRun):
        self._submit = submit

    def execute(self, user: CurrentUser, release_id: UUID, parameters: object) -> QueuedRun:
        return self._submit.execute(release_id, parameters, requested_by=user.id)
