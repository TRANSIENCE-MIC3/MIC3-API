"""Run errors, grouped under the shared application error categories."""

from mic3_api.application.errors import AccessDenied, LimitExceeded, NotFound


class RunNotFound(NotFound):
    """The run has no durable submission."""


class RunAccessDenied(AccessDenied):
    """The caller neither owns the run nor administers the platform."""


class RunAdmissionExceeded(LimitExceeded):
    """Outstanding work has reached an API admission bound."""


class RequesterNotFound(NotFound):
    """The user a run is submitted for does not exist."""
