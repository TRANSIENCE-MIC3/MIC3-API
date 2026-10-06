"""Model catalog, immutable release references, queued runs and outbox."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_model_runs"
down_revision = "0002_admin_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
    )
    op.create_table(
        "model_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("model_id", sa.Text(), sa.ForeignKey("models.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("image_digest", sa.Text(), nullable=False),
        sa.Column("execution_definition", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(execution_definition) = 'object'", name="execution_object"),
    )
    op.create_index("ix_model_releases_model_id", "model_releases", ["model_id"])
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("model_release_id", sa.Uuid(), sa.ForeignKey("model_releases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters_object"),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_sha256"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="status"),
    )
    op.create_index("ix_runs_fingerprint", "runs", ["fingerprint"])
    op.create_index("ix_runs_requested_by_created_at", "runs", ["requested_by", "created_at"])
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_events_unpublished", "outbox_events", ["created_at", "id"],
                    postgresql_where=sa.text("published_at IS NULL"))


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("runs")
    op.drop_table("model_releases")
    op.drop_table("models")
