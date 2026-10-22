"""Atomic submission persistence and operator inspection of durable records."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ModelError
from mic3_api.application.runs.submission import QueuedRun
from mic3_api.infrastructure.persistence.execution import OutboxEvent, Run, RunArtifact
from mic3_api.infrastructure.persistence.users import User


class SqlAlchemyRunSubmissions:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_requested(self, run: QueuedRun) -> None:
        with self._session.begin():
            if run.requested_by is not None and self._session.get(User, run.requested_by) is None:
                raise ModelError("The requested_by MIC3 user does not exist.")
            self._session.add(Run(**asdict(run)))
            self._session.flush()
            self._session.add(OutboxEvent(run_id=run.id, event_type="run.requested"))
            self._session.flush()

    def show(self, run_id: UUID) -> dict:
        with self._session.begin():
            run = self._session.get(Run, run_id)
            if run is None:
                raise ModelError("Run does not exist.")
            events = self._session.scalars(select(OutboxEvent).where(
                OutboxEvent.run_id == run_id,
            ).order_by(OutboxEvent.created_at, OutboxEvent.id))
            return {
                "id": run.id, "model_release_id": run.model_release_id,
                "requested_by": run.requested_by, "parameters": run.parameters,
                "fingerprint": run.fingerprint, "status": run.status, "created_at": run.created_at,
                "accepted_at": run.accepted_at, "failure_code": run.failure_code,
                "dispatch_started_at": run.dispatch_started_at, "job_name": f"mic3-run-{run.id}",
                "job_namespace": run.job_namespace, "job_uid": run.job_uid, "finished_at": run.finished_at,
                "artifacts": [{"bucket": a.bucket, "object_key": a.object_key, "size_bytes": a.size_bytes}
                              for a in self._session.scalars(select(RunArtifact).where(RunArtifact.run_id == run_id).order_by(RunArtifact.object_key))],
                "events": [{"id": event.id, "event_type": event.event_type,
                            "created_at": event.created_at, "published_at": event.published_at}
                           for event in events],
            }
