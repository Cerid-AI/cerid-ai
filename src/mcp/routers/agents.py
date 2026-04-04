# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Agent endpoints — thin wrappers over agent modules."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import config
from deps import get_chroma, get_neo4j, get_redis
from errors import CeridError
from models.agents_response import (
    AgentQueryResponse,
    CompressResponse,
    HallucinationCheckResponse,
    HallucinationReport,
    MemoryArchiveResponse,
    MemoryExtractionResponse,
    MemoryRecallResponse,
    StatusResponse,
    TradingResponse,
    TriageBatchResponse,
    TriageResponse,
    VerificationReportResponse,
    VerificationSaveResponse,
)
from models.trading import (
    CascadeConfirmRequest,
    HerdDetectRequest,
    KellySizeRequest,
    LongshotSurfaceRequest,
    TradingSignalRequest,
)
from services.ingestion import ingest_content, validate_file_path

router = APIRouter()
logger = logging.getLogger("ai-companion")


class AgentQueryRequest(BaseModel):
    query: str
    domains: list[str] | None = None
    top_k: int = Field(10, ge=1, le=100)
    use_reranking: bool = True
    conversation_messages: list[dict[str, str]] | None = None
    response_text: str | None = Field(None, description="LLM response text for Self-RAG validation")
    model: str | None = Field(None, description="Generating model (for Self-RAG metadata)")
    enable_self_rag: bool | None = Field(None, description="Override Self-RAG toggle (None = use server config)")
    strict_domains: bool | None = Field(None, description="When True, disables cross-domain affinity bleed. None = use consumer default.")
    rag_mode: str | None = Field(None, description="RAG mode: manual, smart, or custom_smart. None = use server default.")
    source_config: dict | None = Field(None, description="Custom Smart source weights/toggles (Pro tier only)")


class TriageFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    categorize_mode: str = ""
    tags: str = ""


class TriageBatchRequest(BaseModel):
    files: list[dict[str, str]]
    default_mode: str = ""


class RectifyRequest(BaseModel):
    checks: list[str] | None = None
    auto_fix: bool = False
    stale_days: int = Field(90, ge=1, le=3650)


class HallucinationCheckRequest(BaseModel):
    response_text: str
    conversation_id: str
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    model: str | None = None


class MemoryRecallRequest(BaseModel):
    query: str
    top_k: int = Field(10, ge=1, le=50)
    min_score: float = Field(0.3, ge=0.0, le=1.0)


class MemoryExtractionRequest(BaseModel):
    response_text: str
    conversation_id: str
    model: str = ""


class MemoryArchiveRequest(BaseModel):
    retention_days: int = Field(180, ge=1, le=3650)


class AuditRequest(BaseModel):
    reports: list[str] | None = None
    hours: int = Field(24, ge=1, le=8760)


class MaintenanceRequest(BaseModel):
    actions: list[str] | None = None
    stale_days: int = Field(90, ge=1, le=3650)
    auto_purge: bool = False


class CurateRequest(BaseModel):
    mode: str = Field("audit", pattern="^(audit)$")
    domains: list[str] | None = None
    max_artifacts: int = Field(200, ge=1, le=1000)
    generate_synopses: bool = False
    synopsis_model: str | None = None


class CurateEstimateRequest(BaseModel):
    synopsis_model: str = ""
    domains: list[str] | None = None
    max_artifacts: int = Field(200, ge=1, le=1000)


class CompressRequest(BaseModel):
    messages: list[dict[str, str]]
    target_tokens: int = Field(ge=100, le=1_000_000)


@router.get("/agents/activity/stream")
async def stream_agent_activity(request: Request):
    """SSE stream of agent activity events."""

    async def event_generator():
        from deps import get_redis
        redis = get_redis()
        pubsub = redis.pubsub()
        pubsub.subscribe("cerid:agent:activity")

        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    yield f"data: {data}\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.1)
        finally:
            pubsub.unsubscribe("cerid:agent:activity")
            pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/compress", response_model=CompressResponse)
