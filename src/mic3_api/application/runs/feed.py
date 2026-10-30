"""Bounded personal subscriptions and notification fan-out, independent of HTTP."""
import asyncio
from threading import Lock
from typing import Protocol
from uuid import UUID

from mic3_api.application.runs.queries import RunDetail


class FeedUnavailable(Exception):
    pass


class FeedLimitExceeded(Exception):
    pass


class FeedStore(Protocol):
    def find(self, run_id: UUID) -> RunDetail | None: ...
    def snapshot(self, user_id: UUID) -> list[RunDetail]: ...
    def is_active(self, user_id: UUID) -> bool: ...


class Subscription:
    def __init__(self, user_id, loop):
        self.user_id = user_id
        self.loop = loop
        self.changed = asyncio.Event()
        self.pending = {}
        self.error = None

    def wake(self):
        self.loop.call_soon_threadsafe(self.changed.set)


class RunFeed:
    def __init__(self, store: FeedStore, *, per_user=5, total=100, pending=100):
        self.store = store
        self.per_user, self.total, self.pending_limit = per_user, total, pending
        self._lock = Lock()
        # Serialize snapshot initialization with read-and-deliver, preventing stale
        # pre-snapshot reads from being delivered after the initial snapshot.
        self._reads = Lock()
        self._subscriptions = set()
        self._available = False

    def available(self):
        with self._lock:
            self._available = True

    def unavailable(self):
        with self._lock:
            self._available = False
            for sub in self._subscriptions:
                self._fail(sub, "unavailable")

    def subscribe(self, user_id, loop):
        with self._lock:
            if not self._available:
                raise FeedUnavailable()
            if (len(self._subscriptions) >= self.total or
                    sum(s.user_id == user_id for s in self._subscriptions) >= self.per_user):
                raise FeedLimitExceeded()
            sub = Subscription(user_id, loop)
            self._subscriptions.add(sub)
            return sub

    def unsubscribe(self, sub):
        with self._lock:
            self._subscriptions.discard(sub)

    def snapshot(self, sub):
        with self._reads:
            result = self.store.snapshot(sub.user_id)
            with self._lock:
                if sub.error or not self._available:
                    raise FeedUnavailable()
                sub.pending.clear()
            return result

    def publish(self, run_ids):
        with self._reads:
            for run_id in run_ids:
                with self._lock:
                    if not self._subscriptions:
                        return
                run = self.store.find(run_id)
                if run is None or run.requested_by is None:
                    continue
                with self._lock:
                    for sub in self._subscriptions:
                        if sub.error or sub.user_id != run.requested_by:
                            continue
                        if run.id not in sub.pending and len(sub.pending) >= self.pending_limit:
                            self._fail(sub, "resync_required")
                        else:
                            wake = not sub.pending
                            sub.pending[run.id] = run
                            if wake:
                                sub.wake()

    def drain(self, sub):
        with self._lock:
            sub.changed.clear()
            updates = list(sub.pending.values())
            sub.pending.clear()
            return sub.error, updates

    def _fail(self, sub, code):
        if sub.error is None:
            sub.error = code
            sub.pending.clear()
            sub.wake()
