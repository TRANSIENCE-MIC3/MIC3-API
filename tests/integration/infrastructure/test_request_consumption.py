import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import register_release
from mic3_api.application.runs.publication import PublishOutbox
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.core.worker_config import WorkerSettings
from mic3_api.infrastructure.kafka_consumption import KafkaRequestConsumer
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.kafka_publication import KafkaEventPublisher
from mic3_api.infrastructure.persistence.execution import Run
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.outbox import SqlAlchemyOutbox
from mic3_api.infrastructure.persistence.rejections import RejectedMessage
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from mic3_api.worker import run
from tests.catalog_data import CONFIG, PARAMETERS


@pytest.fixture
def flow(migrated_engine, kafka_broker):
    topic = "mic3.test." + uuid4().hex
    group = "mic3.test." + uuid4().hex
    admin = AdminClient({"bootstrap.servers": kafka_broker.address})
    admin.create_topics([NewTopic(topic, 1, 1)])[topic].result(15)
    settings = WorkerSettings(kafka_bootstrap_servers=kafka_broker.address,
                              kafka_requests_topic=topic, kafka_consumer_group=group,
                              worker_idle_timeout_seconds=12)
    database = SimpleNamespace(open_session=lambda: Session(migrated_engine))
    factory = lambda: KafkaRequestConsumer(kafka_broker.address, topic, group)
    return SimpleNamespace(engine=migrated_engine, broker=kafka_broker, topic=topic,
                           group=group, settings=settings, database=database, factory=factory)


def submit(flow):
    with Session(flow.engine) as session:
        catalog = SqlAlchemyModelCatalog(session)
        release = register_release(CONFIG, catalog)
        return SubmitRun(catalog, SqlAlchemyRunSubmissions(session)).execute(release.id, PARAMETERS)


def publish(flow):
    publisher = KafkaEventPublisher(flow.broker.address, flow.topic)
    try:
        with Session(flow.engine) as session:
            PublishOutbox(SqlAlchemyOutbox(session), publisher).execute()
    finally:
        publisher.close()


def send(flow, run_id=None, *, value=None):
    run_id = run_id or uuid4()
    if value is None:
        value = json.dumps(dict(schema_version=1, event_id=str(uuid4()), run_id=str(run_id),
                               event_type="run.requested", created_at=datetime.now(timezone.utc).isoformat())).encode()
    producer = Producer({"bootstrap.servers": flow.broker.address})
    errors = []
    producer.produce(flow.topic, key=str(run_id).encode(), value=value,
                     on_delivery=lambda error, _: errors.append(error))
    assert producer.flush(15) == 0
    assert errors == [None]


def once(flow, factory=None):
    return run(flow.database, factory or flow.factory, flow.settings, Event(), once=True)


def position(flow):
    consumer = Consumer({"bootstrap.servers": flow.broker.address, "group.id": flow.group,
                         "enable.auto.commit": False})
    try:
        return consumer.committed([TopicPartition(flow.topic, 0)], timeout=10)[0].offset
    finally:
        consumer.close()


def saved(flow, run_id):
    with Session(flow.engine) as session:
        return session.get(Run, run_id)


def fail_commit_factory(flow):
    consumer = flow.factory()
    def fail(_):
        raise KafkaException(KafkaError(KafkaError._TIMED_OUT))
    consumer.commit = fail
    return consumer


