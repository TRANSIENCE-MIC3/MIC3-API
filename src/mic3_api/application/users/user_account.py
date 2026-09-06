"""Immutable application data for persisted and authenticated users."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UserAccount:
    """Provider-independent snapshot of a persisted MIC3 account."""

    id: UUID
    email: str | None
    display_name: str | None
    is_active: bool
    roles: frozenset[str]


@dataclass(frozen=True, slots=True)
class UserProfileUpdates:
    """Non-empty profile values that should replace stored values."""

    email: str | None = None
    display_name: str | None = None

    @property
    def has_changes(self) -> bool:
        """Return whether at least one stored profile value should change."""
        return self.email is not None or self.display_name is not None


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Active MIC3 identity safe to expose to application use cases."""

    id: UUID
    email: str | None
    display_name: str | None
    roles: tuple[str, ...]
