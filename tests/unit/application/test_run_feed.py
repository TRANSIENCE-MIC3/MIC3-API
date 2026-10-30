import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock
from threading import Event
from uuid import uuid4

import pytest

from mic3_api.application.runs.feed import FeedLimitExceeded, FeedUnavailable, RunFeed
from mic3_api.application.runs.queries import RunDetail


def run(owner):
    return RunDetail(uuid4(), uuid4(), owner, {}, "queued", datetime.now(timezone.utc), None, None, None, None, ())


def test_fanout_bounds_coalescing_and_failure():
    async def scenario():
        owner, other = uuid4(), uuid4()
        store = Mock()
        feed = RunFeed(store, per_user=2, total=3, pending=1)
        loop = asyncio.get_running_loop()
        with pytest.raises(FeedUnavailable):
            feed.subscribe(owner, loop)
        feed.available()
        one, two = feed.subscribe(owner, loop), feed.subscribe(owner, loop)
        third = feed.subscribe(other, loop)
        with pytest.raises(FeedLimitExceeded):
            feed.subscribe(owner, loop)
        with pytest.raises(FeedLimitExceeded):
            feed.subscribe(uuid4(), loop)
        item = run(owner)
        store.find.return_value = item
        feed.publish({item.id: None})
        store.find.assert_called_once_with(item.id)
        store.find.return_value = replace(item, status="running")
        feed.publish({item.id: None})
        assert feed.drain(one)[1][0].status == "running"
        assert feed.drain(third) == (None, [])
        another = run(owner)
        store.find.return_value = another
        feed.publish({another.id: None})
        assert feed.drain(two) == ("resync_required", [])
        feed.unavailable()
        assert feed.drain(one) == ("unavailable", [])
        feed.unsubscribe(one)
        feed.unsubscribe(two)
        feed.unsubscribe(third)
        assert not feed._subscriptions
    asyncio.run(scenario())


def test_notification_during_snapshot_is_delivered_after_initial_state():
    async def scenario():
        owner = uuid4()
        store = Mock()
        feed = RunFeed(store)
        feed.available()
        sub = feed.subscribe(owner, asyncio.get_running_loop())
        initial = run(owner)
        entered, release = Event(), Event()
        def snapshot(_):
            entered.set()
            assert release.wait(2)
            return [initial]
        store.snapshot.side_effect = snapshot
        store.find.return_value = replace(initial, status="succeeded")
        snapshot_task = asyncio.create_task(asyncio.to_thread(feed.snapshot, sub))
        assert await asyncio.to_thread(entered.wait, 2)
        publish_task = asyncio.create_task(asyncio.to_thread(feed.publish, {initial.id: None}))
        await asyncio.sleep(0.02)
        assert not publish_task.done()
        release.set()
        assert (await snapshot_task)[0].status == "queued"
        await publish_task
        assert feed.drain(sub)[1][0].status == "succeeded"
    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["sse_max_streams_per_user", "sse_max_streams", "sse_pending_runs"])
def test_positive_settings(test_settings, field):
    from pydantic import ValidationError
    from mic3_api.core.config import Settings
    with pytest.raises(ValidationError):
        Settings(**(test_settings.model_dump() | {field: 0}))


def test_initial_snapshot_discards_only_older_pending_reads():
    async def scenario():
        owner = uuid4()
        store = Mock()
        feed = RunFeed(store)
        feed.available()
        sub = feed.subscribe(owner, asyncio.get_running_loop())
        item = run(owner)
        store.find.return_value = item
        feed.publish({item.id: None})
        store.snapshot.return_value = [replace(item, status="running")]
        assert feed.snapshot(sub)[0].status == "running"
        assert feed.drain(sub) == (None, [])
        store.find.return_value = replace(item, status="succeeded")
        feed.publish({item.id: None})
        assert feed.drain(sub)[1][0].status == "succeeded"
    asyncio.run(scenario())


def test_known_owner_without_stream_skips_the_read():
    async def scenario():
        watcher = uuid4()
        store = Mock()
        feed = RunFeed(store)
        feed.available()
        sub = feed.subscribe(watcher, asyncio.get_running_loop())
        unwatched = run(uuid4())
        feed.publish({unwatched.id: unwatched.requested_by})
        store.find.assert_not_called()
        watched = run(watcher)
        store.find.return_value = watched
        feed.publish({watched.id: watcher})
        assert feed.drain(sub) == (None, [watched])
    asyncio.run(scenario())


def test_read_failure_resyncs_affected_streams_without_dropping_feed():
    async def scenario():
        owner, other, bystander = uuid4(), uuid4(), uuid4()
        store = Mock()
        feed = RunFeed(store)
        feed.available()
        loop = asyncio.get_running_loop()
        mine, theirs, untouched = (feed.subscribe(u, loop) for u in (owner, other, bystander))
        store.find.side_effect = TimeoutError("statement timeout")
        feed.publish({uuid4(): owner, uuid4(): other})
        assert store.find.call_count == 1  # Remaining reads are skipped after a failure.
        assert feed.drain(mine) == ("resync_required", [])
        assert feed.drain(theirs) == ("resync_required", [])
        assert feed.drain(untouched) == (None, [])
        assert feed._available
        feed.publish({uuid4(): None})  # Unknown owner: every remaining stream resyncs.
        assert feed.drain(untouched) == ("resync_required", [])
    asyncio.run(scenario())


def test_notification_payload_parsing():
    from mic3_api.infrastructure.run_notifications import parse_payload
    run_id, owner = uuid4(), uuid4()
    assert parse_payload(str(run_id)) == (run_id, None)
    assert parse_payload(f"{run_id}:{owner}") == (run_id, owner)
    for bad in ("", "nope", f"{run_id}:", f"{run_id}:nope"):
        with pytest.raises(ValueError):
            parse_payload(bad)
