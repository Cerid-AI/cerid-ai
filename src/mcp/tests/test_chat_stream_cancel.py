# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression: /chat/stream must propagate client abort to the upstream
LLM connection (was previously leaking OpenRouter sockets on browser
navigation)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_generator_aborts_on_disconnect():
    """If Request.is_disconnected returns True mid-stream, the generator
    must stop yielding and close the upstream httpx response."""
    from app.routers import chat as chat_mod

    # Build a fake request whose is_disconnected flips True after 3 chunks.
    disconnect_state = {"count": 0, "flipped": False}

    async def _is_disc():
        return disconnect_state["flipped"]

    request = MagicMock()
    request.is_disconnected = _is_disc

    # Fake upstream httpx response that would emit forever.
    upstream_aclose = AsyncMock()
    upstream = MagicMock()
    upstream.aclose = upstream_aclose

    async def fake_aiter_bytes():
        i = 0
        while True:
            yield f'data: {{"choices":[{{"delta":{{"content":"tok{i}"}}}}]}}\n\n'.encode()
            await asyncio.sleep(0)
            i += 1
            disconnect_state["count"] += 1
            if disconnect_state["count"] == 3:
                disconnect_state["flipped"] = True

    upstream.aiter_bytes = fake_aiter_bytes

    gen = chat_mod._success_gen(request, upstream, "test-model")
    chunks = []
    async for c in gen:
        chunks.append(c)
        if len(chunks) > 20:
            pytest.fail("generator did not stop after disconnect")

    # After disconnect, the finally block must have closed the upstream response.
    upstream_aclose.assert_awaited()
    # And it should have yielded at least one chunk but well under 20 (stopped early).
    assert 1 <= len(chunks) < 10


@pytest.mark.asyncio
async def test_generator_handles_cancelled_error():
    """When the task is cancelled (client closes connection), the generator
    must run its finally block and aclose the upstream."""
    from app.routers import chat as chat_mod

    async def _is_disc():
        return False

    request = MagicMock()
    request.is_disconnected = _is_disc

    upstream_aclose = AsyncMock()
    upstream = MagicMock()
    upstream.aclose = upstream_aclose

    started = asyncio.Event()

    async def fake_aiter_bytes():
        started.set()
        # Yield one chunk then hang — the consumer will cancel.
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        await asyncio.sleep(10)  # cancelled here
        yield b"never"

    upstream.aiter_bytes = fake_aiter_bytes

    gen = chat_mod._success_gen(request, upstream, "test-model")

    async def consumer():
        async for _ in gen:
            pass

    task = asyncio.create_task(consumer())
    await started.wait()
    await asyncio.sleep(0)  # let one yield through
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # finally must have run
    upstream_aclose.assert_awaited()


@pytest.mark.asyncio
async def test_generator_emits_meta_update_as_complete_sse_event():
    """cerid_meta_update must be emitted as a self-contained SSE event with
    a trailing \\n\\n boundary (not spliced mid-line)."""
    from app.routers import chat as chat_mod

    async def _is_disc():
        return False

    request = MagicMock()
    request.is_disconnected = _is_disc

    upstream_aclose = AsyncMock()
    upstream = MagicMock()
    upstream.aclose = upstream_aclose

    async def fake_aiter_bytes():
        # First chunk — upstream reports a different model
        yield (
            b'data: {"model":"anthropic/claude-3.7-sonnet",'
            b'"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        )
        yield b"data: [DONE]\n\n"

    upstream.aiter_bytes = fake_aiter_bytes

    gen = chat_mod._success_gen(request, upstream, "anthropic/claude-sonnet-4.6")
    all_bytes = b""
    async for c in gen:
        all_bytes += c

    # Must contain meta update as its own event
    assert b"cerid_meta_update" in all_bytes
    # The injected meta event must be terminated with \n\n before the next data: line
    text = all_bytes.decode()
    # Find the meta line and check its termination
    import re
    m = re.search(r"data: (\{[^\n]*cerid_meta_update[^\n]*\})\n\n", text)
    assert m is not None, f"meta event not properly framed: {text!r}"
    upstream_aclose.assert_awaited()
