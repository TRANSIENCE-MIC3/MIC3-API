from fastapi import FastAPI

from mic3_api import __version__
from mic3_api.api.router import api_router
from mic3_api.core.config import Settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = Settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.include_router(api_router)
    return app
