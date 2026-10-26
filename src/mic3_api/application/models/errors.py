"""Application errors for invalid catalog data and run requests."""


class ModelError(Exception):
    """Invalid model configuration, parameters, or catalog reference."""


class ModelReleaseNotFound(ModelError):
    """The requested release is not in the catalog."""
