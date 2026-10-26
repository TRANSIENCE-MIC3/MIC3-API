"""Authorized, materialized run snapshots shared by HTTP and future streaming."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from mic3_api.application.users import CurrentUser

RunStatus = Literal["queued", "running", "succeeded", "failed"]


class RunNotFound(Exception):
    pass


class RunAccessDenied(Exception):
    pass


@dataclass(frozen=True)
class RunSummary:
    id: UUID
    model_release_id: UUID
    requested_by: UUID | None
    parameters: dict
    status: RunStatus
    created_at: datetime
    accepted_at: datetime | None
    dispatch_started_at: datetime | None
    finished_at: datetime | None
    failure_code: str | None


@dataclass(frozen=True)
class RunFile:
    name: str
    size_bytes: int


@dataclass(frozen=True)
class RunDetail(RunSummary):
    artifacts: tuple[RunFile, ...]


class RunQueries(Protocol):
    def find(self, run_id: UUID) -> RunDetail | None: ...
    def list_owned(self, user_id: UUID, limit: int, offset: int) -> list[RunSummary]: ...


class GetRun:
    def execute(self, user: CurrentUser, store: RunQueries, run_id: UUID) -> RunDetail:
        run = store.find(run_id)
        if run is None:
            raise RunNotFound("Run does not exist.")
        if run.requested_by != user.id and "admin" not in user.roles:
            raise RunAccessDenied("You cannot access this run.")
        return run


class ListOwnRuns:
    def execute(self, user: CurrentUser, store: RunQueries, *, limit: int, offset: int) -> list[RunSummary]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination.")
        return store.list_owned(user.id, limit, offset)
