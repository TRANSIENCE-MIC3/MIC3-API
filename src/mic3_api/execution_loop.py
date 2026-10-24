"""Supervisable execution loop, independent of its concrete network adapters."""
import logging

from mic3_api.application.runs.execution import InfrastructureUnavailable
from mic3_api.worker import retryable

LOG = logging.getLogger(__name__)


def run_execution(coordinator, stop, poll_seconds):
    while not stop.is_set():
        try:
            coordinator.tick()
        except Exception as exc:
            if not isinstance(exc, InfrastructureUnavailable) and not retryable(exc):
                LOG.error("Execution stopped (%s).", type(exc).__name__)
                stop.set()
                return 1
            LOG.warning("Execution retry (%s); durable work retained.", type(exc).__name__)
        stop.wait(poll_seconds)
    return 0
