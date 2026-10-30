"""One supervised autocommit LISTEN connection per API process."""
import logging
from threading import Event, Thread
from uuid import UUID

import psycopg

LOG = logging.getLogger(__name__)


def parse_payload(payload: str) -> tuple[UUID, UUID | None]:
    """Read "<run_id>" or "<run_id>:<owner_id>"; raise ValueError for anything else."""
    run_id, separator, owner = payload.partition(":")
    if separator and not owner:
        raise ValueError(payload)
    return UUID(run_id), UUID(owner) if owner else None


class RunNotifications:
    def __init__(self, settings, feed):
        self.feed = feed
        self.connection_args = dict(host=settings.db_host, port=settings.db_port,
                                    dbname=settings.db_name, user=settings.db_user,
                                    password=settings.db_password.get_secret_value(),
                                    autocommit=True, connect_timeout=3,
                                    options="-c statement_timeout=5000",
                                    application_name="mic3-run-notifications",
                                    keepalives=1, keepalives_idle=15,
                                    keepalives_interval=5, keepalives_count=3)
        self.stop_event = Event()
        self.thread = Thread(target=self._run, name="run-notifications", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join()

    def _run(self):
        delay = 1
        while not self.stop_event.is_set():
            try:
                with psycopg.connect(**self.connection_args) as connection:
                    installed = connection.execute("""
                        SELECT count(*) FROM pg_catalog.pg_trigger
                        WHERE (tgrelid = 'public.runs'::regclass AND tgname = 'mic3_runs_notify'
                           OR tgrelid = 'public.run_artifacts'::regclass AND tgname = 'mic3_run_artifacts_notify')
                          AND tgenabled IN ('O', 'A') AND NOT tgisinternal
                    """).fetchone()[0]
                    if installed != 2:
                        raise RuntimeError("Run notification migration is required")
                    connection.execute("LISTEN mic3_run_changes")
                    self.feed.available()
                    delay = 1
                    while not self.stop_event.is_set():
                        # Bound each batch and drain the socket even when no users listen.
                        changes: dict[UUID, UUID | None] = {}
                        for notification in connection.notifies(timeout=0.5, stop_after=100):
                            try:
                                run_id, owner = parse_payload(notification.payload)
                            except ValueError:
                                LOG.warning("Ignoring malformed run notification")
                                continue
                            # Artifact notifications carry no owner; keep one if any sender knew it.
                            changes[run_id] = owner or changes.get(run_id)
                        if changes:
                            self.feed.publish(changes)
            except Exception:
                LOG.warning("Run notification listener unavailable; reconnecting")
                self.feed.unavailable()
                self.stop_event.wait(delay)
                delay = min(delay * 2, 30)
        self.feed.unavailable()
