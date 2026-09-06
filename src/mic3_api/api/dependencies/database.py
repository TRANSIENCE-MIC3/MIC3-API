"""FastAPI dependencies for database capabilities and request sessions."""

from collections.abc import Iterator
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mic3_api.infrastructure.database import DatabaseGateway


logger = logging.getLogger(__name__)


def get_database(request: Request) -> DatabaseGateway:
    """Return the application-scoped database connection boundary."""
    return request.app.state.database


def get_session(
    database: Annotated[DatabaseGateway, Depends(get_database)],
) -> Iterator[Session]:
    """Provide one SQLAlchemy session and always close it after the request."""
    try:
        session = database.open_session()
    except SQLAlchemyError as exc:
        logger.warning("Unable to open a request database session: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database access is unavailable.",
        ) from exc

    try:
        yield session
    finally:
        session.close()
