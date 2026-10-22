"""Record run acceptance and durable rejection of invalid incoming messages."""

from alembic import op
import sqlalchemy as sa

revision = "0004_run_acceptance"
down_revision = "0003_model_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("failure_code", sa.Text(), nullable=True))
    op.create_table(
        "rejected_messages",
        sa.Column("topic", sa.Text(), primary_key=True),
        sa.Column("partition", sa.Integer(), primary_key=True),
        sa.Column("offset", sa.BigInteger(), primary_key=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("diagnostic", sa.String(256), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rejected_messages")
    op.drop_column("runs", "failure_code")
    op.drop_column("runs", "accepted_at")
