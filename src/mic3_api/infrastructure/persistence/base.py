"""Shared SQLAlchemy registry and database-object naming policy."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


CONSTRAINT_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Register all MIC3-owned mappings in one shared metadata collection."""

    metadata = MetaData(naming_convention=CONSTRAINT_NAMING_CONVENTION)
