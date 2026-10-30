"""Short-lived queries for a personal live feed; no streaming sessions."""
from sqlalchemy import or_, select, text

from mic3_api.infrastructure.persistence.execution import Run, RunArtifact
from mic3_api.infrastructure.persistence.users import User
from mic3_api.infrastructure.persistence.run_queries import SqlAlchemyRunQueries, summary_values
from mic3_api.application.runs.queries import RunDetail, RunFile
from pathlib import PurePosixPath


class SqlAlchemyRunFeed:
    def __init__(self, database):
        self.database = database

    def find(self, run_id):
        with self.database.open_session() as session:
            return SqlAlchemyRunQueries(session, statement_timeout_ms=5000).find(run_id)

    def is_active(self, user_id):
        with self.database.open_session() as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            return session.scalar(select(User.is_active).where(User.id == user_id)) is True

    def snapshot(self, user_id):
        with self.database.open_session() as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            terminal = select(Run.id).where(
                Run.requested_by == user_id, Run.status.in_(("succeeded", "failed")),
            ).order_by(Run.created_at.desc(), Run.id.desc()).limit(50)
            rows = session.execute(
                select(Run, RunArtifact).outerjoin(RunArtifact, RunArtifact.run_id == Run.id)
                .where(Run.requested_by == user_id,
                       or_(Run.status.in_(("queued", "running")), Run.id.in_(terminal)))
                .order_by(Run.created_at.desc(), Run.id.desc(), RunArtifact.object_key)
            ).all()
            runs, artifacts = {}, {}
            for run, artifact in rows:
                runs[run.id] = run
                artifacts.setdefault(run.id, [])
                if artifact is not None:
                    artifacts[run.id].append(RunFile(PurePosixPath(artifact.object_key).name, artifact.size_bytes))
            return [RunDetail(**summary_values(run), artifacts=tuple(artifacts[run.id])) for run in runs.values()]
