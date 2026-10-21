"""Record durable acceptance on the existing run."""

from alembic import op
import sqlalchemy as sa

revision = "0004_run_acceptance"
down_revision = "0003_model_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("failure_code", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "failure_code")
    op.drop_column("runs", "accepted_at")
