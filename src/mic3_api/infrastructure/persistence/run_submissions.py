"""Atomic submission persistence with admission limits."""

from dataclasses import asdict

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mic3_api.application.runs.admission import RunAdmissionLimits
from mic3_api.application.runs.errors import RequesterNotFound
from mic3_api.application.runs.status import ACTIVE_STATUSES
from mic3_api.application.runs.submission import QueuedRun
from mic3_api.infrastructure.persistence.execution import OutboxEvent, Run
from mic3_api.infrastructure.persistence.users import User

ADMISSION_LOCK = 7523190402


class SqlAlchemyRunSubmissions:
    def __init__(self, session: Session, limits: RunAdmissionLimits | None = None) -> None:
        self._session = session
        self._limits = limits

    def save_requested(self, run: QueuedRun) -> None:
        with self._session.begin():
            if self._limits is not None:
                # Separate from execution capacity; held until both inserts commit.
                self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMISSION_LOCK})
                active = select(func.count()).select_from(Run).where(Run.status.in_(ACTIVE_STATUSES))
                total = self._session.scalar(active)
                owned = self._session.scalar(active.where(Run.requested_by == run.requested_by))
                self._limits.check(owned, total)
            if run.requested_by is not None and self._session.get(User, run.requested_by) is None:
                raise RequesterNotFound("The requested_by MIC3 user does not exist.")
            self._session.add(Run(**asdict(run)))
            self._session.flush()
            self._session.add(OutboxEvent(run_id=run.id, event_type="run.requested"))
            self._session.flush()
