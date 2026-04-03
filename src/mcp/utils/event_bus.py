# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Internal async event bus for decoupled inter-module communication (Phase 3 — extensibility).

Usage::

    from utils.event_bus import event_bus, DocumentIngested

    # Subscribe
    async def on_ingest(event: DocumentIngested):
        print(f"Ingested {event.artifact_id}")

    event_bus.subscribe("document.ingested", on_ingest)

    # Publish
    await event_bus.publish(DocumentIngested(artifact_id="abc", domain="code", chunks=12))
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

logger = logging.getLogger("ai-companion.event_bus")

# Maximum handlers per event type (configurable via env var)
_MAX_HANDLERS: int = int(os.getenv("CERID_EVENT_BUS_MAX_HANDLERS", "100"))


# ---------------------------------------------------------------------------
# Event hierarchy
# ---------------------------------------------------------------------------


@dataclass
class Event:
    """Base event — all concrete events inherit from this."""

    event_type: str = field(init=False, default="base")
    timestamp: str = field(init=False)

    def __post_init__(self) -> None:
        self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class DocumentIngested(Event):
    """Fired after a document is successfully ingested into the KB."""

    artifact_id: str = ""
    domain: str = ""
    chunks: int = 0

    def __post_init__(self) -> None:
        self.event_type = "document.ingested"
        super().__post_init__()


@dataclass
class MemoryCreated(Event):
    """Fired when a new memory is extracted and persisted."""

    memory_id: str = ""
    memory_type: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        self.event_type = "memory.created"
        super().__post_init__()


@dataclass
class AgentCompleted(Event):
    """Fired when an agent finishes processing a query."""

    agent_name: str = ""
    query_id: str = ""
    duration_ms: float = 0.0
    success: bool = True

    def __post_init__(self) -> None:
        self.event_type = "agent.completed"
        super().__post_init__()


@dataclass
class VerificationDone(Event):
    """Fired after the hallucination pipeline completes a verification."""

    conversation_id: str = ""
    overall_score: float = 0.0
    claims_total: int = 0
    claims_verified: int = 0

    def __post_init__(self) -> None:
        self.event_type = "verification.done"
        super().__post_init__()


@dataclass
class HealthChanged(Event):
    """Fired when the system health status changes (e.g. degradation tier shift)."""

    previous_status: str = ""
    new_status: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        self.event_type = "health.changed"
        super().__post_init__()


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

# Type alias for handler callables
Handler = Callable[[Any], Coroutine[Any, Any, None]]


class EventBus:
    """Lightweight async publish/subscribe event bus.

    Handlers are dispatched via ``asyncio.gather`` with per-handler exception
    isolation — a failing handler never breaks other subscribers.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._wildcard_handlers: list[Handler] = []

    # -- subscription -------------------------------------------------------

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register *handler* for events of the given type."""
        if len(self._handlers[event_type]) >= _MAX_HANDLERS:
            raise RuntimeError(
                f"Max handlers ({_MAX_HANDLERS}) reached for event '{event_type}'"
            )
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        """Register *handler* to receive every published event (wildcard)."""
        if len(self._wildcard_handlers) >= _MAX_HANDLERS:
            raise RuntimeError(
                f"Max wildcard handlers ({_MAX_HANDLERS}) reached"
            )
        self._wildcard_handlers.append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        """Remove *handler* from the given event type's subscriber list."""
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass  # handler was not subscribed — silently ignore

    # -- publishing ---------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Dispatch *event* to all matching handlers.

        Matching = handlers subscribed to ``event.event_type`` + wildcard handlers.
        Each handler is invoked concurrently via ``asyncio.gather``; exceptions
        in individual handlers are logged but do not propagate.
        """
        handlers: list[Handler] = [
            *self._handlers.get(event.event_type, []),
            *self._wildcard_handlers,
        ]
        if not handlers:
            return

        async def _safe_call(h: Handler) -> None:
            try:
                await h(event)
            except Exception:
                logger.exception(
                    "Event handler %s failed for %s",
                    getattr(h, "__name__", repr(h)),
                    event.event_type,
                )

        await asyncio.gather(*(_safe_call(h) for h in handlers))

    # -- introspection ------------------------------------------------------

    @property
    def handler_count(self) -> int:
        """Total number of registered handlers (typed + wildcard)."""
        typed = sum(len(v) for v in self._handlers.values())
        return typed + len(self._wildcard_handlers)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

event_bus = EventBus()
