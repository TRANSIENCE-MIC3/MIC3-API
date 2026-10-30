"""Editable development catalog; registration updates a model/image entry."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import (
    ExecutionDefinition, ModelRelease, ReleaseDefinition,
)
from mic3_api.application.models.catalog_changes import ensure_active_modes_unchanged
from mic3_api.application.models.errors import ModelReleaseNotFound
from mic3_api.application.runs.status import ACTIVE_STATUSES
from mic3_api.infrastructure.persistence.execution import Model, ModelReleaseRow, Run
from mic3_api.infrastructure.persistence.mappers import release_definition


class SqlAlchemyModelCatalog:
    def __init__(self, session: Session) -> None:
        self._session = session

    def register(self, definition: ReleaseDefinition) -> ModelRelease:
        with self._session.begin():
            self._session.execute(insert(Model).values(
                id=definition.model_id, display_name=definition.display_name,
            ).on_conflict_do_nothing(index_elements=["id"]))
            # Serialize registrations for a model, including concurrent first registrations.
            model = self._session.scalar(select(Model).where(
                Model.id == definition.model_id,
            ).with_for_update())
            model.display_name = definition.display_name
            execution = definition.execution_definition.to_dict()
            release = self._session.scalar(select(ModelReleaseRow).where(
                ModelReleaseRow.model_id == definition.model_id,
                ModelReleaseRow.image_digest == definition.image_digest,
            ).order_by(ModelReleaseRow.created_at, ModelReleaseRow.id).with_for_update()
                .execution_options(populate_existing=True))
            if release is None:
                release = ModelReleaseRow(
                    model_id=definition.model_id,
                    image_digest=definition.image_digest, execution_definition=execution,
                )
                self._session.add(release)
            else:
                active_modes = self._session.scalars(select(Run.parameters["mode"].astext).where(
                    Run.model_release_id == release.id,
                    Run.accepted_at.is_not(None), Run.status.in_(ACTIVE_STATUSES),
                ).distinct())
                ensure_active_modes_unchanged(
                    ExecutionDefinition.from_dict(release.execution_definition),
                    definition.execution_definition, active_modes,
                )
                release.execution_definition = execution
            self._session.flush()
            return self._snapshot(release, model)

    def get_release(self, release_id: UUID) -> ModelRelease:
        with self._session.begin():
            row = self._session.execute(select(ModelReleaseRow, Model).join(Model).where(
                ModelReleaseRow.id == release_id,
            )).one_or_none()
            if row is None:
                raise ModelReleaseNotFound("Model release does not exist.")
            return self._snapshot(*row)

    def list_releases(self) -> list[ModelRelease]:
        with self._session.begin():
            rows = self._session.execute(select(ModelReleaseRow, Model).join(Model).order_by(
                Model.id, ModelReleaseRow.created_at, ModelReleaseRow.id,
            ))
            return [self._snapshot(*row) for row in rows]

    @staticmethod
    def _snapshot(release: ModelReleaseRow, model: Model) -> ModelRelease:
        return ModelRelease(release.id, release_definition(model, release))
