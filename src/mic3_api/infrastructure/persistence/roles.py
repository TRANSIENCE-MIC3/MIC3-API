"""SQLAlchemy mappings for local roles and explicit user assignments."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from mic3_api.infrastructure.persistence.base import Base


class Role(Base):
    """Stable local authorization role identifier."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UserRole(Base):
    """Explicit assignment of one local role to an internal user."""

    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(native_uuid=True),
        ForeignKey("users.id"),
        primary_key=True,
    )
    role_name: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("roles.name"),
        primary_key=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
