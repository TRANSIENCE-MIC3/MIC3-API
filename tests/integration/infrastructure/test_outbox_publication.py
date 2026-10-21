import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

from confluent_kafka import Consumer, TopicPartition, OFFSET_BEGINNING
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import register_release
from mic3_api.application.runs.publication import PublicationError, PublishOutbox
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.infrastructure.kafka_publication import KafkaEventPublisher
from mic3_api.infrastructure.persistence.execution import OutboxEvent
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.outbox import SqlAlchemyOutbox
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions
from tests.catalog_data import CONFIG, PARAMETERS

TOPIC = "mic3.run.requests.v1"
ROOT = Path(__file__).resolve().parents[3]


def submit(engine):
    with Session(engine) as session:
        catalog = SqlAlchemyModelCatalog(session)
        release = register_release(CONFIG, catalog)
        return SubmitRun(catalog, SqlAlchemyRunSubmissions(session)).execute(release.id, PARAMETERS)


def pending(engine):
    with Session(engine) as session:
        return list(session.scalars(select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None))))


def read_events(broker, run_id, count):
    consumer = Consumer({
        "bootstrap.servers": broker.address, "group.id": "mic3.test." + uuid4().hex,
        "enable.auto.commit": False, "allow.auto.create.topics": False,
    })
    try:
        consumer.assign([TopicPartition(TOPIC, 0, OFFSET_BEGINNING)])
        messages = []
        deadline = time.monotonic() + 20
        while len(messages) < count and time.monotonic() < deadline:
            message = consumer.poll(0.5)
            if message is None:
                continue
            assert not message.error(), message.error()
            if message.key() == str(run_id).encode():
                messages.append(json.loads(message.value()))
        assert len(messages) == count
        return messages
    finally:
        consumer.close()


def test_cli_submission_publishes_real_event_and_skips_published(
    migrated_engine, kafka_broker, tmp_path,
):
    # Exercise the existing CLI, not a parallel submission path.
    environment = {
        **os.environ, "KAFKA_BOOTSTRAP_SERVERS": kafka_broker.address,
        "KAFKA_REQUESTS_TOPIC": TOPIC, "KAFKA_DELIVERY_TIMEOUT_SECONDS": "10",
        "PUBLISHER_BATCH_SIZE": "10",
    }
    def cli(*args):
        result = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=environment,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    config = tmp_path / "release.json"
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps(PARAMETERS), encoding="utf-8")
    release = json.loads(cli("mic3_api.cli", "register-model-release", "--config",
                            str(config)).stdout)
    run = json.loads(cli("mic3_api.cli", "submit-run", "--release-id", release["id"],
                        "--parameters", str(request)).stdout)
    original_id = pending(migrated_engine)[0]
    assert "published=1" in cli("mic3_api.publisher", "--once").stderr
    envelope = read_events(kafka_broker, run["id"], 1)[0]
    assert envelope["schema_version"] == 1
    assert envelope["event_id"] == str(original_id)
    assert envelope["event_type"] == "run.requested"
    assert envelope["run_id"] == run["id"]
    assert not pending(migrated_engine)
    assert "published=0" in cli("mic3_api.publisher", "--once").stderr
    saved = json.loads(cli("mic3_api.cli", "show-run", "--run-id", run["id"]).stdout)
    assert saved["status"] == "queued"
    assert saved["events"][0]["published_at"] is not None


def test_database_commit_failure_after_ack_replays_same_event(migrated_engine, kafka_broker):
    run = submit(migrated_engine)
    original_id = pending(migrated_engine)[0]
    transport = KafkaEventPublisher(kafka_broker.address, TOPIC)
    try:
        with Session(migrated_engine) as session:
            def crash(session):
                raise RuntimeError("simulated crash after Kafka acknowledgement")
            event.listen(session, "before_commit", crash, once=True)
            with pytest.raises(RuntimeError, match="simulated crash"):
                PublishOutbox(SqlAlchemyOutbox(session), transport).execute()
            assert not session.in_transaction()
        assert pending(migrated_engine) == [original_id]
        with Session(migrated_engine) as session:
            assert PublishOutbox(SqlAlchemyOutbox(session), transport).execute() == 1
        messages = read_events(kafka_broker, run.id, 2)
        assert messages[0] == messages[1]
        assert messages[0]["event_id"] == str(original_id)
        assert not pending(migrated_engine)
    finally:
        transport.close()


def test_broker_outage_retains_event_and_recovers(migrated_engine, kafka_broker):
    run = submit(migrated_engine)
    original = pending(migrated_engine)
    kafka_broker.run("stop", "kafka")
    transport = KafkaEventPublisher(kafka_broker.address, TOPIC, 1)
    try:
        with Session(migrated_engine) as session:
            with pytest.raises(PublicationError):
                PublishOutbox(SqlAlchemyOutbox(session), transport).execute()
            assert not session.in_transaction()
        assert pending(migrated_engine) == original
    finally:
        transport.close()
        kafka_broker.run("up", "-d", "--wait", "kafka", timeout=240)
    recovered = KafkaEventPublisher(kafka_broker.address, TOPIC)
    try:
        with Session(migrated_engine) as session:
            assert PublishOutbox(SqlAlchemyOutbox(session), recovered).execute() == 1
        assert read_events(kafka_broker, run.id, 1)[0]["event_id"] == str(original[0])
    finally:
        recovered.close()


def test_batch_failure_rolls_back_acknowledged_prefix(migrated_engine):
    submit(migrated_engine)
    submit(migrated_engine)
    sent = []
    def publish(message):
        sent.append(message.id)
        if len(sent) == 2:
            raise PublicationError("broker unavailable")
    with Session(migrated_engine) as session:
        with pytest.raises(PublicationError):
            PublishOutbox(SqlAlchemyOutbox(session), SimpleNamespace(publish=publish)).execute()
        assert not session.in_transaction()
    assert set(pending(migrated_engine)) == set(sent)


def test_concurrent_claims_skip_locked_rows_and_release_after_rollback(migrated_engine):
    submit(migrated_engine)
    submit(migrated_engine)
    with Session(migrated_engine) as first, Session(migrated_engine) as second:
        with pytest.raises(RuntimeError):
            with SqlAlchemyOutbox(first).claim(1) as batch1:
                with SqlAlchemyOutbox(second).claim(10) as batch2:
                    assert len(batch1.events) == len(batch2.events) == 1
                    assert batch1.events[0].id != batch2.events[0].id
                    batch2.mark_published(batch2.events[0].id)
                raise RuntimeError("abandon first claim")
        with SqlAlchemyOutbox(first).claim(10) as retry:
            assert [e.id for e in retry.events] == [batch1.events[0].id]


def test_uncommitted_outbox_event_is_invisible(migrated_engine):
    run = submit(migrated_engine)
    with Session(migrated_engine) as writer, Session(migrated_engine) as reader:
        with writer.begin():
            extra = OutboxEvent(run_id=run.id, event_type="run.requested")
            writer.add(extra)
            writer.flush()
            with SqlAlchemyOutbox(reader).claim(10) as batch:
                assert extra.id not in [e.id for e in batch.events]
                assert len(batch.events) == 1
