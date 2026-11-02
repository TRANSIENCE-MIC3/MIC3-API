"""Accept Kafka requests durably, optionally dispatching model Jobs with --execute."""

import argparse
import logging
import time
from threading import Event

from mic3_api.application.runs.acceptance import AcceptRun
from mic3_api.application.runs.consumption import ConsumeRequest
from mic3_api.core.worker_config import WorkerSettings
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.kafka_consumption import KafkaRequestConsumer
from mic3_api.infrastructure.persistence.rejections import SqlAlchemyRejections
from mic3_api.infrastructure.persistence.run_acceptance import SqlAlchemyRunAcceptance
from mic3_api.infrastructure.request_envelope import decode_request
from mic3_api.infrastructure.retry import is_retryable
from mic3_api.process import stop_on_signals

LOG = logging.getLogger(__name__)


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
            if not is_retryable(exc):
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Handle at most one record, or exit after the idle timeout")
    mode.add_argument("--execute", action="store_true", help="Also dispatch and reconcile accepted model Jobs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop, database = Event(), None
    try:
        if args.execute:
            from mic3_api.core.execution_config import ExecutionSettings
            settings = ExecutionSettings()
        else:
            settings = WorkerSettings()
        database = Database(settings.database_url,
                            statement_timeout_ms=settings.worker_statement_timeout_ms,
                            lock_timeout_ms=settings.worker_lock_timeout_ms)

        def consumer_factory():
            return KafkaRequestConsumer(settings.kafka_bootstrap_servers, settings.kafka_requests_topic,
                                        settings.kafka_consumer_group, security=settings)

        with stop_on_signals(stop):
            if args.execute:
                from mic3_api.execution_runtime import run_with_execution
                return run_with_execution(database, consumer_factory, settings, stop, run)
            return run(database, consumer_factory, settings, stop, once=args.once)
    except Exception as exc:
        LOG.error("Worker initialization or shutdown failed (%s).", type(exc).__name__)
        return 1
    finally:
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
