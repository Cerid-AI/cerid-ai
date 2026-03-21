# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingestion REST endpoints.

Business logic lives in services/ingestion.py.
This module is a thin router: Pydantic models + endpoint handlers.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import config
from deps import get_chroma, get_neo4j, get_redis
from services.ingestion import ingest_batch, ingest_content, ingest_file
from utils import cache
from utils.time import utcnow

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Concurrency limiter for ingestion
_ingest_semaphore = asyncio.Semaphore(3)


# ── Pydantic models ────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    content: str
    domain: str = "general"


class IngestFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    sub_category: str = ""
    tags: str = ""
    categorize_mode: str = ""


class FeedbackIngestRequest(BaseModel):
    user_message: str
    assistant_response: str
    model: str = ""
    conversation_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class BatchIngestItem(BaseModel):
    content: str | None = None
    file_path: str | None = None
    domain: str = ""
    sub_category: str = ""
    tags: str = ""
    categorize_mode: str = ""


class BatchIngestRequest(BaseModel):
    items: list[BatchIngestItem] = Field(..., max_length=20)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest_endpoint(req: IngestRequest):
    async with _ingest_semaphore:
        result = await asyncio.to_thread(ingest_content, req.content, req.domain)
    try:
        from utils.query_cache import invalidate_all
        invalidate_all()
    except Exception as e:
        logger.debug(f"Cache invalidation failed (non-blocking): {e}")
    return result


@router.post("/ingest_file")
async def ingest_file_endpoint(req: IngestFileRequest):
    try:
        async with _ingest_semaphore:
            result = await ingest_file(
                file_path=req.file_path,
                domain=req.domain,
                sub_category=req.sub_category,
                tags=req.tags,
                categorize_mode=req.categorize_mode,
            )
        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception as e:
            logger.debug(f"Cache invalidation failed (non-blocking): {e}")
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ingest file error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest_batch")
async def ingest_batch_endpoint(req: BatchIngestRequest):
    """Ingest up to 20 files/content items concurrently."""
    try:
        # Validate each item has exactly one of content or file_path
        for i, item in enumerate(req.items):
            if item.content and item.file_path:
                raise HTTPException(
                    status_code=400,
                    detail=f"Item {i}: provide either 'content' or 'file_path', not both",
                )
            if not item.content and not item.file_path:
                raise HTTPException(
                    status_code=400,
                    detail=f"Item {i}: must have either 'content' or 'file_path'",
                )

        items = [item.model_dump() for item in req.items]
        result = await ingest_batch(items)

        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception as e:
            logger.debug(f"Cache invalidation failed (non-blocking): {e}")

        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch ingest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/feedback")
async def ingest_feedback_endpoint(req: FeedbackIngestRequest):
    """Ingest a chat turn into the conversations domain for the feedback loop."""
    # Backend gate: reject if feedback loop is disabled server-side
    if not config.ENABLE_FEEDBACK_LOOP:
        return {"status": "skipped", "reason": "Feedback loop disabled (ENABLE_FEEDBACK_LOOP=false)"}

    try:
        convo_prefix = req.conversation_id[:8] if req.conversation_id else "unknown"
        timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{convo_prefix}_{timestamp}"
        content = (
            f"User: {req.user_message}\n\n"
            f"Assistant ({req.model}): {req.assistant_response}"
        )
        metadata = {
            "filename": filename,
            "conversation_id": req.conversation_id,
            "model": req.model,
            "summary": req.user_message[:200],
        }
        async with _ingest_semaphore:
            result = await asyncio.to_thread(ingest_content, content, "conversations", metadata)

        try:
            cache.log_event(
                get_redis(),
                event_type="feedback",
                artifact_id=result.get("artifact_id", ""),
                domain="conversations",
                filename=filename,
                conversation_id=req.conversation_id,
            )
        except Exception as e:
            logger.debug(f"Feedback audit log failed (non-blocking): {e}")

        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception as e:
            logger.debug(f"Cache invalidation failed (non-blocking): {e}")

        # Trigger hallucination check if enabled (async, non-blocking)
        if config.ENABLE_HALLUCINATION_CHECK and result.get("status") == "success":
            try:
                from agents.hallucination import check_hallucinations
                asyncio.get_running_loop().create_task(
                    check_hallucinations(
                        response_text=req.assistant_response,
                        conversation_id=req.conversation_id,
                        chroma_client=get_chroma(),
                        neo4j_driver=get_neo4j(),
                        redis_client=get_redis(),
                        model=req.model,
                    )
                )
            except RuntimeError:
                pass  # no running loop

        # Log conversation metrics if tokens provided
        if req.input_tokens or req.output_tokens:
            try:
                from utils.cache import log_conversation_metrics
                log_conversation_metrics(
                    get_redis(),
                    conversation_id=req.conversation_id,
                    model=req.model,
                    input_tokens=req.input_tokens,
                    output_tokens=req.output_tokens,
                    latency_ms=req.latency_ms,
                )
            except Exception as e:
                logger.debug(f"Conversation metrics logging failed (non-blocking): {e}")

        return result
    except Exception as e:
        logger.error(f"Feedback ingest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest_log")
async def ingest_log_endpoint(limit: int = Query(50, ge=1, le=500)):
    try:
        return cache.get_log(get_redis(), limit=limit)
    except Exception as e:
        logger.error(f"Ingest log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
