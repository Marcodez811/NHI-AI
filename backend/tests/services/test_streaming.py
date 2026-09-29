import asyncio

import pytest

from app.services.streaming import _stream_with_heartbeat


@pytest.mark.asyncio
async def test_stream_wrapper_emits_heartbeat_while_source_is_idle():
    async def source():
        await asyncio.sleep(0.03)
        yield 'data: {"type":"done","citations":[]}\n\n'

    events = [
        event
        async for event in _stream_with_heartbeat(
            source(), heartbeat_seconds=0.01, timeout_seconds=1
        )
    ]

    assert ": heartbeat\n\n" in events
    assert '"type":"done"' in events[-1]


@pytest.mark.asyncio
async def test_stream_wrapper_emits_terminal_timeout_and_closes_source():
    closed = False

    async def source():
        nonlocal closed
        try:
            await asyncio.sleep(1)
            yield "unreachable"
        finally:
            closed = True

    events = [
        event
        async for event in _stream_with_heartbeat(
            source(), heartbeat_seconds=0.01, timeout_seconds=0.025
        )
    ]

    assert '"code":"chat_timeout"' in events[-1]
    assert closed is True
