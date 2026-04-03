# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real-time agent activity event stream.

Agents emit events via ``emit_agent_event()``.  Events are stored in a Redis
Stream (``cerid:agent_events``) with a 500-entry cap and streamed to the
frontend via SSE (see ``routers/agent_console.py``).

This module is a *fire-and-forget* utility -- failures are silently logged so
agent hot-paths are never blocked by console telemetry.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger("ai-companion.agent_events")

STREAM_KEY = "cerid:agent_events"
STREAM_MAXLEN = 500


def emit_agent_event(
    agent: str,
    message: str,
    level: str = "info",
    metadata: dict | None = None,
) -> None:
    """Push an agent event to the Redis stream (sync, fire-and-forget).

    Safe to call from both sync and async contexts -- uses the synchronous
    Redis singleton from ``deps.get_redis()``.
    """
    try:
        from deps import get_redis

        redis = get_redis()
        event = {
            "agent": agent,
            "message": message,
            "level": level,
            "timestamp": str(time.time()),
            "metadata": json.dumps(metadata or {}),
        }
        redis.xadd(STREAM_KEY, event, maxlen=STREAM_MAXLEN)
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to emit agent event: %s", e)


def get_recent_events(count: int = 50) -> list[dict]:
    """Return the most recent *count* events from the stream (newest first)."""
    try:
        from deps import get_redis

        redis = get_redis()
        entries = redis.xrevrange(STREAM_KEY, count=count)
        results = []
        for eid, fields in entries:
            fields["id"] = eid
            # Decode timestamp back to float for JSON serialization
            if "timestamp" in fields:
                try:
                    fields["timestamp"] = float(fields["timestamp"])
                except (ValueError, TypeError):
                    pass
            # Decode metadata JSON
            if "metadata" in fields:
                try:
                    fields["metadata"] = json.loads(fields["metadata"])
                except (json.JSONDecodeError, TypeError):
                    fields["metadata"] = {}
            results.append(fields)
        return results
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to get recent agent events: %s", e)
        return []


def clear_events() -> int:
    """Delete the event stream.  Returns number of entries deleted."""
    try:
        from deps import get_redis

        redis = get_redis()
        length = redis.xlen(STREAM_KEY)
        redis.delete(STREAM_KEY)
        return length
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to clear agent events: %s", e)
        return 0
