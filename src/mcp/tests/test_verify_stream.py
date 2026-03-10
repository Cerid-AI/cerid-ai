# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the SSE verify-stream endpoint keepalive mechanism.

Validates the PEP 479 fix: ``_safe_anext()`` must be a regular async function
(not an async generator) so that ``StopAsyncIteration`` raised by the
underlying generator is caught normally instead of being converted to
``RuntimeError`` by PEP 479 inside an async generator frame.
"""

import asyncio

import pytest

from routers.agents import _STREAM_END, _safe_anext

# ---------------------------------------------------------------------------
# Helpers — synthetic async generators for testing
# ---------------------------------------------------------------------------


async def _gen_items(*items):
    """Yield the given items, then exhaust."""
    for item in items:
        yield item


async def _gen_empty():
    """An async generator that yields nothing."""
    return
    yield  # noqa: RET504 — makes this an async generator


async def _gen_raises(exc_cls, *items):
    """Yield items, then raise an exception."""
    for item in items:
        yield item
    raise exc_cls("synthetic error")


# ---------------------------------------------------------------------------
# Tests for _safe_anext
# ---------------------------------------------------------------------------


class TestSafeAnext:
    """Verify _safe_anext correctly handles generator exhaustion."""

    @pytest.mark.asyncio
    async def test_returns_items(self):
        gen = _gen_items("a", "b", "c")
        results = []
        while True:
            result = await _safe_anext(gen)
            if result is _STREAM_END:
                break
            results.append(result)
        assert results == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_returns_sentinel_on_exhaustion(self):
        gen = _gen_empty()
        result = await _safe_anext(gen)
        assert result is _STREAM_END

    @pytest.mark.asyncio
    async def test_sentinel_is_not_none(self):
        """Ensure sentinel doesn't collide with None values."""
        gen = _gen_items(None, None)
        r1 = await _safe_anext(gen)
        assert r1 is None  # Actual None from generator
        r2 = await _safe_anext(gen)
        assert r2 is None
        r3 = await _safe_anext(gen)
        assert r3 is _STREAM_END

    @pytest.mark.asyncio
    async def test_propagates_non_stop_exceptions(self):
        """Non-StopAsyncIteration exceptions must still propagate."""
        gen = _gen_raises(ValueError, "ok")
        r1 = await _safe_anext(gen)
        assert r1 == "ok"
        with pytest.raises(ValueError, match="synthetic error"):
            await _safe_anext(gen)


class TestSafeAnextInAsyncGenerator:
    """Verify that _safe_anext avoids the PEP 479 RuntimeError.

    PEP 479 says: if ``StopIteration`` (or ``StopAsyncIteration``) is raised
    inside a generator (or async generator), Python converts it to
    ``RuntimeError``.  This is the exact bug that caused "stream interrupted":
    calling ``task.result()`` inside the ``event_generator()`` async generator
    would re-raise ``StopAsyncIteration``, which PEP 479 converted to
    ``RuntimeError``, bypassing ``except StopAsyncIteration: break``.

    These tests prove that ``_safe_anext`` (a regular async function) does NOT
    trigger PEP 479.
    """

    @pytest.mark.asyncio
    async def test_no_runtime_error_in_async_generator(self):
        """Using _safe_anext inside an async generator must not raise RuntimeError."""

        async def consumer():
            gen = _gen_items(1, 2, 3)
            while True:
                event = await _safe_anext(gen)
                if event is _STREAM_END:
                    break
                yield event

        results = [item async for item in consumer()]
        assert results == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_safe_anext_works_with_ensure_future(self):
        """Verify that _safe_anext + ensure_future terminates cleanly.

        This is the exact pattern used in the SSE endpoint.  The sentinel
        approach avoids any StopAsyncIteration propagation issues regardless
        of Python version or asyncio Task behavior.
        """

        async def consumer():
            gen = _gen_items("x", "y")
            while True:
                task = asyncio.ensure_future(_safe_anext(gen))
                await asyncio.wait({task})
                event = task.result()
                if event is _STREAM_END:
                    break
                yield event

        results = [item async for item in consumer()]
        assert results == ["x", "y"]


class TestKeepaliveIntegration:
    """Integration tests simulating the keepalive mechanism from agents.py."""

    @pytest.mark.asyncio
    async def test_keepalive_with_slow_generator(self):
        """Simulate a slow generator that triggers keepalive emissions."""

        async def _slow_gen():
            yield {"type": "extraction_complete"}
            await asyncio.sleep(0.1)
            yield {"type": "claim_verified", "index": 0}
            yield {"type": "summary", "total": 1}

        async def event_generator():
            gen = _slow_gen()
            anext_task = None
            try:
                while True:
                    if anext_task is None:
                        anext_task = asyncio.ensure_future(_safe_anext(gen))
                    done, _ = await asyncio.wait({anext_task}, timeout=0.05)
                    if done:
                        event = anext_task.result()
                        if event is _STREAM_END:
                            break
                        yield ("data", event)
                        anext_task = None
                    else:
                        yield ("keepalive", None)
            finally:
                if anext_task and not anext_task.done():
                    anext_task.cancel()
                await gen.aclose()

        results = []
        async for kind, event in event_generator():
            results.append((kind, event))

        # Should have data events and at least one keepalive
        data_events = [r for r in results if r[0] == "data"]
        keepalive_events = [r for r in results if r[0] == "keepalive"]
        assert len(data_events) == 3
        assert len(keepalive_events) >= 1

    @pytest.mark.asyncio
    async def test_generator_error_propagated_not_as_stream_end(self):
        """Errors from the generator should propagate, not silently end."""

        async def _error_gen():
            yield {"type": "extraction_complete"}
            raise ValueError("backend crash")

        gen = _error_gen()
        r1 = await _safe_anext(gen)
        assert r1["type"] == "extraction_complete"

        with pytest.raises(ValueError, match="backend crash"):
            await _safe_anext(gen)
