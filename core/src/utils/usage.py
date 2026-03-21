# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Usage metering utilities for per-user consumption tracking.

Tracks queries and ingestions per user per month in Redis.
Counters use keys like ``usage:{user_id}:queries:2026-03`` for monthly rollup.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger("ai-companion.usage")


def _month_key() -> str:
    """Return the current month as YYYY-MM."""
    return datetime.now(UTC).strftime("%Y-%m")


def record_query(redis_client, user_id: str) -> None:
    """Increment the query counter for a user in the current month."""
    if not user_id:
        return
    key = f"usage:{user_id}:queries:{_month_key()}"
    redis_client.incr(key)
    # Expire after 90 days so old counters auto-clean
    redis_client.expire(key, 90 * 86400)


def record_ingestion(redis_client, user_id: str, chunks: int = 1) -> None:
    """Increment the ingestion counter for a user in the current month."""
    if not user_id:
        return
    key = f"usage:{user_id}:ingestions:{_month_key()}"
    redis_client.incrby(key, chunks)
    redis_client.expire(key, 90 * 86400)


def get_usage(redis_client, user_id: str, month: str | None = None) -> dict:
    """Return usage counters for a user in a given month (defaults to current).

    Returns::

        {"queries": int, "ingestions": int, "month": "YYYY-MM"}
    """
    m = month or _month_key()
    queries = int(redis_client.get(f"usage:{user_id}:queries:{m}") or 0)
    ingestions = int(redis_client.get(f"usage:{user_id}:ingestions:{m}") or 0)
    return {"queries": queries, "ingestions": ingestions, "month": m}
