from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mic3_api import __version__
from mic3_api.application.authentication import AccessTokenValidator
from mic3_api.api.router import api_router
from mic3_api.core.config import Settings
from mic3_api.infrastructure.authentication import OidcAccessTokenValidator
from mic3_api.infrastructure.database import Database, DatabaseGateway


def create_app(
    settings: Settings | None = None,
    database: DatabaseGateway | None = None,
    token_validator: AccessTokenValidator | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app_settings = settings or Settings()
    app_database = database or Database(app_settings.database_url)
    app_token_validator = token_validator or OidcAccessTokenValidator(
        issuer_url=app_settings.oidc_issuer_url,
        audience=app_settings.oidc_audience,
        allowed_algorithms=app_settings.oidc_allowed_algorithms,
        clock_skew_seconds=app_settings.oidc_clock_skew_seconds,
        http_timeout_seconds=app_settings.oidc_http_timeout_seconds,
        jwks_cache_seconds=app_settings.oidc_jwks_cache_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            app_database.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.database = app_database
    app.state.token_validator = app_token_validator
    app.include_router(api_router)
    return app
