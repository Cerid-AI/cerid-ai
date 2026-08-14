# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sync-cursor service — Redis-backed hot store, Neo4j durable backstop.

Every connector ingestion run reads its last cursor, fetches since
that point, and writes back the new cursor. Reads come from Redis
(sub-millisecond, no Neo4j round-trip in the hot path). Writes go to
Neo4j (durable) first, then Redis (hot) — see :func:`set_cursor` for
why that order matters (AF-074): it guarantees Redis is never left
holding a cursor value that was never durably committed.

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
    """Persist a cursor advance. Writes Neo4j (durable) first, then Redis (hot).

    AF-074: Neo4j is written first and Redis is only updated once that write
    has succeeded — a reconciliation ordering that rules out the case where
    a crash between the two writes leaves Redis holding a cursor value that
    was never durably committed. If the Neo4j write fails, the Redis write
    is skipped entirely so the two stores never diverge with Redis "ahead":
    the in-flight advance is dropped and the next run re-fetches from the
    last durably-committed cursor (idempotent per the connector protocol) —
    worse case is a duplicate batch, never a skipped one. If Redis then
    fails after a successful Neo4j write, Neo4j is ahead of a stale/missing
    Redis entry, which :func:`get_cursor`'s Neo4j fallback already repairs
    on the next read (it warms Redis from Neo4j on a miss).
    """
    try:
        srcdb.update_source_cursor(driver, source_id, cursor)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error(
            "app.services.sync_cursor.neo4j_write",
            exc,
            context={"source_id": source_id},
        )
        return

    if redis_client is not None:
        try:
            redis_client.set(_REDIS_PREFIX + source_id, json.dumps(cursor))
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "app.services.sync_cursor.redis_write",
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
