"""Authorized, materialized run snapshots shared by HTTP and streaming."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from mic3_api.application.runs.errors import RunAccessDenied, RunNotFound
from mic3_api.application.runs.status import RunStatus
from mic3_api.application.users import CurrentUser
from mic3_api.application.users.roles import ADMIN_ROLE

__all__ = ["GetRun", "ListOwnRuns", "RunAccessDenied", "RunDetail", "RunFile", "RunNotFound",
           "RunQueries", "RunStatus", "RunSummary"]


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
    def __init__(self, store: RunQueries) -> None:
        self._store = store

    def execute(self, user: CurrentUser, run_id: UUID) -> RunDetail:
        run = self._store.find(run_id)
        if run is None:
            raise RunNotFound("Run does not exist.")
        if run.requested_by != user.id and ADMIN_ROLE not in user.roles:
            raise RunAccessDenied("You cannot access this run.")
        return run


class ListOwnRuns:
    def __init__(self, store: RunQueries) -> None:
        self._store = store

    def execute(self, user: CurrentUser, *, limit: int, offset: int) -> list[RunSummary]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination.")
        return self._store.list_owned(user.id, limit, offset)
