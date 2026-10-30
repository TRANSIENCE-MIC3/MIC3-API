"""Wire use cases to their adapters for one request.

Routes depend on these providers, never on infrastructure. Tests can replace any
provider with app.dependency_overrides.
"""
from concurrent.futures import Executor
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from mic3_api.api.dependencies.database import get_session
from mic3_api.application.models.catalog import ListModels
from mic3_api.application.runs.admission import RunAdmissionLimits
from mic3_api.application.runs.feed import RunFeed
from mic3_api.application.runs.queries import GetRun, ListOwnRuns
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.application.users.administration import ListUsers
from mic3_api.core.config import Settings
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_queries import SqlAlchemyRunQueries
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from mic3_api.infrastructure.persistence.user_administration import SqlAlchemyUserAdministrationUnitOfWork

SessionDependency = Annotated[Session, Depends(get_session)]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_submit_run(session: SessionDependency,
                   settings: Annotated[Settings, Depends(get_settings)]) -> SubmitRun:
    limits = RunAdmissionLimits(settings.run_max_active_per_user, settings.run_max_active_global)
    return SubmitRun(SqlAlchemyModelCatalog(session), SqlAlchemyRunSubmissions(session, limits))


def get_get_run(session: SessionDependency) -> GetRun:
    return GetRun(SqlAlchemyRunQueries(session))


def get_list_own_runs(session: SessionDependency) -> ListOwnRuns:
    return ListOwnRuns(SqlAlchemyRunQueries(session))


def get_list_users(session: SessionDependency) -> ListUsers:
    return ListUsers(SqlAlchemyUserAdministrationUnitOfWork(session))


def get_list_models(session: SessionDependency) -> ListModels:
    return ListModels(SqlAlchemyModelCatalog(session))


def get_run_feed(request: Request) -> RunFeed:
    return request.app.state.run_feed


def get_snapshot_executor(request: Request) -> Executor:
    return request.app.state.run_feed_snapshots
