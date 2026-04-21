# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingestion REST endpoints.

Business logic lives in services/ingestion.py.
This module is a thin router: Pydantic models + endpoint handlers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from threading import Lock as _TLock

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import config
from app.deps import get_chroma, get_neo4j, get_redis
from app.services.ingestion import ingest_batch, ingest_content, ingest_file
from core.utils import cache
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Concurrency limiter for ingestion
_ingest_semaphore = asyncio.Semaphore(3)

# ── In-flight progress tracking ───────────────────────────────────────────────

_progress_lock = _TLock()
_active_jobs: dict[str, dict] = {}
_PRUNE_TTL = 30  # seconds to keep completed/errored entries


def _register_job(filename: str) -> None:
    with _progress_lock:
        _active_jobs[filename] = {
            "filename": filename,
            "step": "parsing",
            "progress": 0,
            "status": "processing",
            "error": None,
            "_ts": time.monotonic(),
        }


def _complete_job(filename: str, *, error: str | None = None) -> None:
    with _progress_lock:
        if filename in _active_jobs:
            _active_jobs[filename]["status"] = "error" if error else "done"
            _active_jobs[filename]["progress"] = 0 if error else 100
            if error:
                _active_jobs[filename]["error"] = error
            _active_jobs[filename]["_ts"] = time.monotonic()


def _prune_stale() -> None:
    now = time.monotonic()
    stale = [
        k for k, v in _active_jobs.items()
        if v["status"] in ("done", "error") and now - v["_ts"] > _PRUNE_TTL
    ]
    for k in stale:
        del _active_jobs[k]


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

@router.get("/ingestion/progress")
def ingestion_progress_endpoint():
    """Return current ingestion pipeline state for the progress UI."""
    with _progress_lock:
        _prune_stale()
        files = [{k: v for k, v in job.items() if k != "_ts"} for job in _active_jobs.values()]
    completed = sum(1 for f in files if f["status"] == "done")
    return {"files": files, "total_files": len(files), "completed_files": completed}


@router.post("/ingest")
async def ingest_endpoint(req: IngestRequest, request: Request):
    client_source = request.headers.get("X-Client-ID", "")
    metadata = {"client_source": client_source} if client_source else None
    async with _ingest_semaphore:
        result = await asyncio.to_thread(ingest_content, req.content, req.domain, metadata)
    try:
        from utils.query_cache import invalidate_cache_non_blocking
        asyncio.get_running_loop().create_task(invalidate_cache_non_blocking())
    except Exception as e:
        log_swallowed_error("routers.ingestion.ingest_cache_invalidate", e)
    return result


@router.post("/ingest_file")
async def ingest_file_endpoint(req: IngestFileRequest, request: Request):
    filename = req.file_path.rsplit("/", 1)[-1] if "/" in req.file_path else req.file_path
    _register_job(filename)
    try:
        async with _ingest_semaphore:
            result = await ingest_file(
                file_path=req.file_path,
                domain=req.domain,
                sub_category=req.sub_category,
                tags=req.tags,
                categorize_mode=req.categorize_mode,
                client_source=request.headers.get("X-Client-ID", ""),
            )
        _complete_job(filename)
        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception as e:
            log_swallowed_error("routers.ingestion.ingest_file_cache_invalidate", e)
        return result
    except FileNotFoundError as e:
        _complete_job(filename, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        _complete_job(filename, error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _complete_job(filename, error=str(e))
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

        # Register all file-based items for progress tracking
        filenames: list[str] = []
        for item in req.items:
            if item.file_path:
                fn = item.file_path.rsplit("/", 1)[-1] if "/" in item.file_path else item.file_path
                _register_job(fn)
                filenames.append(fn)

        items = [item.model_dump() for item in req.items]
        result = await ingest_batch(items)

        for fn in filenames:
            _complete_job(fn)

        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception as e:
            log_swallowed_error("routers.ingestion.ingest_batch_cache_invalidate", e)

        return result
    except ValueError as e:
        for fn in filenames:
            _complete_job(fn, error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        for fn in filenames:
            _complete_job(fn, error=str(e))
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
            log_swallowed_error(
                "routers.ingestion.feedback_audit_log",
                e,
                redis_client=get_redis(),
            )

        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception as e:
            log_swallowed_error(
                "routers.ingestion.feedback_cache_invalidate",
                e,
                redis_client=get_redis(),
            )

        # Trigger hallucination check if enabled (async, non-blocking)
        if config.ENABLE_HALLUCINATION_CHECK and result.get("status") == "success":
            try:
                from core.agents.hallucination import check_hallucinations
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
                from core.utils.cache import log_conversation_metrics
                log_conversation_metrics(
                    get_redis(),
                    conversation_id=req.conversation_id,
                    model=req.model,
                    input_tokens=req.input_tokens,
                    output_tokens=req.output_tokens,
                    latency_ms=req.latency_ms,
                )
            except Exception as e:
                log_swallowed_error(
                    "routers.ingestion.feedback_conversation_metrics",
                    e,
                    redis_client=get_redis(),
                )

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
