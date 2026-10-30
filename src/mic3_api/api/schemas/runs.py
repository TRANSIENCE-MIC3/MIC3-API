"""HTTP shapes for runs, shared by the REST routes and the SSE stream."""
from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mic3_api.application.runs.queries import RunDetail, RunSummary
from mic3_api.application.runs.status import RunStatus


class SubmitRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_release_id: UUID
    parameters: dict


class SubmittedRunResponse(BaseModel):
    id: UUID
    model_release_id: UUID
    parameters: dict
    status: Literal["queued"]


class RunSummaryResponse(BaseModel):
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

    @classmethod
    def from_summary(cls, run: RunSummary) -> "RunSummaryResponse":
        return cls(**{name: getattr(run, name) for name in cls.model_fields})


class RunFileResponse(BaseModel):
    name: str
    size_bytes: int


class RunDetailResponse(RunSummaryResponse):
    artifacts: list[RunFileResponse]

    @classmethod
    def from_detail(cls, run: RunDetail) -> "RunDetailResponse":
        return cls(**asdict(run))


class RunListResponse(BaseModel):
    items: list[RunSummaryResponse]
    limit: int
    offset: int
