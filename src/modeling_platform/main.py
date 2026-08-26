from fastapi import FastAPI

from modeling_platform import __version__
from modeling_platform.api.router import api_router
from modeling_platform.core.config import Settings


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
