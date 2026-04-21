# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Admin endpoints for the Dead-Letter Queue (DLQ).

Provides visibility into failed ingestions and manual retry/discard controls.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from config.features import CERID_MULTI_USER
from deps import get_redis
from errors import IngestionError
from utils.dlq import MAX_ATTEMPTS, clear_dlq_entry, dlq_count, list_dlq, push_to_dlq
from utils.error_handler import handle_errors
from utils.typed_redis import TypedRedis

logger = logging.getLogger("ai-companion.dlq-admin")


def _require_admin(request: Request) -> None:
    """Block non-admin users in multi-user mode. No-op in single-user."""
    if not CERID_MULTI_USER:
        return
    is_admin = getattr(request.state, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


router = APIRouter(
    prefix="/admin/dlq",
    tags=["admin-dlq"],
    dependencies=[Depends(_require_admin)],
)


@router.get("")
@handle_errors()
async def get_dlq_entries(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List DLQ entries with pagination."""
    redis_client = TypedRedis(get_redis())
    entries = await list_dlq(redis_client, limit=limit, offset=offset)
    total = await dlq_count(redis_client)
    return {
        "entries": entries,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/retry/{entry_id}")
@handle_errors()
async def retry_dlq_entry(entry_id: str) -> dict[str, Any]:
    """Manually retry a specific DLQ entry.

    Re-runs ingest_content with the stored payload. On success the entry
    is removed from the DLQ. On failure it is re-queued with an incremented
    attempt counter (up to MAX_ATTEMPTS, after which it stays for manual discard).
    """
    import asyncio

    redis_client = TypedRedis(get_redis())

    # Find the specific entry
    entries = await list_dlq(redis_client, limit=500, offset=0)
    target = next((e for e in entries if e["entry_id"] == entry_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"DLQ entry {entry_id} not found")

    payload = target["payload"]
    attempt = target["attempt"]

    # Remove the entry before retrying (prevent double-processing)
    await clear_dlq_entry(redis_client, entry_id)

    # Re-run ingestion
    from app.services.ingestion import ingest_content

    content = payload.get("content", "")
    domain = payload.get("domain", "general")
    metadata = payload.get("metadata") or {}
    metadata["_dlq_attempt"] = attempt + 1

    try:
        result = await asyncio.to_thread(ingest_content, content, domain, metadata)
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        # Re-queue if under max attempts
        new_attempt = attempt + 1
        if new_attempt <= MAX_ATTEMPTS:
            await push_to_dlq(redis_client, payload, error=str(e), attempt=new_attempt)
            return {
                "status": "retry_failed",
                "entry_id": entry_id,
                "attempt": new_attempt,
                "error": str(e),
                "requeued": True,
            }
        return {
            "status": "retry_failed",
            "entry_id": entry_id,
            "attempt": new_attempt,
            "error": str(e),
            "requeued": False,
            "message": "Max attempts exceeded — entry discarded",
        }

    if result.get("status") == "error":
        new_attempt = attempt + 1
        if new_attempt <= MAX_ATTEMPTS:
            await push_to_dlq(
                redis_client, payload, error=result.get("error", "unknown"), attempt=new_attempt
            )
            return {
                "status": "retry_failed",
                "entry_id": entry_id,
                "attempt": new_attempt,
                "error": result.get("error"),
                "requeued": True,
            }
        return {
            "status": "retry_failed",
            "entry_id": entry_id,
            "attempt": new_attempt,
            "error": result.get("error"),
            "requeued": False,
            "message": "Max attempts exceeded — entry discarded",
        }

    return {
        "status": "retry_success",
        "entry_id": entry_id,
        "result": result,
    }


@router.delete("/{entry_id}")
@handle_errors()
async def discard_dlq_entry(entry_id: str) -> dict[str, Any]:
    """Discard a DLQ entry without retrying."""
    redis_client = TypedRedis(get_redis())
    removed = await clear_dlq_entry(redis_client, entry_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"DLQ entry {entry_id} not found")
    return {"status": "discarded", "entry_id": entry_id}