async def compress_history_endpoint(req: CompressRequest):
    """Compress conversation history to fit a target token budget.

    Uses LLM summarization for the middle turns while preserving the
    system message and most recent turns verbatim.  Falls back to pure
    sliding-window truncation if the LLM call fails.
    """
    try:
        from utils.context_compression import (
            _estimate_messages_tokens,
            compress_history,
            sliding_window_prune,
        )

        messages = [dict(m) for m in req.messages]
        original_tokens = _estimate_messages_tokens(messages)

        if original_tokens <= req.target_tokens:
            return {
                "messages": messages,
                "original_tokens": original_tokens,
                "compressed_tokens": original_tokens,
            }

        try:
            compressed = await compress_history(messages, req.target_tokens)
        except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.warning("compress_history LLM failed, falling back to sliding window: %s", exc)
            compressed = sliding_window_prune(messages)

        compressed_tokens = _estimate_messages_tokens(compressed)
        return {
            "messages": compressed,
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
        }
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error("Compress history error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/query", response_model=AgentQueryResponse)
async def agent_query_endpoint(req: AgentQueryRequest, request: Request):
    # Private mode: level >= 2 skips KB context injection (return empty context)
    client_id = request.headers.get("X-Client-ID", "unknown")
    try:
        from utils.private_mode import get_private_mode_level
        pm_level = get_private_mode_level(client_id)
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        pm_level = 0

    if pm_level >= 2:
        return {"answer": "", "sources": [], "context": "", "private_mode": True}

    try:
        from utils.query_cache import get_cached, set_cached

        has_context = bool(req.conversation_messages)
        domain_key = f"{','.join(sorted(req.domains)) if req.domains else 'all'}|rerank={req.use_reranking}"
        if not has_context:
            cached = get_cached(req.query, domain_key, req.top_k)
            if cached:
                return cached

        debug_timing = request.headers.get("X-Debug-Timing", "").lower() == "true"

        # Consumer domain isolation: look up allowed_domains and strict_domains
        from config.settings import CONSUMER_REGISTRY
        client_id = request.headers.get("x-client-id", "gui")
        consumer = CONSUMER_REGISTRY.get(client_id, CONSUMER_REGISTRY.get("_default", {}))
        allowed_domains = consumer.get("allowed_domains")
        # Per-request strict_domains can only tighten (True), never loosen the consumer default
        consumer_strict = consumer.get("strict_domains", False)
        strict_domains = req.strict_domains if req.strict_domains else consumer_strict

        # Resolve RAG mode: request > server default
        from config.settings import RAG_ORCHESTRATION_MODE
        rag_mode = req.rag_mode or RAG_ORCHESTRATION_MODE

        # Gate custom_smart behind Pro tier and plugin availability
        if rag_mode == "custom_smart":
            from agents.retrieval_orchestrator import _custom_rag_fn
            from config.features import is_feature_enabled
            if not is_feature_enabled("custom_smart_rag") or _custom_rag_fn is None:
                logger.info(
                    "custom_smart RAG mode requires Pro tier plugin — downgrading to smart mode"
                )
                rag_mode = "smart"  # Graceful downgrade

        if rag_mode in ("smart", "custom_smart"):
            from agents.retrieval_orchestrator import orchestrated_query
            result = await orchestrated_query(
                query=req.query,
                rag_mode=rag_mode,
                domains=req.domains,
                top_k=req.top_k,
                use_reranking=req.use_reranking,
                conversation_messages=req.conversation_messages,
                chroma_client=get_chroma(),
                redis_client=get_redis(),
                neo4j_driver=get_neo4j(),
                source_config=req.source_config,
                debug_timing=debug_timing,
                allowed_domains=allowed_domains,
                strict_domains=strict_domains,
                model=req.model,
            )
        else:
            from agents.query_agent import agent_query
            result = await agent_query(
                query=req.query,
                domains=req.domains,
                top_k=req.top_k,
                use_reranking=req.use_reranking,
                conversation_messages=req.conversation_messages,
                chroma_client=get_chroma(),
                redis_client=get_redis(),
                neo4j_driver=get_neo4j(),
                debug_timing=debug_timing,
                allowed_domains=allowed_domains,
                strict_domains=strict_domains,
                model=req.model,
            )

        # Self-RAG: validate claims and refine retrieval if enabled
        use_self_rag = req.enable_self_rag if req.enable_self_rag is not None else config.ENABLE_SELF_RAG
        if use_self_rag and req.response_text:
            from agents.self_rag import self_rag_enhance
            result = await self_rag_enhance(
                query_result=result,
                response_text=req.response_text,
                chroma_client=get_chroma(),
                neo4j_driver=get_neo4j(),
                redis_client=get_redis(),
                model=req.model,
            )

        if not has_context:
            set_cached(req.query, domain_key, req.top_k, result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Agent query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage", response_model=TriageResponse)
async def triage_file_endpoint(req: TriageFileRequest):
    try:
        validate_file_path(req.file_path)
        from agents.triage import triage_file
        triage_result = await triage_file(
            file_path=req.file_path,
            domain=req.domain,
            categorize_mode=req.categorize_mode,
            tags=req.tags,
        )
        if triage_result.get("status") == "error":
            raise HTTPException(status_code=400, detail=triage_result.get("error", "Triage failed"))
        result = ingest_content(
            triage_result["parsed_text"],
            triage_result["domain"],
            metadata=triage_result["metadata"],
        )
        result["filename"] = triage_result["filename"]
        result["categorize_mode"] = triage_result.get("categorize_mode", "")
        result["triage_status"] = triage_result["status"]
        result["is_structured"] = triage_result.get("is_structured", False)
        return result
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage/batch", response_model=TriageBatchResponse)
async def triage_batch_endpoint(req: TriageBatchRequest):
    try:
        from agents.triage import triage_batch
        triage_results = await triage_batch(
            files=req.files,
            default_mode=req.default_mode,
        )
        final_results = []
        for triage_result in triage_results:
            if triage_result.get("status") == "error":
                final_results.append({
                    "filename": triage_result.get("filename", ""),
                    "status": "error",
                    "error": triage_result.get("error", ""),
                })
                continue
            try:
                result = ingest_content(
                    triage_result["parsed_text"],
                    triage_result["domain"],
                    metadata=triage_result["metadata"],
                )
                result["filename"] = triage_result["filename"]
                result["triage_status"] = triage_result["status"]
                final_results.append(result)
            except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                final_results.append({
                    "filename": triage_result.get("filename", ""),
                    "status": "error",
                    "error": str(e),
                })
        succeeded = sum(1 for r in final_results if r.get("status") == "success")
        failed = sum(1 for r in final_results if r.get("status") == "error")
        duplicates = sum(1 for r in final_results if r.get("status") == "duplicate")
        return {
            "total": len(final_results),
            "succeeded": succeeded,
            "failed": failed,
            "duplicates": duplicates,
            "results": final_results,
        }
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Batch triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/hallucination", response_model=HallucinationCheckResponse)
async def hallucination_check_endpoint(req: HallucinationCheckRequest):
    try:
        from agents.hallucination import check_hallucinations
        return await check_hallucinations(
            response_text=req.response_text,
            conversation_id=req.conversation_id,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
            threshold=req.threshold,
            model=req.model,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Hallucination check error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agent/hallucination/{conversation_id}", response_model=HallucinationReport)
async def hallucination_report_endpoint(conversation_id: str):
    try:
        from agents.hallucination import get_hallucination_report
        report = get_hallucination_report(get_redis(), conversation_id)
        if not report:
            raise HTTPException(status_code=404, detail="No hallucination report found")
        return report
    except HTTPException:
        raise
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Hallucination report error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ClaimFeedbackRequest(BaseModel):
    conversation_id: str
    claim_index: int
    correct: bool


@router.post("/agent/hallucination/feedback", response_model=StatusResponse)
async def claim_feedback_endpoint(req: ClaimFeedbackRequest):
    """Record user feedback on a verification claim."""
    try:
        from agents.hallucination import REDIS_HALLUCINATION_PREFIX, REDIS_HALLUCINATION_TTL, get_hallucination_report
        from utils.cache import log_claim_feedback

        redis = get_redis()
        report = get_hallucination_report(redis, req.conversation_id)
        if not report:
            raise HTTPException(status_code=404, detail="No hallucination report found")

        if req.claim_index < 0 or req.claim_index >= len(report.get("claims", [])):
            raise HTTPException(status_code=400, detail="Invalid claim index")

        # Update claim with user feedback
        feedback_value = "correct" if req.correct else "incorrect"
        report["claims"][req.claim_index]["user_feedback"] = feedback_value

        # Write updated report back to Redis
        key = f"{REDIS_HALLUCINATION_PREFIX}{req.conversation_id}"
        redis.setex(key, REDIS_HALLUCINATION_TTL, json.dumps(report))

        # Log feedback for analytics
        model = report.get("model")
        log_claim_feedback(redis, req.conversation_id, req.claim_index, req.correct, model)

        return {"status": "ok"}
    except HTTPException:
        raise
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Claim feedback error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/memory/recall", response_model=MemoryRecallResponse)
async def memory_recall_endpoint(req: MemoryRecallRequest):
    """Recall relevant memories with salience-aware scoring.

    Used by the frontend in manual mode for explicit memory browsing,
    and internally by the orchestrator in smart modes.
    """
    try:
        from agents.memory import recall_memories
        from utils.time import utcnow_iso

        results = await recall_memories(
            query=req.query,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            top_k=req.top_k,
            min_score=req.min_score,
        )
        return {
            "memories": [
                {
                    "id": m.get("memory_id", ""),
                    "text": m.get("text", ""),
                    "score": m.get("adjusted_score", 0.0),
                    "access_count": m.get("access_count", 0),
                    "memory_type": m.get("memory_type", "empirical"),
                    "age_days": m.get("age_days", 0.0),
                    "source_authority": m.get("source_authority", 0.7),
                    "summary": m.get("summary", ""),
                    "base_similarity": m.get("base_similarity", 0.0),
                    "created_at": "",
                }
                for m in results
            ],
            "total_recalled": len(results),
            "timestamp": utcnow_iso(),
        }
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Memory recall error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/memory/extract", response_model=MemoryExtractionResponse)
async def memory_extract_endpoint(req: MemoryExtractionRequest):
    try:
        from agents.memory import extract_and_store_memories
        return await extract_and_store_memories(
            response_text=req.response_text,
            conversation_id=req.conversation_id,
            model=req.model,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Memory extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/memory/archive", response_model=MemoryArchiveResponse)
async def memory_archive_endpoint(req: MemoryArchiveRequest):
    try:
        from agents.memory import archive_old_memories
        return await archive_old_memories(
            neo4j_driver=get_neo4j(),
            retention_days=req.retention_days,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Memory archive error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class VerifyStreamRequest(BaseModel):
    response_text: str
    conversation_id: str
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    model: str | None = None
    user_query: str | None = Field(None, description="Original user query for evasion detection")
    conversation_history: list[dict[str, str]] | None = Field(
        None, description="Prior conversation messages for consistency checking"
    )
    expert_mode: bool = Field(False, description="Use expert-tier model (Grok 4) for all verification")
    source_artifact_ids: list[str] = Field(
        default_factory=list,
        description="KB artifact IDs that were injected into the LLM prompt (anti-circularity)",
    )


_STREAM_END = object()  # Sentinel for generator exhaustion


async def _safe_anext(gen):  # type: ignore[no-untyped-def]
    """Advance an async generator, returning ``_STREAM_END`` on exhaustion.

    This **must** be a regular async function — not an async generator — so
    that ``StopAsyncIteration`` raised by ``gen.__anext__()`` is caught
    normally.  PEP 479 converts ``StopAsyncIteration`` into ``RuntimeError``
    inside async generator frames, which is exactly the bug this helper
    exists to avoid.
    """
    try:
        return await gen.__anext__()
    except StopAsyncIteration:
        return _STREAM_END


@router.post("/agent/verify-stream")
async def verify_stream_endpoint(req: VerifyStreamRequest):
    """SSE endpoint for streaming truth verification of an LLM response.

    Includes keepalive heartbeats (SSE comments) every 15s during long
    verification phases to prevent intermediary proxies and browsers from
    closing idle connections prematurely.
    """

    async def event_generator():
        event_count = 0
        keepalive_count = 0
        try:
            from agents.hallucination import verify_response_streaming

            logger.info(
                "Verify stream started for conversation=%s (model=%s, query_len=%d)",
                req.conversation_id,
                req.model or "default",
                len(req.user_query or ""),
            )

            gen = verify_response_streaming(
                response_text=req.response_text,
                conversation_id=req.conversation_id,
                chroma_client=get_chroma(),
                neo4j_driver=get_neo4j(),
                redis_client=get_redis(),
                threshold=req.threshold,
                model=req.model,
                user_query=req.user_query,
                conversation_history=req.conversation_history,
                expert_mode=req.expert_mode,
                source_artifact_ids=req.source_artifact_ids,
            )

            # Read events with a keepalive timeout — if no event arrives
            # within 15s, emit an SSE comment to keep the connection alive.
            # NOTE: _safe_anext is a regular async function (not a generator)
            # to avoid PEP 479 converting StopAsyncIteration → RuntimeError
            # inside this async generator frame.
            anext_task: asyncio.Task | None = None
            try:
                while True:
                    if anext_task is None:
                        anext_task = asyncio.ensure_future(_safe_anext(gen))
                    done, _ = await asyncio.wait({anext_task}, timeout=15.0)
                    if done:
                        event = anext_task.result()
                        if event is _STREAM_END:
                            logger.info(
                                "Verify stream completed for conversation=%s "
                                "(events=%d, keepalives=%d)",
                                req.conversation_id,
                                event_count,
                                keepalive_count,
                            )
                            break
                        event_count += 1
                        yield f"data: {json.dumps(event)}\n\n"
                        anext_task = None
                    else:
                        # No event in 15s — emit SSE keepalive comment
                        keepalive_count += 1
                        logger.debug(
                            "Verify stream keepalive #%d for conversation=%s",
                            keepalive_count,
                            req.conversation_id,
                        )
                        yield ": keepalive\n\n"
            finally:
                # Cancel the pending anext task and wait for it to finish
                # before closing the generator.  If we call gen.aclose()
                # while the generator is still mid-yield (e.g. Starlette
                # cancelled our request), we get:
                #   RuntimeError: aclose(): asynchronous generator is already running
                if anext_task and not anext_task.done():
                    anext_task.cancel()
                    try:
                        await anext_task
                    except (asyncio.CancelledError, CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
                        logger.debug("Agent anext task cleanup: %s", exc)
                # Now the generator is idle — safe to close
                try:
                    await gen.aclose()
                except (RuntimeError, asyncio.CancelledError, GeneratorExit) as exc:
                    # RuntimeError: generator still running despite cancel-wait
                    # CancelledError: cancel scope still active during cleanup
                    # GeneratorExit: nested generator cleanup during our own exit
                    logger.debug("Generator close cleanup: %s", exc)

        except GeneratorExit:
            # Client disconnected — Starlette closed the async generator.
            # GeneratorExit is a BaseException, not caught by except Exception.
            # We MUST re-raise it (async generators cannot suppress it).
            logger.info(
                "Verify stream client disconnected for conversation=%s "
                "(events=%d)",
                req.conversation_id,
                event_count,
            )
            raise
        except asyncio.CancelledError:
            # Request was aborted (user navigated away, frontend abort()).
            # This is normal — not an error.
            logger.info(
                "Verify stream cancelled for conversation=%s",
                req.conversation_id,
            )
        except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.error(
                "Verify stream error for conversation=%s: %s",
                req.conversation_id,
                e,
                exc_info=True,
            )
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Verification persistence
# ---------------------------------------------------------------------------

class SaveVerificationRequest(BaseModel):
    conversation_id: str
    claims: list[dict]
    overall_score: float = Field(ge=0.0, le=1.0)
    verified: int = 0
    unverified: int = 0
    uncertain: int = 0
    total: int = 0


@router.post("/verification/save", response_model=VerificationSaveResponse)
async def save_verification_report(req: SaveVerificationRequest):
    """Persist a verification report to Neo4j for long-term storage."""
    from db.neo4j.artifacts import save_verification_report as _save

    try:
        report_id = _save(
            get_neo4j(),
            conversation_id=req.conversation_id,
            claims=req.claims,
            overall_score=req.overall_score,
            verified=req.verified,
            unverified=req.unverified,
            uncertain=req.uncertain,
            total=req.total,
        )
        return {"status": "saved", "report_id": report_id}
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error("Failed to save verification report: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verification/{conversation_id}", response_model=VerificationReportResponse)
async def get_verification_report(conversation_id: str):
    """Retrieve a saved verification report by conversation ID."""
    from db.neo4j.artifacts import get_verification_report as _get

    try:
        report = _get(get_neo4j(), conversation_id)
        if report is None:
            raise HTTPException(status_code=404, detail="No verification report found")
        return report
    except HTTPException:
        raise
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error("Failed to get verification report: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/rectify")
async def rectify_endpoint(req: RectifyRequest):
    try:
        from agents.rectify import rectify
        return await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            checks=req.checks,
            auto_fix=req.auto_fix,
            stale_days=req.stale_days,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Rectify error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/audit")
async def audit_endpoint(req: AuditRequest):
    try:
        from agents.audit import audit
        return await audit(
            redis_client=get_redis(),
            reports=req.reports,
            hours=req.hours,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Audit error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/maintain")
async def maintain_endpoint(req: MaintenanceRequest):
    try:
        from agents.maintenance import maintain
        return await maintain(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            actions=req.actions,
            stale_days=req.stale_days,
            auto_purge=req.auto_purge,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Maintenance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/curate")
async def curate_endpoint(req: CurateRequest):
    try:
        from agents.curator import curate
        return await curate(
            neo4j_driver=get_neo4j(),
            mode=req.mode,
            domains=req.domains,
            max_artifacts=req.max_artifacts,
            chroma_client=get_chroma() if req.generate_synopses else None,
            generate_synopses=req.generate_synopses,
            synopsis_model=req.synopsis_model,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Curate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/curate/estimate")
async def curate_estimate_endpoint(req: CurateEstimateRequest):
    try:
        from agents.curator import estimate_synopsis_run
        return estimate_synopsis_run(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            model=req.synopsis_model,
            domains=req.domains,
            max_artifacts=req.max_artifacts,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Curate estimate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Trading agent endpoints (cerid-trading-agent KB enrichment)
# ---------------------------------------------------------------------------

@router.post("/agent/trading/signal", response_model=TradingResponse)
async def trading_signal_endpoint(req: TradingSignalRequest):
    """Enrich a trading signal with KB context."""
    try:
        from agents.trading_agent import trading_signal_enrich
        return await trading_signal_enrich(
            query=req.query,
            signal_data=req.signal_data,
            domains=req.domains,
            chroma=get_chroma(),
            neo4j=get_neo4j(),
            top_k=req.top_k,
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Trading signal enrich error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/trading/herd-detect", response_model=TradingResponse)
async def trading_herd_detect_endpoint(req: HerdDetectRequest):
    """Detect herd behavior via correlation graph violations."""
    try:
        from agents.trading_agent import herd_detect
        return await herd_detect(
            asset=req.asset,
            sentiment_data=req.sentiment_data,
            neo4j=get_neo4j(),
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Herd detect error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/trading/kelly-size", response_model=TradingResponse)
async def trading_kelly_size_endpoint(req: KellySizeRequest):
    """Query historical CV_edge for Kelly sizing."""
    try:
        from agents.trading_agent import kelly_size
        return await kelly_size(
            strategy=req.strategy,
            confidence=req.confidence,
            win_loss_ratio=req.win_loss_ratio,
            neo4j=get_neo4j(),
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Kelly size error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/trading/cascade-confirm", response_model=TradingResponse)
async def trading_cascade_confirm_endpoint(req: CascadeConfirmRequest):
    """Confirm cascade pattern against historical data."""
    try:
        from agents.trading_agent import cascade_confirm
        return await cascade_confirm(
            asset=req.asset,
            liquidation_events=req.liquidation_events,
            neo4j=get_neo4j(),
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Cascade confirm error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/trading/longshot-surface", response_model=TradingResponse)
async def trading_longshot_surface_endpoint(req: LongshotSurfaceRequest):
    """Query stored calibration surface from Neo4j."""
    try:
        from agents.trading_agent import longshot_surface_query
        return await longshot_surface_query(
            asset=req.asset,
            date_range=req.date_range,
            neo4j=get_neo4j(),
        )
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Longshot surface error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Enrichment (S1.1 — external source query for a message)
# ---------------------------------------------------------------------------


class EnrichRequest(BaseModel):
    message_id: str
    content: str


class EnrichResult(BaseModel):
    source: str
    snippet: str


class EnrichResponse(BaseModel):
    results: list[EnrichResult]
    source_count: int


@router.post("/agent/enrich", response_model=EnrichResponse)
async def enrich_endpoint(req: EnrichRequest):
    """Query external data sources with message content for additional context."""
    try:
        from utils.data_sources import DataSourceManager
        mgr = DataSourceManager()
        raw_results = await mgr.query_all(req.content, limit=5)
        results = [
            EnrichResult(source=r.get("source", "unknown"), snippet=r.get("content", "")[:300])
            for r in raw_results
        ]
        return EnrichResponse(results=results, source_count=len(results))
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Enrichment failed: %s", e)
        return EnrichResponse(results=[], source_count=0)
