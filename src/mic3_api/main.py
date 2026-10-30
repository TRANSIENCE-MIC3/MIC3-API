from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import asyncio
from typing import Protocol

from fastapi import FastAPI

from mic3_api import __version__
from mic3_api.api.errors import register_error_handlers
from mic3_api.api.router import api_router
from mic3_api.application.authentication import AccessTokenValidator
from mic3_api.application.runs.feed import RunFeed
from mic3_api.core.config import Settings
from mic3_api.infrastructure.authentication import OidcAccessTokenValidator
from mic3_api.infrastructure.database import Database, DatabaseGateway
from mic3_api.infrastructure.persistence.run_feed import SqlAlchemyRunFeed
from mic3_api.infrastructure.run_notifications import RunNotifications


class BackgroundService(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


NotificationsFactory = Callable[[Settings, RunFeed], BackgroundService]


def create_app(
    settings: Settings | None = None,
    database: DatabaseGateway | None = None,
    token_validator: AccessTokenValidator | None = None,
    notifications_factory: NotificationsFactory | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application; arguments replace real adapters."""
    notifications_factory = notifications_factory or RunNotifications
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
    run_feed = RunFeed(SqlAlchemyRunFeed(app_database),
                       per_user=app_settings.sse_max_streams_per_user,
                       total=app_settings.sse_max_streams,
                       pending=app_settings.sse_pending_runs)
    notifications = notifications_factory(app_settings, run_feed)
    snapshot_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run-feed-snapshot")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        notifications.start()
        try:
            yield
        finally:
            await asyncio.to_thread(notifications.stop)
            snapshot_executor.shutdown(wait=False, cancel_futures=True)
            app_database.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.database = app_database
    app.state.settings = app_settings
    app.state.run_feed = run_feed
    app.state.run_feed_snapshots = snapshot_executor
    app.state.token_validator = app_token_validator
    register_error_handlers(app)
    app.include_router(api_router)
    return app
