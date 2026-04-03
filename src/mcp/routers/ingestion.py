# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingestion REST endpoints.

Business logic lives in services/ingestion.py.
This module is a thin router: Pydantic models + endpoint handlers.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import config
from config.features import CERID_WEBHOOK_SECRET
from deps import get_chroma, get_neo4j, get_redis
from errors import IngestionError
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
    items: list[BatchIngestItem] = Field(..., max_length=100)


class WebhookIngestPayload(BaseModel):
    """Payload for the inbound webhook ingestion endpoint."""
    text: str                                  # Required: content to ingest
    source: str                                # Required: source identifier ("zapier", "n8n", "browser-ext")
    domain: str | None = None                  # Optional: target domain
    sub_category: str | None = None            # Optional
    title: str | None = None                   # Optional: artifact title
    metadata: dict[str, str] | None = None     # Optional: additional metadata
    tags: list[str] | None = None              # Optional: tags


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest_endpoint(req: IngestRequest, request: Request):
    client_source = request.headers.get("X-Client-ID", "")
    metadata = {"client_source": client_source} if client_source else None
    async with _ingest_semaphore:
        result = await asyncio.to_thread(ingest_content, req.content, req.domain, metadata)
    try:
        from utils.query_cache import invalidate_cache_non_blocking
        asyncio.get_running_loop().create_task(invalidate_cache_non_blocking())
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.debug(f"Cache invalidation failed (non-blocking): {e}")
    return result


