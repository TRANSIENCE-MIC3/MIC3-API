"""Persist dispatch identity, completion, and collected artifacts."""
from alembic import op
import sqlalchemy as sa

revision = "0005_run_execution"
down_revision = "0004_run_acceptance"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("dispatch_started_at", "finished_at"):
        op.add_column("runs", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    for name in ("job_namespace", "job_uid"):
        op.add_column("runs", sa.Column(name, sa.Text(), nullable=True))
    op.create_table("run_artifacts",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("object_key", sa.Text(), primary_key=True),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="artifact_size_nonnegative"))
    op.create_index("ix_runs_dispatch", "runs", ["created_at", "id"],
                    postgresql_where=sa.text("accepted_at IS NOT NULL AND status IN ('queued', 'running')"))


def downgrade():
    op.drop_index("ix_runs_dispatch", table_name="runs")
    op.drop_table("run_artifacts")
    for name in ("job_uid", "job_namespace", "finished_at", "dispatch_started_at"):
        op.drop_column("runs", name)
