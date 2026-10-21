from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from mic3_api.application.runs.publication import PublicationError, PublishOutbox, RunEvent


def test_failed_delivery_stops_batch_before_marking_failed_or_later_events():
    events = [RunEvent(uuid4(), uuid4(), "run.requested", datetime.now(timezone.utc))
              for _ in range(3)]
    marked, sent, outcomes = [], [], []

    @contextmanager
    def claim(limit):
        try:
            yield SimpleNamespace(events=events, mark_published=marked.append)
        except PublicationError:
            outcomes.append("rollback")
            raise
        else:
            outcomes.append("commit")

    def publish(event):
        sent.append(event.id)
        if len(sent) == 2:
            raise PublicationError("No acknowledgement")

    use_case = PublishOutbox(SimpleNamespace(claim=claim), SimpleNamespace(publish=publish))
    with pytest.raises(PublicationError):
        use_case.execute()
    assert sent == [event.id for event in events[:2]]
    assert marked == [events[0].id]
    assert outcomes == ["rollback"]
