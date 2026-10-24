from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

from confluent_kafka import KafkaError, KafkaException
from sqlalchemy.exc import IntegrityError, OperationalError

from mic3_api.application.runs.consumption import RecordLocation
from mic3_api.infrastructure.kafka_consumption import KafkaRecord
from mic3_api.worker import retryable, run


def test_unexpected_failure_stops_without_commit_and_closes_consumer():
    consumer = Mock()
    consumer.poll.return_value = KafkaRecord(RecordLocation("mic3.test", 0, 0), None, b"{")
    database = Mock()
    database.open_session.side_effect = RuntimeError("private diagnostic")
    settings = SimpleNamespace(worker_idle_timeout_seconds=10, worker_retry_seconds=0.01)
    assert run(database, lambda: consumer, settings, Event()) == 1
    consumer.commit.assert_not_called()
    consumer.close.assert_called_once()
    assert consumer.poll.call_count == 1


def test_once_idle_is_bounded_and_does_not_commit(monkeypatch):
    consumer = Mock()
    consumer.poll.return_value = None
    clock = iter([0, 11])
    monkeypatch.setattr("mic3_api.worker.time.monotonic", lambda: next(clock))
    settings = SimpleNamespace(worker_idle_timeout_seconds=10)
    assert run(Mock(), lambda: consumer, settings, Event(), once=True) == 0
    consumer.commit.assert_not_called()
    consumer.close.assert_called_once()


def test_retry_classification_does_not_hide_programming_or_constraint_errors():
    assert retryable(KafkaException(KafkaError(KafkaError._TIMED_OUT)))
    assert retryable(OperationalError("sql", {}, Exception("connection failed")))
    integrity = SimpleNamespace(sqlstate="23505")
    assert not retryable(IntegrityError("sql", {}, integrity))
    assert not retryable(ValueError("bug"))


def test_shutdown_closes_without_processing_next_record():
    stop = Event()
    consumer = Mock()
    def poll(_):
        stop.set()
        return None
    consumer.poll.side_effect = poll
    settings = SimpleNamespace(worker_idle_timeout_seconds=10, worker_retry_seconds=0.01)
    assert run(Mock(), lambda: consumer, settings, stop) == 0
    consumer.commit.assert_not_called()
    consumer.close.assert_called_once()
