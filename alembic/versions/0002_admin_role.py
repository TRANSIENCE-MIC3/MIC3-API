"""Seed the explicit local admin role without elevating users."""

from alembic import op
import sqlalchemy as sa

revision = "0002_admin_role"
down_revision = "0001_user_member_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles = sa.table("roles", sa.column("name"), sa.column("description"))
    op.bulk_insert(roles, [{
        "name": "admin", "description": "Explicitly granted MIC3 administrator role.",
    }])


def downgrade() -> None:
    assignments = sa.table("user_roles", sa.column("role_name"))
    roles = sa.table("roles", sa.column("name"))
    op.execute(assignments.delete().where(assignments.c.role_name == "admin"))
    op.execute(roles.delete().where(roles.c.name == "admin"))
