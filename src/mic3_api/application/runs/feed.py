"""Bounded personal subscriptions and notification fan-out, independent of HTTP."""
import asyncio
from collections.abc import Mapping
import logging
from threading import Lock
from typing import Protocol
from uuid import UUID

from mic3_api.application.runs.queries import RunDetail

LOG = logging.getLogger(__name__)


class FeedUnavailable(Exception):
    pass


class FeedLimitExceeded(Exception):
    pass


class FeedStore(Protocol):
    def find(self, run_id: UUID) -> RunDetail | None: ...
    def snapshot(self, user_id: UUID) -> list[RunDetail]: ...
    def is_active(self, user_id: UUID) -> bool: ...


class Subscription:
    def __init__(self, user_id: UUID, loop: asyncio.AbstractEventLoop):
        self.user_id = user_id
        self.loop = loop
        self.changed = asyncio.Event()
        self.pending: dict[UUID, RunDetail] = {}
        self.error: str | None = None

    def wake(self) -> None:
        self.loop.call_soon_threadsafe(self.changed.set)


class RunFeed:
    def __init__(self, store: FeedStore, *, per_user: int = 5, total: int = 100, pending: int = 100):
        self.store = store
        self.per_user = per_user
        self.total = total
        self.pending_limit = pending
        self._lock = Lock()
        # Serialize snapshot initialization with read-and-deliver, preventing stale
        # pre-snapshot reads from being delivered after the initial snapshot.
        self._reads = Lock()
        self._subscriptions: set[Subscription] = set()
        self._available = False

    def available(self) -> None:
        with self._lock:
            self._available = True

    def unavailable(self) -> None:
        with self._lock:
            self._available = False
            for sub in self._subscriptions:
                self._fail(sub, "unavailable")

    def subscribe(self, user_id: UUID, loop: asyncio.AbstractEventLoop) -> Subscription:
        with self._lock:
            if not self._available:
                raise FeedUnavailable()
            if (len(self._subscriptions) >= self.total or
                    sum(s.user_id == user_id for s in self._subscriptions) >= self.per_user):
                raise FeedLimitExceeded()
            sub = Subscription(user_id, loop)
            self._subscriptions.add(sub)
            return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subscriptions.discard(sub)

    def is_active(self, user_id: UUID) -> bool:
        return self.store.is_active(user_id)

    def snapshot(self, sub: Subscription) -> list[RunDetail]:
        with self._reads:
            result = self.store.snapshot(sub.user_id)
            with self._lock:
                if sub.error or not self._available:
                    raise FeedUnavailable()
                sub.pending.clear()
            return result

    def publish(self, changes: Mapping[UUID, UUID | None]) -> None:
        """Deliver changed runs. Each change maps a run ID to its owner, or None when unknown.

        A failed read can't be retried later without breaking ordering, so the affected
        streams are told to resync instead. After one failure the rest of the batch is
        not read: the store is likely down and each read would wait for its timeout.
        """
        with self._reads:
            store_failed = False
            for run_id, owner in changes.items():
                with self._lock:
                    watched = {s.user_id for s in self._subscriptions if s.error is None}
                if not watched:
                    return
                if owner is not None and owner not in watched:
                    continue  # Nobody is listening for this owner; skip the read.
                if store_failed:
                    self._resync(owner)
                    continue
                try:
                    run = self.store.find(run_id)
                except Exception:
                    LOG.warning("Run feed read failed; affected streams must resync", exc_info=True)
                    store_failed = True
                    self._resync(owner)
                    continue
                if run is None or run.requested_by is None:
                    continue
                self._deliver(run)

    def drain(self, sub: Subscription) -> tuple[str | None, list[RunDetail]]:
        with self._lock:
            sub.changed.clear()
            updates = list(sub.pending.values())
            sub.pending.clear()
            return sub.error, updates

    def _deliver(self, run: RunDetail) -> None:
        with self._lock:
            for sub in self._subscriptions:
                if sub.error or sub.user_id != run.requested_by:
                    continue
                if run.id not in sub.pending and len(sub.pending) >= self.pending_limit:
                    self._fail(sub, "resync_required")
                    continue
                wake = not sub.pending
                sub.pending[run.id] = run
                if wake:
                    sub.wake()

    def _resync(self, owner: UUID | None) -> None:
        with self._lock:
            for sub in self._subscriptions:
                if owner is None or sub.user_id == owner:
                    self._fail(sub, "resync_required")

    def _fail(self, sub: Subscription, code: str) -> None:
        if sub.error is None:
            sub.error = code
            sub.pending.clear()
            sub.wake()
