"""Accept Kafka requests durably; this process does not dispatch model Jobs."""

import argparse
import logging
import signal
import time
from threading import Event

from confluent_kafka import KafkaException
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from mic3_api.application.runs.acceptance import AcceptRun
from mic3_api.application.runs.consumption import ConsumeRequest
from mic3_api.core.worker_config import WorkerSettings
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.kafka_consumption import KafkaRequestConsumer
from mic3_api.infrastructure.persistence.rejections import SqlAlchemyRejections
from mic3_api.infrastructure.persistence.run_acceptance import SqlAlchemyRunAcceptance
from mic3_api.infrastructure.request_envelope import decode_request

LOG = logging.getLogger(__name__)


def retryable(error: Exception) -> bool:
    if isinstance(error, KafkaException):
        return not error.args[0].fatal()
    if isinstance(error, DBAPIError):
        code = getattr(error.orig, "sqlstate", None)
        if code is None:
            return error.connection_invalidated or isinstance(error, (OperationalError, InterfaceError))
        return error.connection_invalidated or code.startswith(("08", "40", "53")) or code in {
            "55P03", "57014", "57P01", "57P02", "57P03",
        }
    return False


def run(database, consumer_factory, settings: WorkerSettings, stop: Event, *, once=False) -> int:
    while not stop.is_set():
        consumer = None
        try:
            consumer = consumer_factory()
            deadline = time.monotonic() + settings.worker_idle_timeout_seconds
            while not stop.is_set():
                record = consumer.poll(0.2)
                if record is None:
                    if once and time.monotonic() >= deadline:
                        LOG.info("Worker outcome=idle")
                        return 0
                    continue
                request = decode_request(record.key, record.value)
                with database.open_session() as session:
                    outcome = ConsumeRequest(
                        AcceptRun(SqlAlchemyRunAcceptance(session)), SqlAlchemyRejections(session),
                    ).execute(record.location, request)
                consumer.commit(record)
                LOG.info("Worker outcome=%s topic=%s partition=%d offset=%d", outcome,
                         record.location.topic, record.location.partition, record.location.offset)
                if once:
                    return 0
        except Exception as exc:
            if not retryable(exc):
                LOG.error("Worker stopped (%s); unconfirmed record retained.", type(exc).__name__)
                return 1
            LOG.warning("Worker outcome=retry (%s); resuming from committed offsets.", type(exc).__name__)
            if once:
                return 1
        finally:
            if consumer is not None:
                consumer.close()
        stop.wait(settings.worker_retry_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accept Kafka run requests into PostgreSQL")
    parser.add_argument("--once", action="store_true", help="Handle at most one record, or exit after the idle timeout")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop, previous, database = Event(), {}, None
    try:
        settings = WorkerSettings()
        database = Database(settings.database_url,
                            statement_timeout_ms=settings.worker_statement_timeout_ms,
                            lock_timeout_ms=settings.worker_lock_timeout_ms)
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
        return run(database, lambda: KafkaRequestConsumer(settings.kafka_bootstrap_servers,
                   settings.kafka_requests_topic, settings.kafka_consumer_group), settings, stop, once=args.once)
    except Exception as exc:
        LOG.error("Worker initialization or shutdown failed (%s).", type(exc).__name__)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
