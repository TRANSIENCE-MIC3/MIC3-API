"""Operator view of a run: internal fields, artifacts and outbox publication state."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mic3_api.application.runs.errors import RunNotFound
from mic3_api.infrastructure.persistence.execution import OutboxEvent, Run, RunArtifact


class SqlAlchemyRunInspection:
    def __init__(self, session: Session) -> None:
        self._session = session

    def show(self, run_id: UUID) -> dict:
        with self._session.begin():
            run = self._session.get(Run, run_id)
            if run is None:
                raise RunNotFound("Run does not exist.")
            artifacts = self._session.scalars(
                select(RunArtifact).where(RunArtifact.run_id == run_id).order_by(RunArtifact.object_key))
            events = self._session.scalars(
                select(OutboxEvent).where(OutboxEvent.run_id == run_id)
                .order_by(OutboxEvent.created_at, OutboxEvent.id))
            return {
                "id": run.id, "model_release_id": run.model_release_id,
                "requested_by": run.requested_by, "parameters": run.parameters,
                "fingerprint": run.fingerprint, "status": run.status, "created_at": run.created_at,
                "accepted_at": run.accepted_at, "failure_code": run.failure_code,
                "dispatch_started_at": run.dispatch_started_at, "job_name": f"mic3-run-{run.id}",
                "job_namespace": run.job_namespace, "job_uid": run.job_uid, "finished_at": run.finished_at,
                "artifacts": [{"bucket": a.bucket, "object_key": a.object_key, "size_bytes": a.size_bytes}
                              for a in artifacts],
                "events": [{"id": e.id, "event_type": e.event_type,
                            "created_at": e.created_at, "published_at": e.published_at}
                           for e in events],
            }
