"""Personal SSE transport. Authentication/session lifetimes are shorter than streams."""
import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import Executor
import json
import math
import random
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope, Send

from mic3_api.api.dependencies.authentication import get_validated_identity, resolve_current_user_in_new_session
from mic3_api.api.dependencies.database import get_database
from mic3_api.api.dependencies.use_cases import get_run_feed, get_snapshot_executor
from mic3_api.api.schemas.runs import RunDetailResponse
from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.runs.feed import FeedLimitExceeded, FeedUnavailable, RunFeed, Subscription
from mic3_api.application.runs.queries import RunDetail
from mic3_api.infrastructure.database import DatabaseGateway

router = APIRouter(tags=["runs"])
HEARTBEAT_SECONDS = 15
SEND_TIMEOUT_SECONDS = 30
# Clients reconnect together after a listener loss; spread them out.
RETRY_MIN_MS, RETRY_MAX_MS = 2_000, 8_000


def frame(event: str, data: Any) -> str:
    payload = json.dumps(jsonable_encoder(data), separators=(",", ":"), allow_nan=False)
    return f"event: {event}\ndata: {payload}\n\n"


def run_data(run: RunDetail) -> dict:
    return RunDetailResponse.from_detail(run).model_dump(mode="json")


class RunEventResponse(StreamingResponse):
    def __init__(self, content: AsyncIterator[str], feed: RunFeed, subscription: Subscription):
        super().__init__(content, media_type="text/event-stream", headers={
            # no-transform stops gzip layers (e.g. the Next.js proxy) from buffering frames.
            "Cache-Control": "no-cache, no-store, no-transform", "X-Accel-Buffering": "no",
        })
        self.feed = feed
        self.subscription = subscription

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def bounded_send(message: Message) -> None:
            await asyncio.wait_for(send(message), SEND_TIMEOUT_SECONDS)
        try:
            await super().__call__(scope, receive, bounded_send)
        except (TimeoutError, ClientDisconnect):
            pass  # A blocked client cannot reliably receive another error frame.
        finally:
            self.feed.unsubscribe(self.subscription)
            await self.body_iterator.aclose()


@router.get("/runs/events", response_class=StreamingResponse, responses={
    200: {"content": {"text/event-stream": {}}, "description": "Personal snapshots and run updates."},
    401: {"description": "A valid, unexpired bearer token is required."},
    403: {"description": "The local account is inactive."},
    429: {"description": "This API process has reached a stream limit."},
    503: {"description": "The database or notification listener is unavailable."},
})
async def run_events(identity: Annotated[ValidatedIdentity, Depends(get_validated_identity)],
                     feed: Annotated[RunFeed, Depends(get_run_feed)],
                     snapshots: Annotated[Executor, Depends(get_snapshot_executor)],
                     database: Annotated[DatabaseGateway, Depends(get_database)]):
    expires_at = identity.expires_at
    if expires_at is None or not math.isfinite(expires_at) or expires_at <= time.time():
        raise HTTPException(401, "An unexpired bearer token is required.", headers={"WWW-Authenticate": "Bearer"})
    loop = asyncio.get_running_loop()
    # Not the get_current_user dependency: its request session would stay open for the whole stream.
    user = await asyncio.to_thread(resolve_current_user_in_new_session, identity, database)
    try:
        sub = feed.subscribe(user.id, loop)
    except FeedLimitExceeded as exc:
        raise HTTPException(429, "The stream connection limit has been reached.") from exc
    except FeedUnavailable as exc:
        raise HTTPException(503, "Run notifications are unavailable.") from exc
    try:
        # Snapshots run one at a time; a small dedicated pool keeps waiting ones from
        # tying up the default executor that the access checks below also use.
        snapshot = await loop.run_in_executor(snapshots, feed.snapshot, sub)
    except BaseException as exc:
        feed.unsubscribe(sub)
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(503, "Run notifications are unavailable.") from exc

    async def access_error() -> str | None:
        if time.time() >= expires_at:
            return "token_expired"
        try:
            active = await asyncio.to_thread(feed.is_active, user.id)
        except Exception:
            return "unavailable"
        if time.time() >= expires_at:
            return "token_expired"
        return None if active else "access_denied"

    async def stream() -> AsyncIterator[str]:
        yield f"retry: {random.randint(RETRY_MIN_MS, RETRY_MAX_MS)}\n\n"
        error = await access_error()
        if error is None:
            yield frame("runs.snapshot", {"items": [run_data(run) for run in snapshot]})
        heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS
        while error is None:
            error, updates = feed.drain(sub)
            heartbeat_due = time.monotonic() >= heartbeat_at
            # One access check per batch, not per update.
            if error is None and (updates or heartbeat_due):
                error = await access_error()
            if error is None and time.time() >= expires_at:
                error = "token_expired"
            if error is not None:
                break
            for run in updates:
                yield frame("run.updated", run_data(run))
            if heartbeat_due:
                yield ": heartbeat\n\n"
                heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS
            timeout = min(expires_at - time.time(), heartbeat_at - time.monotonic())
            try:
                await asyncio.wait_for(sub.changed.wait(), max(0, timeout))
            except TimeoutError:
                pass
        yield frame("stream.error", {"code": error})

    return RunEventResponse(stream(), feed, sub)
