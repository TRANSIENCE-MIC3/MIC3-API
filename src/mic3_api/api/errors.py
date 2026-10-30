"""One place that turns application and database errors into HTTP responses."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from mic3_api.application.errors import AccessDenied, InvalidInput, LimitExceeded, NotFound

logger = logging.getLogger(__name__)

STATUS_BY_CATEGORY: dict[type[Exception], int] = {
    NotFound: status.HTTP_404_NOT_FOUND,
    AccessDenied: status.HTTP_403_FORBIDDEN,
    LimitExceeded: status.HTTP_429_TOO_MANY_REQUESTS,
    InvalidInput: 422,  # Constant name differs between Starlette versions.
}


def register_error_handlers(app: FastAPI) -> None:
    for category, code in STATUS_BY_CATEGORY.items():
        app.add_exception_handler(category, _application_error(code))
    app.add_exception_handler(SQLAlchemyError, _database_unavailable)


def _application_error(code: int):
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=code)
    return handle


async def _database_unavailable(_: Request, exc: Exception) -> JSONResponse:
    # Driver messages can contain connection details; log the type only.
    logger.warning("Database access failed during a request (%s)", type(exc).__name__)
    return JSONResponse({"detail": "Database access is unavailable."},
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
