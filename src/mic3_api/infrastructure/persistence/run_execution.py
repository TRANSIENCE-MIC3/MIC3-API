"""PostgreSQL owns capacity, reconciliation locks and atomic terminal outcomes."""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.runs.execution import Artifact, ExecutionFailure, ExecutionWork
from mic3_api.application.runs.status import RunStatus
from mic3_api.infrastructure.persistence.execution import ModelReleaseRow, Run
from mic3_api.infrastructure.persistence.mappers import load_release_definition
from mic3_api.infrastructure.persistence.run_outcomes import finish_row

CAPACITY_LOCK = 7523190401


def _locked_run(session: Session, run_id: UUID) -> Run:
    return session.scalar(select(Run).where(Run.id == run_id).with_for_update()
                          .execution_options(populate_existing=True))


class SqlAlchemyExecutionRecord:
    def __init__(self, session: Session, run_id: UUID) -> None:
        self.session = session
        self.run_id = run_id

    def load(self) -> ExecutionWork | None:
        with self.session.begin():
            row = self.session.get(Run, self.run_id, populate_existing=True)
            if row is None or row.status != RunStatus.RUNNING:
                return None
            release = self.session.get(ModelReleaseRow, row.model_release_id)
            return ExecutionWork(row.id, load_release_definition(self.session, release), row.parameters,
                                 row.fingerprint, row.job_namespace, row.job_uid)

    def save_job_uid(self, uid: str) -> None:
        with self.session.begin():
            row = _locked_run(self.session, self.run_id)
            if row.job_uid not in (None, uid):
                raise ExecutionFailure("job_identity_conflict")
            if row.status == RunStatus.RUNNING:
                row.job_uid = uid

    def finish(self, failure_code: str | None, artifacts: tuple[Artifact, ...] = ()) -> None:
        with self.session.begin():
            finish_row(self.session, _locked_run(self.session, self.run_id), failure_code, artifacts)


class SqlAlchemyClaim:
    """A locked queued run; the use case decides whether it starts or fails."""

    def __init__(self, session: Session, row: Run, release: ModelReleaseRow, namespace: str) -> None:
        self._session = session
        self._row = row
        self._release = release
        self._namespace = namespace

    @property
    def run_id(self) -> UUID:
        return self._row.id

    @property
    def parameters(self) -> dict:
        return self._row.parameters

    @property
    def fingerprint(self) -> str:
        return self._row.fingerprint

    def load_release(self) -> ReleaseDefinition:
        return load_release_definition(self._session, self._release)

    def start(self) -> None:
        self._row.status = RunStatus.RUNNING
        self._row.dispatch_started_at = datetime.now(timezone.utc)
        self._row.job_namespace = self._namespace

    def fail(self, failure_code: str) -> None:
        finish_row(self._session, self._row, failure_code)


class SqlAlchemyExecutions:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def running_ids(self) -> list[UUID]:
        with Session(self.engine) as session:
            return list(session.scalars(select(Run.id).where(Run.status == RunStatus.RUNNING)
                                        .order_by(Run.created_at, Run.id)))

    @contextmanager
    def claim(self, namespace: str, capacity: int) -> Iterator[SqlAlchemyClaim | None]:
        with Session(self.engine) as session, session.begin():
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CAPACITY_LOCK})
            running = select(func.count()).select_from(Run).where(Run.status == RunStatus.RUNNING)
            if session.scalar(running) >= capacity:
                yield None
                return
            candidate = session.execute(
                select(Run.id, Run.model_release_id)
                .where(Run.status == RunStatus.QUEUED, Run.accepted_at.is_not(None))
                .order_by(Run.created_at, Run.id).limit(1)
            ).first()
            if candidate is None:
                yield None
                return
            # Lock the release before the run, matching acceptance and registration.
            release = session.scalar(select(ModelReleaseRow)
                                     .where(ModelReleaseRow.id == candidate.model_release_id).with_for_update())
            row = session.scalar(select(Run).where(Run.id == candidate.id).with_for_update())
            if row.status != RunStatus.QUEUED or row.accepted_at is None:
                yield None
                return
            yield SqlAlchemyClaim(session, row, release, namespace)

    @contextmanager
    def locked(self, run_id: UUID) -> Iterator[SqlAlchemyExecutionRecord | None]:
        # Session-level lock survives short transactions while network work occurs.
        key = int.from_bytes(run_id.bytes[:8], "big", signed=True)
        with self.engine.connect() as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            connection.commit()
            if not acquired:
                yield None
                return
            try:
                with Session(connection) as session:
                    yield SqlAlchemyExecutionRecord(session, run_id)
            finally:
                # A disconnected connection must not be transparently reconnected
                # and used for subsequent writes under a lock it no longer owns.
                if not connection.invalidated:
                    # A commit failure can leave the DBAPI transaction open even
                    # after SQLAlchemy has deactivated its transaction object.
                    # Never let the unlock transaction commit those pending writes.
                    connection.connection.rollback()
                    connection.rollback()
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    connection.commit()
