"""SQLAlchemy mappings for MIC3 users and their external identities."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from mic3_api.infrastructure.persistence.base import Base


class User(Base):
    """MIC3-owned user profile, independent of any identity provider."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        Uuid(native_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserIdentity(Base):
    """Opaque external OIDC identity mapped to an internal MIC3 user."""

    __tablename__ = "user_identities"
    __table_args__ = (Index("ix_user_identities_user_id", "user_id"),)

    issuer: Mapped[str] = mapped_column(Text, primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(native_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
