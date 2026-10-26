"""Authenticated submission and read-only run history."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mic3_api.api.dependencies import get_current_user
from mic3_api.api.dependencies.database import get_session
from mic3_api.application.models.errors import ModelError, ModelReleaseNotFound
from mic3_api.application.runs.admission import RunAdmissionExceeded, RunAdmissionLimits
from mic3_api.application.runs.queries import GetRun, ListOwnRuns, RunAccessDenied, RunNotFound, RunStatus
from mic3_api.application.runs.submission import SubmitOwnRun, SubmitRun
from mic3_api.application.users import CurrentUser
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_queries import SqlAlchemyRunQueries
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions

router = APIRouter(prefix="/runs", tags=["runs"], responses={
    401: {"description": "A valid bearer token is required."},
    403: {"description": "The account is inactive or access is denied."},
    503: {"description": "Authentication or database access is unavailable."},
})
UserDependency = Annotated[CurrentUser, Depends(get_current_user)]
SessionDependency = Annotated[Session, Depends(get_session)]


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


class RunFileResponse(BaseModel):
    name: str
    size_bytes: int


class RunDetailResponse(RunSummaryResponse):
    artifacts: list[RunFileResponse]


class RunListResponse(BaseModel):
    items: list[RunSummaryResponse]
    limit: int
    offset: int


@contextmanager
def run_http_errors():
    try:
        yield
    except (ModelReleaseNotFound, RunNotFound) as exc:
        raise HTTPException(404, str(exc)) from exc
    except RunAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    except RunAdmissionExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    except ModelError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Database access is unavailable.") from exc


@router.post("", status_code=202, response_model=SubmittedRunResponse, responses={
    404: {"description": "The model release does not exist."},
    429: {"description": "An outstanding run admission limit has been reached."},
})
def submit_run(body: SubmitRunRequest, request: Request, response: Response,
               current_user: UserDependency, session: SessionDependency) -> SubmittedRunResponse:
    """Commit queued work; execution proceeds asynchronously through the outbox."""
    settings = request.app.state.settings
    limits = RunAdmissionLimits(settings.run_max_active_per_user, settings.run_max_active_global)
    with run_http_errors():
        run = SubmitOwnRun(SubmitRun(
            SqlAlchemyModelCatalog(session), SqlAlchemyRunSubmissions(session, limits),
        )).execute(current_user, body.model_release_id, body.parameters)
    response.headers["Location"] = f"/runs/{run.id}"
    return SubmittedRunResponse(id=run.id, model_release_id=run.model_release_id,
                                parameters=run.parameters, status=run.status)


@router.get("", response_model=RunListResponse)
def list_runs(current_user: UserDependency, session: SessionDependency,
              limit: Annotated[int, Query(ge=1, le=100)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0) -> RunListResponse:
    """List only the caller's runs, including when the caller is an admin."""
    with run_http_errors():
        runs = ListOwnRuns().execute(current_user, SqlAlchemyRunQueries(session), limit=limit, offset=offset)
    return RunListResponse(items=[RunSummaryResponse(**asdict(run)) for run in runs], limit=limit, offset=offset)


@router.get("/{run_id}", response_model=RunDetailResponse, responses={
    404: {"description": "The run does not exist."},
})
def get_run(run_id: UUID, current_user: UserDependency, session: SessionDependency) -> RunDetailResponse:
    """Read lifecycle and recorded files as owner or admin; running means dispatch began."""
    with run_http_errors():
        run = GetRun().execute(current_user, SqlAlchemyRunQueries(session), run_id)
    return RunDetailResponse(**asdict(run))
