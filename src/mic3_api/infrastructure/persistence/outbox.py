"""Short bounded publication batches with PostgreSQL row-lock ownership."""

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mic3_api.application.runs.publication import RunEvent
from mic3_api.infrastructure.persistence.execution import OutboxEvent


class SqlAlchemyOutboxBatch:
    def __init__(self, rows: list[OutboxEvent]) -> None:
        self._rows = {row.id: row for row in rows}
        self.events = tuple(
            RunEvent(row.id, row.run_id, row.event_type, row.created_at) for row in rows
        )

    def mark_published(self, event_id: UUID) -> None:
        self._rows[event_id].published_at = datetime.now(timezone.utc)


class SqlAlchemyOutbox:
    def __init__(self, session: Session) -> None:
        self._session = session

    @contextmanager
    def claim(self, limit: int):
        with self._session.begin():
            rows = list(self._session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ))
            yield SqlAlchemyOutboxBatch(rows)
