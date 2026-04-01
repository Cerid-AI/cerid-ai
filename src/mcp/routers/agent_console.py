# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Agent Communication Console — SSE stream + REST endpoints.

Provides:
- ``GET /agent-console/stream``  — SSE endpoint using Redis XREAD BLOCK
- ``GET /agent-console/recent``  — last N events for initial hydration
- ``DELETE /agent-console/clear`` — clear the event stream
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from deps import get_redis
from utils.agent_events import STREAM_KEY, clear_events, get_recent_events

logger = logging.getLogger("ai-companion.agent_console")

router = APIRouter(prefix="/agent-console", tags=["agent-console"])


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


async def _event_generator() -> AsyncGenerator[str, None]:
    """Yield SSE events from the Redis Stream using blocking reads."""
    redis = get_redis()

    # Start reading from the latest entry ($ = only new events)
    last_id = "$"

    # Send an initial heartbeat so the client knows the connection is live
    yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"

    while True:
        try:
            # XREAD BLOCK with a 5-second timeout so we can send keepalives
            entries = await asyncio.to_thread(
                redis.xread, {STREAM_KEY: last_id}, count=20, block=5000,
            )

            if not entries:
                # No new events -- send keepalive to prevent proxy timeouts
                yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
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
                    yield f"data: {json.dumps(fields)}\n\n"

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


@router.get("/recent")
def recent_events(count: int = Query(default=50, ge=1, le=200)):
    """Return the most recent agent events for initial hydration."""
    events = get_recent_events(count)
    return {"events": events, "count": len(events)}


@router.delete("/clear")
def clear():
    """Clear the agent event stream."""
    deleted = clear_events()
    return {"cleared": deleted}
