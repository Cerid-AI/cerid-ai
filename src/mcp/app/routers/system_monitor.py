# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Storage metrics and ingestion history endpoints.

Endpoints:
  /system/storage         — Aggregated storage usage across all data stores
  /admin/ingest-history   — Persistent ingestion activity feed from Redis stream

Dependencies: deps.py (service connections), config/settings.py (thresholds)

The per-store aggregation + usage-pct/threshold math lives in
``app/services/storage_metrics.py`` — shared with the ingest backpressure
check in ``app/services/ingestion.py`` (AF-042) so there is exactly one
computation of "how full is the corpus".
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Query

from app.services.storage_metrics import get_storage_report
from config.settings import INGEST_HISTORY_RETENTION_DAYS
from core.utils.swallowed import log_swallowed_error
from deps import get_redis
from errors import CeridError

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Redis stream key for ingestion history
INGEST_HISTORY_STREAM = "cerid:ingest:history"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/system/storage")  # response-model-allowed: dynamic response (shape varies)
def get_storage_metrics():
    """Return storage usage across all data stores, cached for 60 seconds."""
    return get_storage_report()


@router.get("/admin/ingest-history", response_model=dict[str, Any])
def get_ingest_history(
    limit: int = Query(50, ge=1, le=500),
    offset: str = Query("0-0", description="Redis stream ID for cursor-based pagination"),
):
    """Return recent ingestion events from Redis stream."""
    try:
        r = get_redis()
        # XREVRANGE returns newest-first.  Use '+' as start (newest), offset as end.
        if offset == "0-0":
            entries = r.xrevrange(INGEST_HISTORY_STREAM, "+", "-", count=limit)
        else:
            # Paginate: get entries older than the provided cursor
            entries = r.xrevrange(INGEST_HISTORY_STREAM, offset, "-", count=limit + 1)
            # Skip the first entry (it matches the cursor exactly)
            if entries and entries[0][0] == offset:
                entries = entries[1:]
            entries = entries[:limit]
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Ingest history unavailable: %s", e)
        return {"items": [], "total": 0, "next_cursor": None, "error": str(e)}

    items = []
    for entry_id, fields in entries:
        items.append({
            "id": entry_id,
            "filename": fields.get("filename", ""),
            "source_type": fields.get("source_type", "upload"),
            "domain": fields.get("domain", ""),
            "status": fields.get("status", "success"),
            "timestamp": fields.get("timestamp", ""),
            "chunks": int(fields.get("chunks", "0")),
            "error": fields.get("error", ""),
        })

    # Total count in stream
    try:
        total = r.xlen(INGEST_HISTORY_STREAM)
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        total = len(items)

    next_cursor = items[-1]["id"] if len(items) == limit else None

    return {
        "items": items,
        "total": total,
        "next_cursor": next_cursor,
    }


# ── Utility: push to ingest history stream ────────────────────────────────────


def record_ingest_event(
    filename: str,
    source_type: str,
    domain: str,
    status: str,
    chunks: int = 0,
    error: str = "",
) -> None:
    """Push an ingestion event to the persistent Redis stream.

    Called from services/ingestion.py after each ingest_content() or ingest_file().
    Stream entries auto-expire based on INGEST_HISTORY_RETENTION_DAYS.
    """
    try:
        from core.utils.time import utcnow_iso

        r = get_redis()
        r.xadd(
            INGEST_HISTORY_STREAM,
            {
                "filename": filename,
                "source_type": source_type,
                "domain": domain,
                "status": status,
                "chunks": str(chunks),
                "error": error[:500] if error else "",
                "timestamp": utcnow_iso(),
            },
        )
        # Trim old entries: keep last N days worth or max 10k entries
        retention_ms = INGEST_HISTORY_RETENTION_DAYS * 86400 * 1000
        r.xtrim(INGEST_HISTORY_STREAM, minid=f"{int(time.time() * 1000) - retention_ms}-0")
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        log_swallowed_error(
            "app.routers.system_monitor.record_ingest_event",
            exc,
        )
