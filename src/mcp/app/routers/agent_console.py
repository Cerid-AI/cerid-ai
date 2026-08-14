# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Agent Communication Console — SSE stream + REST endpoints.

Provides:
- ``GET /agent-console/stream``  — SSE endpoint using Redis XREAD BLOCK
- ``GET /agent-console/recent``  — last N events for initial hydration
- ``DELETE /agent-console/clear`` — clear the event stream

The same three endpoints are mirrored under ``/agents/activity/*`` (see
``activity_router`` below), but the two prefixes are not interchangeable in
practice — two distinct React components each pin to one prefix.
``/agent-console/*`` (all three verbs) backs the "Agent Communication
Console" overlay (``src/web/src/components/console/AgentConsole.tsx`` via
``use-agent-console.ts``, mounted from ``app-layout.tsx``).
``/agents/activity/stream`` — and only the stream verb — backs the "Agent
Activity Console" panel in Settings -> Diagnostics -> Agents
(``src/web/src/components/agents/agent-console.tsx`` via
``use-agent-activity-stream.ts``). ``/agents/activity/recent`` and
``/agents/activity/clear`` have no client at all. Don't assume parity
across the mirror when changing one side.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.utils.sse import sse_event
from deps import get_redis
from utils.agent_events import STREAM_KEY, clear_events, get_recent_events


# --- Response models (generated: single-return dict-literal routes) ---
class RecentEventsResponse(BaseModel):
    events: Any
    count: Any


class ActivityClearResponse(BaseModel):
    cleared: Any


class ActivityRecentResponse(BaseModel):
    events: Any
    count: Any


class ClearResponse(BaseModel):
    cleared: Any



logger = logging.getLogger("ai-companion.agent_console")

router = APIRouter(prefix="/agent-console", tags=["agent-console"])

# Second router mirroring the same endpoints under ``/agents/activity/*``.
# Registered alongside ``router`` in ``app/main.py`` so both paths serve the
# same underlying Redis Stream. Only ``stream`` has a live client here (the
# Settings -> Diagnostics -> Agents pane, via use-agent-activity-stream.ts);
# ``recent`` and ``clear`` are unused twins of the ``/agent-console/*``
# verbs the "Agent Communication Console" overlay actually calls — see
# use-agent-console.ts.
activity_router = APIRouter(prefix="/agents/activity", tags=["agent-console"])


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


async def _event_generator() -> AsyncGenerator[str, None]:
    """Yield SSE events from the Redis Stream using blocking reads."""
    redis = get_redis()

    # Start reading from the latest entry ($ = only new events)
    last_id = "$"

    # Send an initial heartbeat so the client knows the connection is live
    yield sse_event({"ts": time.time()}, event="heartbeat")

    while True:
        try:
            # XREAD BLOCK with a 5-second timeout so we can send keepalives
            entries = await asyncio.to_thread(
                redis.xread, {STREAM_KEY: last_id}, count=20, block=5000,
            )

            if not entries:
                # No new events -- send keepalive to prevent proxy timeouts
                yield sse_event({"ts": time.time()}, event="heartbeat")
                continue

            for _stream_name, messages in entries:
                for msg_id, fields in messages:
                    last_id = msg_id
                    # Decode timestamp
                    if "timestamp" in fields:
                        try:
                            fields["timestamp"] = float(fields["timestamp"])
                        except (ValueError, TypeError):
                            pass
                    # Decode metadata
                    if "metadata" in fields:
                        try:
                            fields["metadata"] = json.loads(fields["metadata"])
                        except (json.JSONDecodeError, TypeError):
                            fields["metadata"] = {}
                    fields["id"] = msg_id
                    yield sse_event(fields)

        except asyncio.CancelledError:
            logger.debug("Agent console SSE stream cancelled")
            return
        except Exception:  # noqa: BLE001
            logger.debug("Agent console SSE read error, reconnecting in 2s")
            await asyncio.sleep(2)


@router.get("/stream")
async def stream_events():
    """SSE endpoint for real-time agent activity events."""
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/recent", response_model=RecentEventsResponse)
def recent_events(count: int = Query(default=50, ge=1, le=200)):
    """Return the most recent agent events for initial hydration."""
    events = get_recent_events(count)
    return {"events": events, "count": len(events)}


@router.delete("/clear", response_model=ClearResponse)
def clear():
    """Clear the agent event stream."""
    deleted = clear_events()
    return {"cleared": deleted}


# ---------------------------------------------------------------------------
# ``/agents/activity/*`` aliases — same handlers as ``/agent-console/*``.
# ``stream`` has a live client (Settings -> Diagnostics -> Agents pane, see
# use-agent-activity-stream.ts); ``recent`` and ``clear`` are unused twins of
# the ``/agent-console/*`` verbs the "Agent Communication Console" overlay
# actually calls (use-agent-console.ts). Kept as the documented public URL
# for external API consumers.
# ---------------------------------------------------------------------------


@activity_router.get("/stream")
async def activity_stream():
    """SSE stream of real-time agent activity events (documented public URL).

    Emits ``{agent, message, level, timestamp, metadata, id}`` JSON envelopes
    in the same shape as ``/agent-console/stream``. Consumed directly by the
    Settings -> Diagnostics -> Agents pane's ``AgentConsole`` component
    (``src/web/src/components/agents/agent-console.tsx``, via
    ``use-agent-activity-stream.ts``) — a distinct component, despite the
    matching name, from the one that consumes ``/agent-console/stream``
    (``src/web/src/components/console/AgentConsole.tsx``).
    Heartbeats fire every 5 s to keep proxies (nginx, Cloudflare) from
    severing the connection during idle periods.
    """
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@activity_router.get("/recent", response_model=ActivityRecentResponse)
def activity_recent(count: int = Query(default=50, ge=1, le=200)):
    """Return the most recent agent events for initial hydration."""
    events = get_recent_events(count)
    return {"events": events, "count": len(events)}


@activity_router.delete("/clear", response_model=ActivityClearResponse)
def activity_clear():
    """Clear the agent event stream."""
    deleted = clear_events()
    return {"cleared": deleted}
