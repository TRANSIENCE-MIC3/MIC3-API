import asyncio
from unittest.mock import Mock

import pytest

from mic3_api.api.routes.run_events import RunEventResponse


@pytest.mark.parametrize("failure", ["timeout", "disconnect"])
@pytest.mark.parametrize("spec", ["2.0", "2.4"])
def test_transport_failure_closes_generator_and_subscription(monkeypatch, failure, spec):
    monkeypatch.setattr("mic3_api.api.routes.run_events.SEND_TIMEOUT_SECONDS", 0.02)
    async def scenario():
        closed = []
        async def content():
            try:
                yield "event: runs.snapshot\ndata: {}\n\n"
                await asyncio.sleep(10)
            finally:
                closed.append(True)
        feed, sub = Mock(), Mock()
        response = RunEventResponse(content(), feed, sub)
        async def send(message):
            if message["type"] == "http.response.body":
                if failure == "timeout":
                    await asyncio.sleep(1)
                else:
                    raise OSError("client disconnected")
        async def receive():
            await asyncio.sleep(10)
        try:
            await response({"type": "http", "asgi": {"spec_version": spec}}, receive, send)
        except Exception as exc:
            from starlette.requests import ClientDisconnect
            assert failure == "disconnect" and isinstance(exc, (ClientDisconnect, OSError))
        assert closed == [True]
        feed.unsubscribe.assert_called_once_with(sub)
    asyncio.run(scenario())
