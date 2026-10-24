"""SQLAlchemy mappings for the catalog, immutable submissions and outbox."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mic3_api.infrastructure.persistence.base import Base


class Model(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)


class ModelReleaseRow(Base):
    __tablename__ = "model_releases"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(execution_definition) = 'object'", name="execution_object"),
        Index("ix_model_releases_model_id", "model_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    model_id: Mapped[str] = mapped_column(Text, ForeignKey("models.id", ondelete="RESTRICT"))
    image_digest: Mapped[str] = mapped_column(Text)
    execution_definition: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters_object"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_sha256"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="status"),
        Index("ix_runs_fingerprint", "fingerprint"),
        Index("ix_runs_dispatch", "created_at", "id", postgresql_where=text("accepted_at IS NOT NULL AND status IN ('queued', 'running')")),
        Index("ix_runs_requested_by_created_at", "requested_by", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    model_release_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("model_releases.id", ondelete="RESTRICT"))
    requested_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    parameters: Mapped[dict] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(Text)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_namespace: Mapped[str | None] = mapped_column(Text)
    job_uid: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunArtifact(Base):
    __tablename__ = "run_artifacts"
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="artifact_size_nonnegative"),)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True)
    object_key: Mapped[str] = mapped_column(Text, primary_key=True)
    bucket: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_unpublished", "created_at", "id", postgresql_where=text("published_at IS NULL")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