@router.post("/ingest_file")
async def ingest_file_endpoint(req: IngestFileRequest, request: Request):
    try:
        # Run AI triage scoring when enabled (gates low-value content)
        triage_result = None
        if config.ENABLE_AI_TRIAGE:
            try:
                from agents.triage import triage_file

                triage_state = await triage_file(
                    file_path=req.file_path,
                    domain=req.domain,
                    categorize_mode=req.categorize_mode,
                    tags=req.tags,
                )
                triage_result = triage_state.get("triage_result")
            except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                logger.warning("AI triage failed (proceeding without): %s", e)

        async with _ingest_semaphore:
            result = await ingest_file(
                file_path=req.file_path,
                domain=req.domain,
                sub_category=req.sub_category,
                tags=req.tags,
                categorize_mode=req.categorize_mode,
                client_source=request.headers.get("X-Client-ID", ""),
                triage_result=triage_result,
            )
        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Cache invalidation failed (non-blocking): {e}")
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Cache invalidation failed (non-blocking): {e}")

        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Feedback audit log failed (non-blocking): {e}")

        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
            except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                logger.debug(f"Conversation metrics logging failed (non-blocking): {e}")

        return result
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Feedback ingest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest_log")
async def ingest_log_endpoint(limit: int = Query(50, ge=1, le=500)):
    try:
        return cache.get_log(get_redis(), limit=limit)
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Ingest log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/webhook")
async def ingest_webhook(payload: WebhookIngestPayload, request: Request):
    """Inbound webhook endpoint for external tools (Zapier, n8n, browser extensions, scripts).

    Auth: accepts either ``X-API-Key`` or ``X-Webhook-Secret`` header.
    When neither secret is configured server-side, all requests pass through (dev mode).
    Rate-limited per source via ``X-Client-ID: webhook-{source}``.
    """
    api_key_configured = os.getenv("CERID_API_KEY", "")
    webhook_secret_configured = CERID_WEBHOOK_SECRET

    # When at least one secret is configured, require a valid credential
    if api_key_configured or webhook_secret_configured:
        provided_api_key = request.headers.get("X-API-Key", "")
        provided_webhook_secret = request.headers.get("X-Webhook-Secret", "")

        api_key_ok = bool(api_key_configured) and hmac.compare_digest(
            provided_api_key, api_key_configured,
        )
        webhook_ok = bool(webhook_secret_configured) and hmac.compare_digest(
            provided_webhook_secret, webhook_secret_configured,
        )

        if not (api_key_ok or webhook_ok):
            raise HTTPException(status_code=401, detail="Unauthorized — invalid or missing credentials")

    # Resolve domain, falling back to "general"
    domain = payload.domain or "general"

    # Build metadata dict
    meta: dict[str, str] = {
        "webhook_source": payload.source,
        "client_source": f"webhook-{payload.source}",
    }
    if payload.title:
        meta["filename"] = payload.title
    if payload.sub_category:
        meta["sub_category"] = payload.sub_category
    if payload.tags:
        meta["tags_json"] = json.dumps(payload.tags)
    if payload.metadata:
        meta.update(payload.metadata)

    try:
        async with _ingest_semaphore:
            result = await asyncio.to_thread(
                ingest_content,
                payload.text,
                domain,
                meta,
            )

        # Invalidate query cache (non-blocking)
        try:
            from utils.query_cache import invalidate_cache_non_blocking
            asyncio.get_running_loop().create_task(invalidate_cache_non_blocking())
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Cache invalidation failed (non-blocking): {e}")

        return {
            "artifact_id": result.get("artifact_id"),
            "status": result.get("status", "ingested"),
            "domain": result.get("domain", domain),
            "chunks": result.get("chunks", 0),
        }
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Webhook ingest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/clipboard")
async def ingest_clipboard():
    """One-shot clipboard ingestion: runs pbpaste and ingests the result.

    macOS only. Returns 400 if clipboard is empty or too short,
    501 if pbpaste is not available (non-macOS).
    """
    import subprocess
    import sys

    if sys.platform != "darwin":
        raise HTTPException(status_code=501, detail="Clipboard ingestion requires macOS (pbpaste)")

    try:
        proc = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=501, detail="pbpaste not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="pbpaste timed out")

    text = proc.stdout.strip()
    if not text or len(text) < 50:
        raise HTTPException(status_code=400, detail=f"Clipboard too short ({len(text)} chars, min 50)")
    if len(text) > 50_000:
        raise HTTPException(status_code=400, detail=f"Clipboard too large ({len(text)} chars, max 50000)")

    # Detect content type
    domain = "general"
    code_patterns = ("def ", "function ", "class ", "import ", "const ", "let ", "var ", "=> {")
    if text.startswith(("http://", "https://")):
        domain = "general"  # URL — let triage categorize after fetching
    elif any(p in text for p in code_patterns):
        domain = "code"

    meta = {
        "client_source": "clipboard",
        "webhook_source": "clipboard",
    }

    try:
        async with _ingest_semaphore:
            result = await asyncio.to_thread(ingest_content, text, domain, meta)

        try:
            from utils.query_cache import invalidate_cache_non_blocking
            asyncio.get_running_loop().create_task(invalidate_cache_non_blocking())
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Cache invalidation failed (non-blocking): {e}")

        return {
            "artifact_id": result.get("artifact_id"),
            "status": result.get("status", "ingested"),
            "domain": result.get("domain", domain),
            "chunks": result.get("chunks", 0),
            "source": "clipboard",
            "length": len(text),
        }
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Clipboard ingest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clipboard/status")
async def clipboard_status():
    """Check clipboard daemon heartbeat status.

    Reads Redis key ``cerid:clipboard:alive`` written by the daemon
    every poll cycle with a 10s TTL.  If the key exists the daemon is
    running; otherwise it is stopped or unreachable.
    """
    try:
        r = get_redis()
        heartbeat = r.get("cerid:clipboard:alive")
        if heartbeat:
            return {
                "daemon": "running",
                "last_heartbeat": int(heartbeat),
                "enabled": True,
            }
        # Key absent — daemon not running (or Redis unreachable)
        clipboard_enabled = os.getenv("CERID_CLIPBOARD_ENABLED", "false").lower() == "true"
        return {
            "daemon": "stopped",
            "last_heartbeat": None,
            "enabled": clipboard_enabled,
        }
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Clipboard status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Ingestion progress tracker
# ---------------------------------------------------------------------------

# In-memory progress state — tracks active file ingestions.
# This is intentionally in-process: progress is ephemeral and only
# relevant while ingestion is running.  Redis would be overkill.

_ingestion_progress: dict[str, dict[str, object]] = {}


def update_ingestion_progress(
    filename: str,
    step: str,
    progress: float,
    status: str = "processing",
    error: str | None = None,
) -> None:
    """Called from services/ingestion.py to report file progress."""
    _ingestion_progress[filename] = {
        "filename": filename,
        "step": step,
        "progress": min(100.0, max(0.0, progress)),
        "status": status,
        "error": error,
    }


def clear_ingestion_progress(filename: str) -> None:
    """Remove a file from the progress tracker after completion."""
    _ingestion_progress.pop(filename, None)


@router.get("/ingestion/progress")
async def ingestion_progress():
    """Return current ingestion progress for all active files."""
    files = list(_ingestion_progress.values())
    completed = sum(1 for f in files if f.get("status") in ("done", "error"))
    return {
        "files": files,
        "total_files": len(files),
        "completed_files": completed,
    }
