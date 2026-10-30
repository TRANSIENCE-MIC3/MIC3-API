"""Application errors for invalid catalog data and run requests."""

from mic3_api.application.errors import InvalidInput, NotFound


class ModelError(InvalidInput):
    """Invalid model configuration, parameters, or catalog reference."""


class ModelReleaseNotFound(NotFound):
    """The requested release is not in the catalog."""
