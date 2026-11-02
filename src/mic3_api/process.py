"""Shared lifecycle helpers for the long-running worker and publisher processes."""

from collections.abc import Iterator
from contextlib import contextmanager
import signal
from threading import Event


@contextmanager
def stop_on_signals(stop: Event) -> Iterator[None]:
    """Set stop on SIGINT/SIGTERM while inside the block; restore handlers afterwards."""
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
