"""Short, consistent database reads with no live ORM objects escaping."""
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mic3_api.application.runs.queries import RunDetail, RunSummary
from mic3_api.infrastructure.persistence.execution import Run, RunArtifact
from mic3_api.infrastructure.persistence.mappers import run_detail, run_summary


class SqlAlchemyRunQueries:
    def __init__(self, session: Session, *, statement_timeout_ms: int | None = None) -> None:
        self._session = session
        self._statement_timeout_ms = statement_timeout_ms

    def find(self, run_id: UUID) -> RunDetail | None:
        with self._session.begin():
            if self._statement_timeout_ms is not None:
                self._session.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                                      {"timeout": str(self._statement_timeout_ms)})
            # One statement observes state and artifacts at the same MVCC snapshot.
            rows = self._session.execute(
                select(Run, RunArtifact).outerjoin(RunArtifact, RunArtifact.run_id == Run.id)
                .where(Run.id == run_id).order_by(RunArtifact.object_key)
                .execution_options(populate_existing=True)
            ).all()
            if not rows:
                return None
            return run_detail(rows[0][0], (artifact for _, artifact in rows if artifact is not None))

    def list_owned(self, user_id: UUID, limit: int, offset: int) -> list[RunSummary]:
        with self._session.begin():
            return [run_summary(run) for run in self._session.scalars(
                select(Run).where(Run.requested_by == user_id)
                .order_by(Run.created_at.desc(), Run.id.desc()).limit(limit).offset(offset)
                .execution_options(populate_existing=True)
            )]
