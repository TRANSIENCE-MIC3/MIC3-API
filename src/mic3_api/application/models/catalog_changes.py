"""Protect configuration used by accepted, unfinished runs."""

from collections.abc import Iterable

from mic3_api.application.models.errors import ModelError
from mic3_api.application.models.execution import ExecutionDefinition


def ensure_active_modes_unchanged(current: ExecutionDefinition, proposed: ExecutionDefinition,
                                  active_modes: Iterable[str]) -> None:
    for mode in active_modes:
        if current.modes.get(mode) != proposed.modes.get(mode):
            raise ModelError(f"Mode '{mode}' is used by accepted unfinished runs and cannot be changed.")