def test_cli_submission_publication_and_worker_restart(flow, tmp_path):
    config, parameters = tmp_path / "release.json", tmp_path / "parameters.json"
    config.write_text(json.dumps(CONFIG))
    parameters.write_text(json.dumps(PARAMETERS))
    environment = {**os.environ, "KAFKA_BOOTSTRAP_SERVERS": flow.broker.address,
                   "KAFKA_REQUESTS_TOPIC": flow.topic, "KAFKA_CONSUMER_GROUP": flow.group,
                   "WORKER_IDLE_TIMEOUT_SECONDS": "12"}
    def cli(*args):
        result = subprocess.run([sys.executable, "-m", *args], env=environment,
                                capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, result.stdout + result.stderr
        return result
    release = json.loads(cli("mic3_api.cli", "register-model-release", "--config", str(config)).stdout)
    submitted = json.loads(cli("mic3_api.cli", "submit-run", "--release-id", release["id"],
                               "--parameters", str(parameters)).stdout)
    from uuid import UUID
    run_id = UUID(submitted["id"])
    cli("mic3_api.publisher", "--once")
    assert "outcome=accepted" in cli("mic3_api.worker", "--once").stderr
    first = saved(flow, run_id)
    assert first.status == "queued" and first.accepted_at
    send(flow, run_id)
    assert "outcome=unchanged" in cli("mic3_api.worker", "--once").stderr
    assert saved(flow, run_id).accepted_at == first.accepted_at
    assert position(flow) == 2


@pytest.mark.parametrize("reject", [False, True])
def test_commit_failure_replays_database_outcome_before_processing_next(flow, reject):
    first, second = submit(flow), submit(flow)
    send(flow, first.id, value=b"{" if reject else None)
    send(flow, second.id)
    assert once(flow, lambda: fail_commit_factory(flow)) == 1
    assert position(flow) < 0
    assert saved(flow, second.id).accepted_at is None
    accepted_at = saved(flow, first.id).accepted_at
    assert once(flow) == 0
    assert position(flow) == 1
    assert saved(flow, first.id).accepted_at == accepted_at
    with Session(flow.engine) as session:
        assert len(list(session.scalars(select(RejectedMessage)))) == int(reject)
    assert once(flow) == 0
    assert saved(flow, second.id).accepted_at is not None
    assert position(flow) == 2


@pytest.mark.parametrize("reject", [False, True])
def test_database_commit_failure_rolls_back_and_preserves_offset(flow, reject):
    request = submit(flow)
    send(flow, request.id, value=b"{" if reject else None)
    def fail(connection):
        raise OperationalError("test", {}, Exception("temporary outage"))
    event.listen(flow.engine, "commit", fail)
    try:
        assert once(flow) == 1
    finally:
        event.remove(flow.engine, "commit", fail)
    assert position(flow) < 0
    assert saved(flow, request.id).accepted_at is None
    with Session(flow.engine) as session:
        assert list(session.scalars(select(RejectedMessage))) == []
    assert once(flow) == 0
    assert position(flow) == 1


def test_missing_stale_and_terminal_runs(flow):
    stale, terminal = submit(flow), submit(flow)
    with Session(flow.engine) as session, session.begin():
        session.get(Run, stale.id).fingerprint = "a" * 64
        session.get(Run, terminal.id).status = "succeeded"
    send(flow)
    send(flow, stale.id)
    send(flow, terminal.id)
    for _ in range(3):
        assert once(flow) == 0
    with Session(flow.engine) as session:
        assert session.scalars(select(RejectedMessage)).one().reason_code == "run_not_found"
    assert saved(flow, stale.id).failure_code == "execution_definition_changed"
    assert saved(flow, terminal.id).status == "succeeded"
    assert saved(flow, terminal.id).accepted_at is None


def test_reassignment_does_not_commit_polled_record(flow):
    request = submit(flow)
    publish(flow)
    consumer = flow.factory()
    try:
        deadline = time.monotonic() + 15
        record = None
        while record is None and time.monotonic() < deadline:
            record = consumer.poll(0.2)
        assert record is not None
    finally:
        consumer.close()
    assert position(flow) < 0
    assert once(flow) == 0
    assert saved(flow, request.id).accepted_at


def test_database_connection_failure_recovers_in_continuous_loop(flow):
    request = submit(flow)
    send(flow, request.id)
    real_open = flow.database.open_session
    stop = Event()
    attempts = []
    def open_session():
        attempts.append(1)
        if len(attempts) == 1:
            raise OperationalError("test", {}, Exception("connection unavailable"))
        return real_open()
    flow.database.open_session = open_session
    def factory():
        consumer = flow.factory()
        commit = consumer.commit
        def finish(record):
            commit(record)
            stop.set()
        consumer.commit = finish
        return consumer
    assert run(flow.database, factory, flow.settings, stop) == 0
    assert len(attempts) == 2
    assert position(flow) == 1


def test_worker_database_timeouts_bound_lock_waits_and_preserve_work(flow):
    request = submit(flow)
    send(flow, request.id)
    database = Database(flow.settings.database_url, statement_timeout_ms=1000, lock_timeout_ms=100)
    flow.database = database
    try:
        with database.open_session() as session:
            assert session.scalar(text("SHOW statement_timeout")) == "1s"
            assert session.scalar(text("SHOW lock_timeout")) == "100ms"
        with Session(flow.engine) as locker, locker.begin():
            locker.scalar(select(Run).where(Run.id == request.id).with_for_update())
            assert once(flow) == 1
            assert position(flow) < 0
        assert saved(flow, request.id).accepted_at is None
        assert once(flow) == 0
        assert saved(flow, request.id).accepted_at
    finally:
        database.dispose()
