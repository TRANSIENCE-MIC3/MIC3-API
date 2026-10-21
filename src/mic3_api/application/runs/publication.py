"""Publish committed outbox events; repeated delivery is intentional."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class RunEvent:
    id: UUID
    run_id: UUID
    event_type: str
    created_at: datetime


class PublicationError(Exception):
    """Delivery was not confirmed; retain the event for another publication."""


class EventPublisher(Protocol):
    def publish(self, event: RunEvent) -> None:
        """Return only after acknowledgement, otherwise raise PublicationError."""
        ...


class OutboxBatch(Protocol):
    @property
    def events(self) -> Sequence[RunEvent]: ...

    def mark_published(self, event_id: UUID) -> None: ...


class OutboxStore(Protocol):
    def claim(self, limit: int) -> AbstractContextManager[OutboxBatch]:
        """Lock committed unpublished events; commit on success, roll back on error."""
        ...


class PublishOutbox:
    def __init__(self, store: OutboxStore, publisher: EventPublisher) -> None:
        self._store = store
        self._publisher = publisher

    def execute(self, limit: int = 10) -> int:
        if not 1 <= limit <= 100:
            raise ValueError("Outbox batch size must be between 1 and 100.")
        with self._store.claim(limit) as batch:
            for event in batch.events:
                self._publisher.publish(event)
                batch.mark_published(event.id)
            count = len(batch.events)
        # A failed database commit must never be reported as published.
        return count
