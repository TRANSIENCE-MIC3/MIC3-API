"""Which infrastructure failures are transient and safe to retry."""

from confluent_kafka import KafkaException
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

# Connection loss, serialization or deadlock, insufficient resources.
_RETRYABLE_SQLSTATE_PREFIXES = ("08", "40", "53")
# Lock not available, statement cancelled, and the three server-shutdown codes.
_RETRYABLE_SQLSTATES = {"55P03", "57014", "57P01", "57P02", "57P03"}


def is_retryable(error: Exception) -> bool:
    if isinstance(error, KafkaException):
        return not error.args[0].fatal()
    if isinstance(error, DBAPIError):
        code = getattr(error.orig, "sqlstate", None)
        if code is None:
            return error.connection_invalidated or isinstance(error, (OperationalError, InterfaceError))
        return (error.connection_invalidated or code.startswith(_RETRYABLE_SQLSTATE_PREFIXES)
                or code in _RETRYABLE_SQLSTATES)
    return False
