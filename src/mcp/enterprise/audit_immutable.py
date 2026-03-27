# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Immutable audit logging via Redis Streams (append-only)."""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger("ai-companion.enterprise.audit")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stream_key() -> str:
    """Return the configured audit stream key (deferred import)."""
    from config import settings

    return getattr(settings, "AUDIT_STREAM_KEY", "cerid:audit:stream")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def audit_log(
    redis_client,  # noqa: ANN001
    event_type: str,
    actor: str,
    resource: str,
    action: str,
    result: str,
    metadata: dict | None = None,
) -> str:
    """Append an immutable audit entry to the Redis Stream.

    Returns the stream entry ID assigned by Redis.
    """
    entry: dict[str, str] = {
        "event_type": event_type,
        "actor": actor,
        "resource": resource,
        "action": action,
        "result": result,
        "timestamp": str(time.time()),
    }
    if metadata:
        entry["metadata"] = json.dumps(metadata)

    stream_key = _stream_key()
    entry_id: str | bytes = redis_client.xadd(stream_key, entry)
    if isinstance(entry_id, bytes):
        entry_id = entry_id.decode()
    return entry_id


# ---------------------------------------------------------------------------
# Read / query
# ---------------------------------------------------------------------------

def query_audit_log(
    redis_client,  # noqa: ANN001
    event_type: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query the audit stream with optional filtering.

    Parameters
    ----------
    event_type:
        Filter entries by ``event_type`` field.
    actor:
        Filter entries by ``actor`` field.
    since:
        Redis stream ID lower bound (inclusive).  Defaults to ``"-"`` (beginning).
    until:
        Redis stream ID upper bound (inclusive).  Defaults to ``"+"`` (end).
    limit:
        Maximum number of entries to return.

    Returns a list of dicts, each with an ``"id"`` key and the stored fields.
    """
    stream_key = _stream_key()
    start = since or "-"
    end = until or "+"

    raw_entries = redis_client.xrange(stream_key, min=start, max=end, count=limit)

    results: list[dict] = []
    for entry_id, fields in raw_entries:
        # Decode bytes if necessary
        if isinstance(entry_id, bytes):
            entry_id = entry_id.decode()
        decoded: dict[str, str] = {}
        for k, v in fields.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            decoded[key] = val

        # Apply client-side filters
        if event_type and decoded.get("event_type") != event_type:
            continue
        if actor and decoded.get("actor") != actor:
            continue

        # Parse metadata back to dict if present
        if "metadata" in decoded:
            try:
                decoded["metadata"] = json.loads(decoded["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass

        decoded["id"] = entry_id
        results.append(decoded)

    return results
