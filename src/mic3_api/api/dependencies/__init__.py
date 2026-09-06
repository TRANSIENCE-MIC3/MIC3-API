"""Stable API dependency exports backed by cohesive boundary modules."""

from mic3_api.api.dependencies.authentication import get_current_user
from mic3_api.api.dependencies.database import get_database, get_session

__all__ = ["get_current_user", "get_database", "get_session"]
