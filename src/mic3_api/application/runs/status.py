"""Run lifecycle states, matching the runs.status database constraint."""

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)
TERMINAL_STATUSES = (RunStatus.SUCCEEDED, RunStatus.FAILED)
