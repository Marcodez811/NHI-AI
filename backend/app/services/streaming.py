"""Shared Server-Sent Events transport for streaming API routes.

Extracted from the old ``app/api/routes/chat.py`` (docs/9_29_chat_core_and_attachments_plan.md,
decision 1) so the chat and slides outline-conversation streams depend on one
heartbeat/timeout implementation instead of one route module importing
internals from a sibling route module.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress

# Every hop between the backend and the browser must pass each event on at once.
# ``no-transform`` is what stops compressing proxies from buffering the stream:
# the frontend's Next.js rewrite gzips responses by default and only skips a
# response marked ``no-transform``, so without it the small ``: heartbeat`` writes
# sat in the compression buffer and the browser's 45-second inactivity timeout
# fired while the backend was still working. ``X-Accel-Buffering`` does the same
# for nginx, which ignores ``Cache-Control`` for this purpose.
SSE_RESPONSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


async def _stream_with_heartbeat(
    source: AsyncGenerator[str, None],
    *,
    heartbeat_seconds: float,
    timeout_seconds: float,
) -> AsyncGenerator[str, None]:
    """Forward an SSE generator while keeping proxies alive and enforcing a deadline."""

    iterator = source.__aiter__()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                yield 'data: {"type":"error","code":"chat_timeout","message":"對話處理逾時，請稍後再試。"}\n\n'
                return
            if pending is None:
                pending = asyncio.create_task(iterator.__anext__())
            done, _ = await asyncio.wait(
                {pending},
                timeout=min(heartbeat_seconds, remaining),
            )
            if not done:
                yield ": heartbeat\n\n"
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        with suppress(Exception):
            await iterator.aclose()
