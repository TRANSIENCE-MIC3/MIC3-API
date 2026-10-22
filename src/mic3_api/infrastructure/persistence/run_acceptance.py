"""Acceptance transactions coordinated with catalog registration."""

from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.models.execution import ExecutionDefinition
from mic3_api.application.runs.acceptance import RunForAcceptance, RunNotFound
from mic3_api.infrastructure.persistence.execution import Model, ModelReleaseRow, Run


class SqlAlchemyAcceptanceTransaction:
    def __init__(self, session: Session, row: Run, release: ModelReleaseRow) -> None:
        self._session, self._row, self._release = session, row, release
        self.run = RunForAcceptance(row.id, row.parameters, row.fingerprint, row.status,
                                    row.accepted_at, row.failure_code)

    def load_release(self) -> ReleaseDefinition:
        model = self._session.get(Model, self._release.model_id)
        return ReleaseDefinition(model.id, model.display_name, self._release.image_digest,
                                 ExecutionDefinition.from_dict(self._release.execution_definition))

    def save_outcome(self, run: RunForAcceptance) -> None:
        self._row.status = run.status
        self._row.accepted_at = run.accepted_at
        self._row.failure_code = run.failure_code


class SqlAlchemyRunAcceptance:
    def __init__(self, session: Session) -> None:
        self._session = session

    @contextmanager
    def transaction(self, run_id: UUID):
        with self._session.begin():
            release_id = self._session.scalar(select(Run.model_release_id).where(Run.id == run_id))
            if release_id is None:
                raise RunNotFound("Run does not exist.")
            # Registration takes this same lock before checking protected modes.
            release = self._session.scalar(select(ModelReleaseRow).where(
                ModelReleaseRow.id == release_id,
            ).with_for_update().execution_options(populate_existing=True))
            row = self._session.scalar(select(Run).where(Run.id == run_id)
                                       .with_for_update().execution_options(populate_existing=True))
            if row is None:
                raise RunNotFound("Run does not exist.")
            yield SqlAlchemyAcceptanceTransaction(self._session, row, release)
