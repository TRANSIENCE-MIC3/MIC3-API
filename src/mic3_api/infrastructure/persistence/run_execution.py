"""PostgreSQL owns capacity, reconciliation locks and atomic terminal outcomes."""
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ModelError, ReleaseDefinition
from mic3_api.application.models.execution import ExecutionDefinition
from mic3_api.application.runs.execution import Artifact, ExecutionFailure, ExecutionWork
from mic3_api.application.runs.fingerprint import fingerprint
from mic3_api.infrastructure.persistence.execution import Model, ModelReleaseRow, Run
from mic3_api.infrastructure.persistence.run_outcomes import finish_row

CAPACITY_LOCK = 7523190401


def definition(session, release):
    model = session.get(Model, release.model_id)
    return ReleaseDefinition(model.id, model.display_name, release.image_digest,
                             ExecutionDefinition.from_dict(release.execution_definition))


class SqlAlchemyExecutionRecord:
    def __init__(self, session, run_id):
        self.session, self.run_id = session, run_id

    def load(self):
        with self.session.begin():
            row = self.session.get(Run, self.run_id, populate_existing=True)
            if row is None or row.status != "running":
                return None
            release = self.session.get(ModelReleaseRow, row.model_release_id)
            return ExecutionWork(row.id, definition(self.session, release), row.parameters,
                                 row.fingerprint, row.job_namespace, row.job_uid)

    def save_job_uid(self, uid):
        with self.session.begin():
            row = self.session.scalar(select(Run).where(Run.id == self.run_id).with_for_update()
                                      .execution_options(populate_existing=True))
            if row.job_uid not in (None, uid):
                raise ExecutionFailure("job_identity_conflict")
            if row.status == "running":
                row.job_uid = uid

    def finish(self, failure_code, artifacts=()):
        with self.session.begin():
            row = self.session.scalar(select(Run).where(Run.id == self.run_id).with_for_update()
                                      .execution_options(populate_existing=True))
            finish_row(self.session, row, failure_code, artifacts)


class SqlAlchemyExecutions:
    def __init__(self, engine):
        self.engine = engine

    def running_ids(self):
        with Session(self.engine) as session:
            return list(session.scalars(select(Run.id).where(Run.status == "running")
                                        .order_by(Run.created_at, Run.id)))

    def claim_next(self, namespace, capacity):
        with Session(self.engine) as session, session.begin():
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CAPACITY_LOCK})
            if session.scalar(select(func.count()).select_from(Run).where(Run.status == "running")) >= capacity:
                return None
            candidate = session.execute(select(Run.id, Run.model_release_id).where(
                Run.status == "queued", Run.accepted_at.is_not(None)).order_by(Run.created_at, Run.id).limit(1)).first()
            if candidate is None:
                return None
            release = session.scalar(select(ModelReleaseRow).where(ModelReleaseRow.id == candidate.model_release_id).with_for_update())
            row = session.scalar(select(Run).where(Run.id == candidate.id).with_for_update())
            if row.status != "queued" or row.accepted_at is None:
                return None
            try:
                compatible = fingerprint(definition(session, release), row.parameters) == row.fingerprint
            except ModelError:
                compatible = False
            if not compatible:
                finish_row(session, row, "execution_definition_changed")
                return None
            row.status = "running"
            row.dispatch_started_at = datetime.now(timezone.utc)
            row.job_namespace = namespace
            return row.id

    @contextmanager
    def locked(self, run_id: UUID):
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
