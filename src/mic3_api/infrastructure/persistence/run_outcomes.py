"""Shared atomic terminal writes, always inside the caller's transaction."""
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mic3_api.application.runs.execution import Artifact
from mic3_api.application.runs.status import TERMINAL_STATUSES, RunStatus
from mic3_api.infrastructure.persistence.execution import OutboxEvent, Run, RunArtifact


def finish_row(
    session: Session, row: Run, failure_code: str | None,
    artifacts: tuple[Artifact, ...] = (),
) -> None:
    if row.status in TERMINAL_STATUSES:
        return
    row.status = RunStatus.FAILED if failure_code else RunStatus.SUCCEEDED
    row.failure_code = failure_code
    row.finished_at = datetime.now(timezone.utc)
    for artifact in artifacts:
        session.add(RunArtifact(run_id=row.id, **asdict(artifact)))
    session.add(OutboxEvent(run_id=row.id, event_type=f"run.{row.status}"))
