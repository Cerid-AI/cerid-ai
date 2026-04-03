# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Dead-letter queue for failed ingestion attempts.

Uses Redis Stream cerid:dlq:ingestion for durability.
Entries have exponential backoff: 30s, 60s, 120s (3 attempts max).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.time import utcnow_iso
from utils.typed_redis import TypedRedis as Redis

logger = logging.getLogger("ai-companion.dlq")

STREAM_KEY = "cerid:dlq:ingestion"
MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 30


def _backoff_seconds(attempt: int) -> int:
    """Exponential backoff: 2^attempt * 30 seconds."""
    return (2 ** attempt) * BASE_BACKOFF_SECONDS


async def push_to_dlq(
    redis_client: Redis,
    payload: dict,
    error: str,
    attempt: int = 1,
) -> str:
    """Write a failed ingestion to the DLQ stream.

    Returns the Redis stream entry ID.
    """
    now = datetime.now(timezone.utc)
    next_retry_at = now + timedelta(seconds=_backoff_seconds(attempt))

    entry = {
        "payload": json.dumps(payload),
        "error": str(error),
        "attempt": str(attempt),
        "next_retry_at": next_retry_at.isoformat(),
        "created_at": utcnow_iso(),
    }

    entry_id: bytes | str = redis_client.xadd(STREAM_KEY, entry)
    entry_id_str = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
    logger.warning(
        "DLQ push: attempt=%d next_retry=%s error=%s",
        attempt,
        next_retry_at.isoformat(),
        error[:120],
    )
    return entry_id_str


async def pop_from_dlq(redis_client: Redis) -> dict[str, Any] | None:
    """Read the oldest entry whose next_retry_at has passed.

    Returns the entry dict with its ``entry_id``, or None if nothing is ready.
    """
    # Read oldest entries (up to 20 to find one that's ready)
    entries = redis_client.xrange(STREAM_KEY, count=20)
    if not entries:
        return None

    now = datetime.now(timezone.utc)

    for entry_id_raw, fields_raw in entries:
        entry_id = entry_id_raw.decode() if isinstance(entry_id_raw, bytes) else str(entry_id_raw)
        fields: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in fields_raw.items()
        }

        next_retry_str = fields.get("next_retry_at", "")
        try:
            next_retry = datetime.fromisoformat(next_retry_str)
        except (ValueError, TypeError):
            # Malformed entry — return it for processing anyway
            next_retry = now

        if next_retry <= now:
            return {
                "entry_id": entry_id,
                "payload": json.loads(fields.get("payload", "{}")),
                "error": fields.get("error", ""),
                "attempt": int(fields.get("attempt", "1")),
                "next_retry_at": next_retry_str,
                "created_at": fields.get("created_at", ""),
            }

    return None


async def list_dlq(
    redis_client: Redis,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Paginated listing of all DLQ entries."""
    entries = redis_client.xrange(STREAM_KEY)
    if not entries:
        return []

    result: list[dict[str, Any]] = []
    for entry_id_raw, fields_raw in entries:
        entry_id = entry_id_raw.decode() if isinstance(entry_id_raw, bytes) else str(entry_id_raw)
        fields: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in fields_raw.items()
        }
        result.append({
            "entry_id": entry_id,
            "payload": json.loads(fields.get("payload", "{}")),
            "error": fields.get("error", ""),
            "attempt": int(fields.get("attempt", "1")),
            "next_retry_at": fields.get("next_retry_at", ""),
            "created_at": fields.get("created_at", ""),
        })

    return result[offset : offset + limit]


async def clear_dlq_entry(redis_client: Redis, entry_id: str) -> bool:
    """Remove a single entry from the DLQ stream after successful retry."""
    removed = redis_client.xdel(STREAM_KEY, entry_id)
    if removed:
        logger.info("DLQ entry %s cleared", entry_id)
    return bool(removed)


async def dlq_count(redis_client: Redis) -> int:
    """Total number of entries in the DLQ stream."""
    return redis_client.xlen(STREAM_KEY)
