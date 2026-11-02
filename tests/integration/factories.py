"""Shared builders for integration tests: catalog entries, runs, users and identities.

Test modules import from here instead of from each other.
"""
from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.models.catalog import ModelRelease, RegisterRelease
from mic3_api.application.runs.acceptance import AcceptRun, RunForAcceptance
from mic3_api.application.runs.submission import QueuedRun, SubmitRun
from mic3_api.application.users.administration import GrantAdmin
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_acceptance import SqlAlchemyRunAcceptance
from mic3_api.infrastructure.persistence.run_inspection import SqlAlchemyRunInspection
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from mic3_api.infrastructure.persistence.user_administration import SqlAlchemyUserAdministrationUnitOfWork
from tests.catalog_data import CONFIG, MODE, PARAMETERS

AUTH = {"Authorization": "Bearer valid"}
IDENTITY = ValidatedIdentity(
    issuer="https://issuer.example/realms/mic3",
    subject="Case-Sensitive-Subject",
    email="member@example.org",
    display_name="Member Name",
)


class StubTokenValidator:
    """Accepts any token as `identity`, or raises `error` when set; records tokens seen."""

    def __init__(self, identity: ValidatedIdentity = IDENTITY) -> None:
        self.identity = identity
        self.error: Exception | None = None
        self.tokens: list[str] = []

    def validate(self, access_token: str) -> ValidatedIdentity:
        self.tokens.append(access_token)
        if self.error is not None:
            raise self.error
        return self.identity


def changed_config(change: str = "command", **overrides) -> dict:
    """CONFIG with its main mode altered: "command", "outputs", "remove" or "none"."""
    config = deepcopy(CONFIG) | overrides
    modes = config["execution_definition"]["modes"]
    if change == "command":
        modes[MODE]["arguments"] = ["changed"]
    elif change == "outputs":
        modes[MODE]["required_outputs"] = ["result.csv"]
    elif change == "remove":
        modes["other"] = modes.pop(MODE)
    return config


def register(engine: Engine, config: dict | None = None) -> ModelRelease:
    with Session(engine) as session:
        return RegisterRelease(SqlAlchemyModelCatalog(session)).execute(config or CONFIG)


def submit(session: Session, release_id: UUID, requested_by: UUID | None = None,
           parameters: object = None) -> QueuedRun:
    return SubmitRun(SqlAlchemyModelCatalog(session), SqlAlchemyRunSubmissions(session)).execute(
        release_id, PARAMETERS if parameters is None else parameters, requested_by,
    )


def queued_run(engine: Engine, config: dict | None = None) -> QueuedRun:
    """Register (or re-register) a release and submit one unattributed run for it."""
    release = register(engine, config)
    with Session(engine) as session:
        return submit(session, release.id)


def accept(engine: Engine, run_id: UUID) -> RunForAcceptance:
    with Session(engine) as session:
        result = AcceptRun(SqlAlchemyRunAcceptance(session)).execute(run_id)
        assert not session.in_transaction()
        return result


def show(engine: Engine, run_id: UUID) -> dict:
    with Session(engine) as session:
        return SqlAlchemyRunInspection(session).show(run_id)


def grant(engine: Engine, user_id: UUID) -> bool:
    with Session(engine) as session:
        return GrantAdmin(SqlAlchemyUserAdministrationUnitOfWork(session)).execute(user_id)


def count(engine: Engine, model) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def assert_rejected(engine: Engine, statement, values=None) -> None:
    """The database refuses the write; nothing is committed."""
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(statement, values)
