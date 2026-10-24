from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mic3_api import publisher
from mic3_api.application.runs.publication import PublicationError
from mic3_api.core.publisher_config import PublisherSettings


def test_publisher_settings_need_no_oidc_and_enforce_period_topic_names():
    values = dict(db_host="localhost", db_name="test", db_user="test", db_password="secret",
                  _env_file=None)
    assert PublisherSettings(**values).kafka_requests_topic == "mic3.run.requests.v1"
    for topic in ("mic3.run-requests.v1", "mic3_run_requests", ""):
        with pytest.raises(ValidationError):
            PublisherSettings(**values, kafka_requests_topic=topic)


@pytest.mark.parametrize("once", [True, False])
def test_runner_failure_closes_session_and_backs_off(monkeypatch, caplog, once):
    closed, delays = [], []

    @contextmanager
    def session():
        try:
            yield None
        finally:
            closed.append(True)

    class UseCase:
        def __init__(self, *args): pass
        def execute(self, limit):
            raise PublicationError("sensitive driver detail")

    stop = Event()

    def wait(delay):
        delays.append(delay)
        stop.set()

    monkeypatch.setattr(stop, "wait", wait)
    monkeypatch.setattr(publisher, "PublishOutbox", UseCase)
    settings = SimpleNamespace(publisher_batch_size=10, publisher_poll_seconds=2)
    result = publisher.run(SimpleNamespace(open_session=session), None, settings, stop, once=once)
    assert result == (1 if once else 0)
    assert closed == [True]
    assert delays == ([] if once else [2])
    assert "sensitive driver detail" not in caplog.text
