from fastapi import Request

from mic3_api.infrastructure.database import DatabaseGateway


def get_database(request: Request) -> DatabaseGateway:
    """Return the application-scoped database connection boundary."""
    return request.app.state.database
