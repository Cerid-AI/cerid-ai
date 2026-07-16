# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingestion REST endpoints.

Business logic lives in services/ingestion.py.
This module is a thin router: Pydantic models + endpoint handlers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from threading import Lock as _TLock
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from app.deps import get_redis
from app.services.ingestion import ingest_batch, ingest_content, ingest_file
from core.ingest.sources.safe_fetch import guarded_get
from core.knowledge.adapter_html_scrape import extract_html_content
from core.utils import cache
from core.utils.swallowed import log_swallowed_error


# --- Response models (generated: single-return dict-literal routes) ---
class IngestionProgressEndpointResponse(BaseModel):
    files: Any
    total_files: Any
    completed_files: Any



router = APIRouter()
logger = logging.getLogger("ai-companion")

# Concurrency limiter for ingestion (Workstream E Phase 0 — env-configurable
# via INGEST_CONCURRENCY; see config/settings.py).
_ingest_semaphore = asyncio.Semaphore(config.INGEST_CONCURRENCY)

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


class IngestUrlRequest(BaseModel):
    """Payload for the quick-capture URL tab: fetch + ingest a single
    operator-supplied URL (Task 2.3a). One URL → one artifact; no batch."""

    url: str
    domain: str = "general"
    tags: list[str] | None = None


class StructuredIngestRequest(BaseModel):
    """Payload for the Apple connectors and any other client that produces
    artifact-shaped content with arbitrary tag metadata. Differs from
    `IngestRequest` in that arbitrary metadata flows directly into the
    artifact's tags (not just X-Client-ID header)."""

    content: str
    domain: str = "general"
    metadata: dict[str, str] = Field(default_factory=dict)
    source_id: str | None = None  # Idempotency key — Apple Notes id, Mail Message-ID, etc.


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

@router.get("/ingestion/progress", response_model=IngestionProgressEndpointResponse)
def ingestion_progress_endpoint():
    """Return current ingestion pipeline state for the progress UI."""
    with _progress_lock:
        _prune_stale()
        files = [{k: v for k, v in job.items() if k != "_ts"} for job in _active_jobs.values()]
    completed = sum(1 for f in files if f["status"] == "done")
    return {"files": files, "total_files": len(files), "completed_files": completed}


@router.post("/ingest")  # response-model-allowed: dynamic response (shape varies)
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


@router.post("/ingest/structured")  # response-model-allowed: dynamic response (shape varies)
async def ingest_structured_endpoint(req: StructuredIngestRequest, request: Request):
    """Structured-content ingest. Used by the Apple connectors (Notes,
    Mail, Messages) — each maps a source row to one artifact with rich
    tag metadata that retrieval can filter by source.

    Metadata keys are merged into the artifact's tag set. `source_id` is an
    external idempotency hint here — re-ingesting the same source_id is a no-op
    only in the sense that ChromaDB collapses duplicate content_hashes; explicit
    dedup-by-source_id is tracked for Phase D.2. Note (CL-1): the ingest service
    now links an artifact to a :Source node only when `source_id` resolves to a
    real :Source (existence-checked), so an external id passed here can never
    create a dangling FROM_SOURCE edge or spurious source counter — it is simply
    treated as a filterable tag. (Fully reserving source_id for :Source UUIDs and
    routing external ids through `external_id` is a deferred hygiene follow-up;
    the existence check already makes the current overloading safe.)
    """
    client_source = request.headers.get("X-Client-ID", "")
    metadata: dict[str, str] = dict(req.metadata)
    if client_source:
        metadata["client_source"] = client_source
    if req.source_id:
        metadata["source_id"] = req.source_id
    async with _ingest_semaphore:
        result = await asyncio.to_thread(ingest_content, req.content, req.domain, metadata)
    try:
        from utils.query_cache import invalidate_cache_non_blocking
        asyncio.get_running_loop().create_task(invalidate_cache_non_blocking())
    except Exception as e:
        log_swallowed_error("routers.ingestion.ingest_structured_cache_invalidate", e)
    return result


