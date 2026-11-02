"""Row-to-application conversions shared by every persistence adapter."""
from collections.abc import Iterable
from dataclasses import fields
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.models.execution import ExecutionDefinition
from mic3_api.application.runs.queries import RunDetail, RunFile, RunSummary
from mic3_api.infrastructure.persistence.execution import Model, ModelReleaseRow, Run, RunArtifact


def release_definition(model: Model, release: ModelReleaseRow) -> ReleaseDefinition:
    return ReleaseDefinition(model.id, model.display_name, release.image_digest,
                             ExecutionDefinition.from_dict(release.execution_definition))


def load_release_definition(session: Session, release: ModelReleaseRow) -> ReleaseDefinition:
    return release_definition(session.get(Model, release.model_id), release)


def run_summary(run: Run) -> RunSummary:
    return RunSummary(**_summary_values(run))


def run_detail(run: Run, artifacts: Iterable[RunArtifact]) -> RunDetail:
    """Expose file names only; buckets and full object keys stay internal."""
    files = tuple(RunFile(PurePosixPath(a.object_key).name, a.size_bytes) for a in artifacts)
    return RunDetail(**_summary_values(run), artifacts=files)


def _summary_values(run: Run) -> dict:
    return {field.name: getattr(run, field.name) for field in fields(RunSummary)}
