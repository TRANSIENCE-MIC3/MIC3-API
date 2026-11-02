"""Short-lived queries for a personal live feed; no streaming sessions."""
from uuid import UUID

from sqlalchemy import or_, select, text

from mic3_api.application.runs.queries import RunDetail
from mic3_api.application.runs.status import ACTIVE_STATUSES, TERMINAL_STATUSES
from mic3_api.infrastructure.database import DatabaseGateway
from mic3_api.infrastructure.persistence.execution import Run, RunArtifact
from mic3_api.infrastructure.persistence.mappers import run_detail
from mic3_api.infrastructure.persistence.run_queries import SqlAlchemyRunQueries
from mic3_api.infrastructure.persistence.users import User

SNAPSHOT_TERMINAL_RUNS = 50


class SqlAlchemyRunFeed:
    def __init__(self, database: DatabaseGateway) -> None:
        self.database = database

    def find(self, run_id: UUID) -> RunDetail | None:
        with self.database.open_session() as session:
            return SqlAlchemyRunQueries(session, statement_timeout_ms=5000).find(run_id)

    def is_active(self, user_id: UUID) -> bool:
        with self.database.open_session() as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            return session.scalar(select(User.is_active).where(User.id == user_id)) is True

    def snapshot(self, user_id: UUID) -> list[RunDetail]:
        with self.database.open_session() as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            terminal = select(Run.id).where(
                Run.requested_by == user_id, Run.status.in_(TERMINAL_STATUSES),
            ).order_by(Run.created_at.desc(), Run.id.desc()).limit(SNAPSHOT_TERMINAL_RUNS)
            rows = session.execute(
                select(Run, RunArtifact).outerjoin(RunArtifact, RunArtifact.run_id == Run.id)
                .where(Run.requested_by == user_id,
                       or_(Run.status.in_(ACTIVE_STATUSES), Run.id.in_(terminal)))
                .order_by(Run.created_at.desc(), Run.id.desc(), RunArtifact.object_key)
            ).all()
            runs: dict[UUID, Run] = {}
            artifacts: dict[UUID, list[RunArtifact]] = {}
            for run, artifact in rows:
                runs[run.id] = run
                artifacts.setdefault(run.id, [])
                if artifact is not None:
                    artifacts[run.id].append(artifact)
            return [run_detail(run, artifacts[run_id]) for run_id, run in runs.items()]