_URL_INGEST_USER_AGENT = "CeridAI-UrlIngest/1.0"


@router.post("/ingest/url")  # response-model-allowed: dynamic response (shape varies)
async def ingest_url_endpoint(req: IngestUrlRequest):
    """Fetch an operator-supplied URL through the SSRF-guarded fetcher and
    ingest the extracted title + text as a single artifact (quick-capture
    URL tab; Task 2.3a — frontend wiring is a separate task).
    """
    try:
        resp = await guarded_get(req.url, user_agent=_URL_INGEST_USER_AGENT)
        resp.raise_for_status()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"URL is not fetchable: {exc}")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch URL: {exc}")

    parsed_url = urlparse(req.url)
    fallback_title = parsed_url.netloc + parsed_url.path
    content_type = resp.headers.get("content-type", "").lower()
    if "html" in content_type:
        title, text = extract_html_content(resp.text)
        title = title or fallback_title
    else:
        text = resp.text.strip()
        title = fallback_title

    if not text.strip():
        raise HTTPException(status_code=422, detail="No extractable text at URL")

    metadata: dict[str, Any] = {
        "source_type": "url",
        "url": req.url,
        "title": title,
    }
    if req.tags:
        clean_tags = [t.strip().lower() for t in req.tags if isinstance(t, str) and t.strip()]
        if clean_tags:
            metadata["tags_json"] = json.dumps(clean_tags)

    async with _ingest_semaphore:
        result = await asyncio.to_thread(
            ingest_content, text, req.domain, metadata, enrich=True,
        )
    try:
        from utils.query_cache import invalidate_cache_non_blocking
        asyncio.get_running_loop().create_task(invalidate_cache_non_blocking())
    except Exception as e:
        log_swallowed_error("routers.ingestion.ingest_url_cache_invalidate", e)
    return result


@router.post("/ingest_file")  # response-model-allowed: dynamic response (shape varies)
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


@router.post("/ingest_batch")  # response-model-allowed: dynamic response (shape varies)
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


@router.post("/ingest/feedback")  # response-model-allowed: dynamic response (shape varies)
async def ingest_feedback_endpoint(req: FeedbackIngestRequest):
    """Queue a chat turn for ingestion into the conversations domain.

    The full ingest (chunk → embed → store) ran inline here until it
    504'd through the web proxy under beta load (2026-07-12 triage).
    The heavy tail now runs as a ``FeedbackIngestJob`` on the background
    processor; the cheap parts (feature gate + conversation-metrics
    write) stay synchronous and the endpoint acks 202 immediately.
    Feedback is fire-and-forget in the UI — callers treat 202 as success.
    """
    # Backend gate: reject if feedback loop is disabled server-side
    if not config.ENABLE_FEEDBACK_LOOP:
        return {"status": "skipped", "reason": "Feedback loop disabled (ENABLE_FEEDBACK_LOOP=false)"}

    # Fast synchronous ack: conversation metrics are a cheap Redis write
    # and power the live usage panes, so they land at request time.
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

    try:
        from app.processor.jobs.feedback_ingest import enqueue_feedback_ingest_job
        job_id = await asyncio.to_thread(
            enqueue_feedback_ingest_job,
            user_message=req.user_message,
            assistant_response=req.assistant_response,
            model=req.model,
            conversation_id=req.conversation_id,
        )
    except Exception as e:
        logger.error(f"Feedback ingest enqueue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse(status_code=202, content={"status": "queued", "job_id": job_id})


@router.get("/ingest_log")  # response-model-allowed: dynamic response (shape varies)
async def ingest_log_endpoint(limit: int = Query(50, ge=1, le=500)):
    try:
        return cache.get_log(get_redis(), limit=limit)
    except Exception as e:
        logger.error(f"Ingest log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
