"""Run with python -m mic3_api.publisher [--once], independently of FastAPI."""

import argparse
import logging
import signal
from threading import Event

from confluent_kafka import KafkaException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from mic3_api.application.runs.publication import PublicationError, PublishOutbox
from mic3_api.core.publisher_config import PublisherSettings
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.kafka_publication import KafkaEventPublisher
from mic3_api.infrastructure.persistence.outbox import SqlAlchemyOutbox

LOG = logging.getLogger(__name__)


def run(database, publisher, settings: PublisherSettings, stop: Event, *, once=False) -> int:
    while not stop.is_set():
        try:
            with database.open_session() as session:
                count = PublishOutbox(SqlAlchemyOutbox(session), publisher).execute(
                    settings.publisher_batch_size,
                )
            if count or once:
                LOG.info("Outbox batch committed: published=%d", count)
        except (PublicationError, SQLAlchemyError) as exc:
            # Driver exceptions can contain connection strings or SQL parameters.
            LOG.error("Outbox batch failed (%s); uncommitted events retained.", type(exc).__name__)
            if once:
                return 1
            stop.wait(settings.publisher_poll_seconds)
            continue
        if once:
            return 0
        if count < settings.publisher_batch_size:
            stop.wait(settings.publisher_poll_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish committed MIC3 outbox events to Kafka")
    parser.add_argument("--once", action="store_true", help="Publish at most one batch and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    database = publisher = None
    stop = Event()
    previous = {}
    try:
        settings = PublisherSettings()
        database = Database(settings.database_url)
        publisher = KafkaEventPublisher(
            settings.kafka_bootstrap_servers, settings.kafka_requests_topic,
            settings.kafka_delivery_timeout_seconds,
            settings.kafka_events_topic,
            security=settings,
        )
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
        return run(database, publisher, settings, stop, once=args.once)
    except (ValidationError, KafkaException, SQLAlchemyError):
        LOG.error("Publisher configuration or initialization failed.")
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if publisher is not None:
            publisher.close()
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
