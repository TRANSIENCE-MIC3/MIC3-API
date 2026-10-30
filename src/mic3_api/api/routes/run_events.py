"""Personal SSE transport. Authentication/session lifetimes are shorter than streams."""
import asyncio
from dataclasses import asdict
import json
import math
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from sqlalchemy.exc import SQLAlchemyError

from mic3_api.api.dependencies.authentication import get_current_user, get_validated_identity
from mic3_api.application.authentication import ValidatedIdentity
from mic3_api.application.runs.feed import FeedLimitExceeded, FeedUnavailable
from mic3_api.api.routes.runs import RunDetailResponse

router = APIRouter(tags=["runs"])
HEARTBEAT_SECONDS = 15
SEND_TIMEOUT_SECONDS = 30


def frame(event, data):
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), separators=(',', ':'), allow_nan=False)}\n\n"


def run_data(run):
    return RunDetailResponse(**asdict(run)).model_dump(mode="json")


class RunEventResponse(StreamingResponse):
    def __init__(self, content, feed, subscription):
        super().__init__(content, media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
        })
        self.feed, self.subscription = feed, subscription

    async def __call__(self, scope, receive, send):
        async def bounded_send(message):
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
async def run_events(request: Request, identity: Annotated[ValidatedIdentity, Depends(get_validated_identity)]):
    if identity.expires_at is None or not math.isfinite(identity.expires_at) or identity.expires_at <= time.time():
        raise HTTPException(401, "An unexpired bearer token is required.", headers={"WWW-Authenticate": "Bearer"})
    feed = request.app.state.run_feed

    def resolve_user():
        try:
            with request.app.state.database.open_session() as session:
                return get_current_user(identity, session)
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Database access is unavailable.") from exc

    user = await asyncio.to_thread(resolve_user)
    try:
        sub = feed.subscribe(user.id, asyncio.get_running_loop())
    except FeedLimitExceeded as exc:
        raise HTTPException(429, "The stream connection limit has been reached.") from exc
    except FeedUnavailable as exc:
        raise HTTPException(503, "Run notifications are unavailable.") from exc
    try:
        snapshot = await asyncio.to_thread(feed.snapshot, sub)
    except BaseException as exc:
        feed.unsubscribe(sub)
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(503, "Run notifications are unavailable.") from exc

    async def stream():
        async def access_error():
            if time.time() >= identity.expires_at:
                return "token_expired"
            try:
                active = await asyncio.to_thread(feed.store.is_active, user.id)
            except Exception:
                return "unavailable"
            if time.time() >= identity.expires_at:
                return "token_expired"
            return None if active else "access_denied"

        error = await access_error()
        if error:
            yield frame("stream.error", {"code": error})
            return
        yield frame("runs.snapshot", {"items": [run_data(run) for run in snapshot]})
        heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS
        while True:
            error, updates = feed.drain(sub)
            if error:
                yield frame("stream.error", {"code": error})
                return
            for run in updates:
                error = await access_error()
                if error:
                    yield frame("stream.error", {"code": error})
                    return
                yield frame("run.updated", run_data(run))
            if time.monotonic() >= heartbeat_at:
                error = await access_error()
                if error:
                    yield frame("stream.error", {"code": error})
                    return
                yield ": heartbeat\n\n"
                heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS
            remaining = identity.expires_at - time.time()
            if remaining <= 0:
                yield frame("stream.error", {"code": "token_expired"})
                return
            try:
                await asyncio.wait_for(sub.changed.wait(), min(remaining, max(0, heartbeat_at - time.monotonic())))
            except TimeoutError:
                pass

    return RunEventResponse(stream(), feed, sub)
