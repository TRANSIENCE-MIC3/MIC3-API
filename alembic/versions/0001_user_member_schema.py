"""Create the initial user, external identity, and role schema.

Revision ID: 0001_user_member_schema
Revises:
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_user_member_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the baseline MIC3 application schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_table(
        "roles",
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_roles")),
    )
    op.create_table(
        "user_identities",
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_identities_user_id_users"),
        ),
        sa.PrimaryKeyConstraint(
            "issuer",
            "subject",
            name=op.f("pk_user_identities"),
        ),
    )
    op.create_index(
        op.f("ix_user_identities_user_id"),
        "user_identities",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_name", sa.String(length=50), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["role_name"],
            ["roles.name"],
            name=op.f("fk_user_roles_role_name_roles"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_roles_user_id_users"),
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "role_name",
            name=op.f("pk_user_roles"),
        ),
    )

    roles = sa.table(
        "roles",
        sa.column("name", sa.String(length=50)),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        roles,
        [
            {
                "name": "member",
                "description": "Default non-elevated MIC3 member role.",
            }
        ],
    )


def downgrade() -> None:
    """Remove the baseline MIC3 application schema."""
    op.drop_table("user_roles")
    op.drop_table("user_identities")
    op.drop_table("roles")
    op.drop_table("users")
