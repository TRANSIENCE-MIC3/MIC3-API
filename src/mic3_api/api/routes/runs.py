"""Authenticated submission and read-only run history."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from mic3_api.api.dependencies import get_current_user
from mic3_api.api.dependencies.use_cases import get_get_run, get_list_own_runs, get_submit_run
from mic3_api.api.schemas.runs import (
    RunDetailResponse, RunListResponse, RunSummaryResponse, SubmitRunRequest, SubmittedRunResponse,
)
from mic3_api.application.runs.queries import GetRun, ListOwnRuns
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.application.users import CurrentUser

router = APIRouter(prefix="/runs", tags=["runs"], responses={
    401: {"description": "A valid bearer token is required."},
    403: {"description": "The account is inactive or access is denied."},
    503: {"description": "Authentication or database access is unavailable."},
})
UserDependency = Annotated[CurrentUser, Depends(get_current_user)]


@router.post("", status_code=202, response_model=SubmittedRunResponse, responses={
    404: {"description": "The model release does not exist."},
    429: {"description": "An outstanding run admission limit has been reached."},
})
def submit_run(body: SubmitRunRequest, response: Response, current_user: UserDependency,
               submit: Annotated[SubmitRun, Depends(get_submit_run)]) -> SubmittedRunResponse:
    """Commit queued work; execution proceeds asynchronously through the outbox."""
    # Ownership comes only from the resolved, active identity, never from the body.
    run = submit.execute(body.model_release_id, body.parameters, requested_by=current_user.id)
    response.headers["Location"] = f"/runs/{run.id}"
    return SubmittedRunResponse(id=run.id, model_release_id=run.model_release_id,
                                parameters=run.parameters, status=str(run.status))


@router.get("", response_model=RunListResponse)
def list_runs(current_user: UserDependency, list_own: Annotated[ListOwnRuns, Depends(get_list_own_runs)],
              limit: Annotated[int, Query(ge=1, le=100)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0) -> RunListResponse:
    """List only the caller's runs, including when the caller is an admin."""
    runs = list_own.execute(current_user, limit=limit, offset=offset)
    return RunListResponse(items=[RunSummaryResponse.from_summary(run) for run in runs],
                           limit=limit, offset=offset)


@router.get("/{run_id}", response_model=RunDetailResponse, responses={
    404: {"description": "The run does not exist."},
})
def get_run(run_id: UUID, current_user: UserDependency,
            get: Annotated[GetRun, Depends(get_get_run)]) -> RunDetailResponse:
    """Read lifecycle and recorded files as owner or admin; running means dispatch began."""
    return RunDetailResponse.from_detail(get.execute(current_user, run_id))
