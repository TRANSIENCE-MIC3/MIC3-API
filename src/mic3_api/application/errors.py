"""Error categories shared by every use case.

Each category maps to one HTTP status in mic3_api.api.errors, so routes never
translate errors themselves. Messages are safe to show to the caller.
"""


class ApplicationError(Exception):
    """A use case refused the request."""


class NotFound(ApplicationError):
    """The referenced resource does not exist."""


class AccessDenied(ApplicationError):
    """The caller may not perform this operation."""


class InvalidInput(ApplicationError):
    """The request or configuration is invalid."""


class LimitExceeded(ApplicationError):
    """An admission limit has been reached; retry later."""
