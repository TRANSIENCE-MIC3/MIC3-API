"""Transport-independent coordination of decoded requests and durable rejection."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from mic3_api.application.runs.acceptance import AcceptRun
from mic3_api.application.runs.errors import RunNotFound


@dataclass(frozen=True)
class RecordLocation:
    topic: str
    partition: int
    offset: int


@dataclass(frozen=True)
class Rejection:
    code: str
    diagnostic: str


class RejectionStore(Protocol):
    def record(self, location: RecordLocation, rejection: Rejection) -> None:
        """Commit a rejection once, or raise without claiming it was saved."""
        ...


class ConsumeRequest:
    def __init__(self, acceptance: AcceptRun, rejections: RejectionStore) -> None:
        self._acceptance = acceptance
        self._rejections = rejections

    def execute(self, location: RecordLocation, request: UUID | Rejection) -> str:
        if isinstance(request, UUID):
            try:
                return self._acceptance.execute_with_outcome(request).outcome
            except RunNotFound:
                request = Rejection("run_not_found", "Run does not exist.")
        self._rejections.record(location, request)
        return "rejected"
