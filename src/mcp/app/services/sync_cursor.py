# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sync-cursor service — Redis-backed hot store, Neo4j durable backstop.

Every connector ingestion run reads its last cursor, fetches since
that point, and writes back the new cursor atomically. Reads come
from Redis (sub-millisecond, no Neo4j round-trip in the hot path).
Writes go to BOTH Redis and Neo4j so a Redis flush / restart loses
at most the last in-flight cursor — Neo4j is the durable truth.

See ``tasks/2026-05-24-ingestion-experience-plan.md`` §2.2 for the
protocol contract. The cursor shape is connector-defined — this
service treats it as an opaque JSON blob.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.db.neo4j import sources as srcdb

logger = logging.getLogger("ai-companion.services.sync_cursor")

# Redis key prefix. The trailing ``:`` separates the prefix from the
# source UUID so prefix-scans (e.g., ``KEYS source:cursor:*``) work
# cleanly in ops scripts.
_REDIS_PREFIX = "source:cursor:"


def get_cursor(redis_client, driver, source_id: str) -> dict[str, Any]:
    """Return the cursor for ``source_id``. Redis-first, Neo4j fallback.

    On a Redis miss (cold start, key evicted, source freshly created),
    falls back to Neo4j and warms the Redis cache. Empty dict on both
    misses (the caller should treat "no cursor" as "fetch from the
    beginning").
    """
    if redis_client is not None:
        try:
            raw = redis_client.get(_REDIS_PREFIX + source_id)
            if raw is not None:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "app.services.sync_cursor.redis_read",
                exc,
                context={"source_id": source_id},
            )

    # Neo4j fallback. Read the durable copy and warm Redis.
    record = srcdb.get_source(driver, source_id)
    if record is None:
        return {}
    cursor = record.get("sync_cursor") or {}
    if isinstance(cursor, dict) and cursor and redis_client is not None:
        try:
            redis_client.set(_REDIS_PREFIX + source_id, json.dumps(cursor))
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "app.services.sync_cursor.redis_warm",
                exc,
                context={"source_id": source_id},
            )
    return cursor if isinstance(cursor, dict) else {}


def set_cursor(redis_client, driver, source_id: str, cursor: dict[str, Any]) -> None:
    """Persist a cursor advance. Writes to BOTH Redis (hot) and Neo4j (durable).

    The two writes are NOT in a transaction — if Neo4j fails, Redis
    keeps the new cursor and the next worker reads it. If Redis fails,
    Neo4j is the source of truth on cold start. Worst case: a
    crash-loop between the two writes leaves Redis ahead of Neo4j by
    one cursor; on restart the source replays one extra batch
    (idempotent per the connector protocol).
    """
    raw = json.dumps(cursor)
    if redis_client is not None:
        try:
            redis_client.set(_REDIS_PREFIX + source_id, raw)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "app.services.sync_cursor.redis_write",
                exc,
                context={"source_id": source_id},
            )
    try:
        srcdb.update_source_cursor(driver, source_id, cursor)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error(
            "app.services.sync_cursor.neo4j_write",
            exc,
            context={"source_id": source_id},
        )


def clear_cursor(redis_client, driver, source_id: str) -> None:
    """Reset a cursor to empty. Used by manual re-sync from the FE
    (the "fetch everything from scratch" affordance on the detail
    pane). Wipes Redis + sets Neo4j to ``{}``."""
    if redis_client is not None:
        try:
            redis_client.delete(_REDIS_PREFIX + source_id)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "app.services.sync_cursor.redis_clear",
                exc,
                context={"source_id": source_id},
            )
    try:
        srcdb.update_source_cursor(driver, source_id, {})
    except Exception as exc:  # noqa: BLE001 — observability boundary
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error(
            "app.services.sync_cursor.neo4j_clear",
            exc,
            context={"source_id": source_id},
        )
