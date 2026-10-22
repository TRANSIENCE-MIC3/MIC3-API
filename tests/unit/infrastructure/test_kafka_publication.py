from datetime import datetime, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from mic3_api.application.runs.publication import PublicationError, RunEvent
from mic3_api.infrastructure import kafka_publication


@pytest.mark.parametrize("delivery_error", [None, "broker rejected"])
@pytest.mark.parametrize("event_type", ["run.requested", "run.succeeded", "run.failed"])
def test_delivery_callback_controls_success(monkeypatch, delivery_error, event_type):
    calls = {}

    class Producer:
        def __init__(self, config):
            calls["config"] = config

        def produce(self, topic, **kwargs):
            calls.update(topic=topic, **kwargs)

        def poll(self, timeout):
            error = SimpleNamespace(name=lambda: "TEST_ERROR") if delivery_error else None
            calls["on_delivery"](error, None)

    monkeypatch.setattr(kafka_publication, "Producer", Producer)
    publisher = kafka_publication.KafkaEventPublisher(
        "localhost:9092", "test.requests", events_topic="test.events")
    event = RunEvent(uuid4(), uuid4(), event_type, datetime.now(timezone.utc))
    if delivery_error:
        with pytest.raises(PublicationError):
            publisher.publish(event)
    else:
        publisher.publish(event)
    assert calls["key"] == str(event.run_id).encode()
    assert calls["topic"] == ("test.requests" if event_type == "run.requested" else "test.events")
    assert json.loads(calls["value"]) == {
        "schema_version": 1, "event_id": str(event.id), "run_id": str(event.run_id),
        "event_type": event_type, "created_at": event.created_at.isoformat(),
    }
    assert calls["config"]["enable.idempotence"] is True
    assert calls["config"]["acks"] == "all"


def test_missing_callback_has_bounded_wait(monkeypatch):
    class Producer:
        def __init__(self, config): pass
        def produce(self, *args, **kwargs): pass
        def poll(self, timeout): pass

    clock = iter([0, 0, 20])
    monkeypatch.setattr(kafka_publication, "Producer", Producer)
    monkeypatch.setattr(kafka_publication.time, "monotonic", lambda: next(clock))
    publisher = kafka_publication.KafkaEventPublisher("localhost:9092", "mic3.run.requests.v1")
    with pytest.raises(PublicationError, match="timed out"):
        publisher.publish(RunEvent(uuid4(), uuid4(), "run.requested", datetime.now(timezone.utc)))


def test_unknown_event_is_not_silently_routed(monkeypatch):
    monkeypatch.setattr(kafka_publication, "Producer", lambda config: None)
    publisher = kafka_publication.KafkaEventPublisher("localhost:9092", "mic3.run.requests.v1")
    with pytest.raises(PublicationError, match="Unsupported"):
        publisher.publish(RunEvent(uuid4(), uuid4(), "unknown", datetime.now(timezone.utc)))
