# Copyright (c) 2026 Cerid AI. Apache-2.0 license.
"""Tests for the EventBus — pub/sub, wildcards, error isolation."""
from __future__ import annotations

import pytest

from utils.event_bus import (
    DocumentIngested,
    Event,
    EventBus,
    MemoryCreated,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bus() -> EventBus:
    return EventBus()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSubscribePublish:
    """Core subscribe + publish contract."""

    @pytest.mark.asyncio
    async def test_handler_receives_event(self):
        bus = _make_bus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("document.ingested", handler)
        event = DocumentIngested(artifact_id="a1", domain="code", chunks=5)
        await bus.publish(event)

        assert len(received) == 1
        assert received[0].artifact_id == "a1"

    @pytest.mark.asyncio
    async def test_multiple_handlers_same_event(self):
        bus = _make_bus()
        calls: list[str] = []

        async def h1(e: Event) -> None:
            calls.append("h1")

        async def h2(e: Event) -> None:
            calls.append("h2")

        bus.subscribe("document.ingested", h1)
        bus.subscribe("document.ingested", h2)
        await bus.publish(DocumentIngested())

        assert set(calls) == {"h1", "h2"}

    @pytest.mark.asyncio
    async def test_no_cross_event_delivery(self):
        bus = _make_bus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("memory.created", handler)
        await bus.publish(DocumentIngested())

        assert received == []


class TestWildcard:
    """Wildcard (subscribe_all) handlers."""

    @pytest.mark.asyncio
    async def test_wildcard_receives_all_events(self):
        bus = _make_bus()
        received: list[str] = []

        async def catch_all(event: Event) -> None:
            received.append(event.event_type)

        bus.subscribe_all(catch_all)
        await bus.publish(DocumentIngested())
        await bus.publish(MemoryCreated())

        assert "document.ingested" in received
        assert "memory.created" in received


class TestUnsubscribe:
    """Handler removal."""

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        bus = _make_bus()
        calls = 0

        async def handler(e: Event) -> None:
            nonlocal calls
            calls += 1

        bus.subscribe("document.ingested", handler)
        await bus.publish(DocumentIngested())
        assert calls == 1

        bus.unsubscribe("document.ingested", handler)
        await bus.publish(DocumentIngested())
        assert calls == 1  # No further delivery

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_is_silent(self):
        bus = _make_bus()

        async def handler(e: Event) -> None:
            pass

        # Should not raise
        bus.unsubscribe("document.ingested", handler)


class TestErrorIsolation:
    """One handler failure must not prevent others."""

    @pytest.mark.asyncio
    async def test_failing_handler_does_not_block_others(self):
        bus = _make_bus()
        calls: list[str] = []

        async def bad_handler(e: Event) -> None:
            raise RuntimeError("boom")

        async def good_handler(e: Event) -> None:
            calls.append("ok")

        bus.subscribe("document.ingested", bad_handler)
        bus.subscribe("document.ingested", good_handler)
        await bus.publish(DocumentIngested())

        assert "ok" in calls


class TestHandlerCount:

    def test_empty_bus_has_zero_handlers(self):
        bus = _make_bus()
        assert bus.handler_count == 0

    def test_count_reflects_subscriptions(self):
        bus = _make_bus()

        async def h(e: Event) -> None:
            pass

        bus.subscribe("document.ingested", h)
        bus.subscribe("memory.created", h)
        assert bus.handler_count == 2

    def test_wildcard_counted(self):
        bus = _make_bus()

        async def h(e: Event) -> None:
            pass

        bus.subscribe_all(h)
        assert bus.handler_count == 1


class TestMaxHandlers:
    """Max handler cap raises RuntimeError."""

    def test_exceeding_max_raises(self, monkeypatch):
        import utils.event_bus as mod
        monkeypatch.setattr(mod, "_MAX_HANDLERS", 2)
        bus = EventBus()

        async def h(e: Event) -> None:
            pass

        bus.subscribe("x", h)
        bus.subscribe("x", h)
        with pytest.raises(RuntimeError, match="Max handlers"):
            bus.subscribe("x", h)
