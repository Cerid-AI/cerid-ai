# Copyright (c) 2026 Justin Michaels. All rights reserved.
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
from app.deps import get_chroma, get_neo4j, get_redis
from app.services.ingestion import ingest_content, validate_file_path

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Limit concurrent CPU-bound agent queries to prevent event loop stalls.
# BM25 tokenization and ONNX embedding/reranking are synchronous and block
# the event loop.  Without this limit, 5+ concurrent /agent/query requests
# (e.g. from auto-inject) starve /chat/stream for 30+ seconds.
_QUERY_SEMAPHORE = asyncio.Semaphore(2)


class AgentQueryRequest(BaseModel):
    query: str
    domains: list[str] | None = None
    top_k: int = Field(10, ge=1, le=100)
    use_reranking: bool = True
    conversation_messages: list[dict[str, str]] | None = None
    response_text: str | None = Field(None, description="LLM response text for Self-RAG validation")
    model: str | None = Field(None, description="Generating model (for Self-RAG metadata)")
    enable_self_rag: bool | None = Field(None, description="Override Self-RAG toggle (None = use server config)")
    # --- Query scope (high-level intent) ---
    # "document" = single-file focus, "domain" = single-domain, "kb" = whole KB (default)
    # Expands into strict_domains / skip_cache / metadata_filter automatically.
    # Individual flags below still work for power users and override scope defaults.
    query_scope: str | None = Field(None, description="Query scope: document | domain | kb (None = kb)")
    scope_ref: str | None = Field(None, description="Scope reference — filename for 'document' scope")
    # --- Individual overrides (set by scope expansion or directly) ---
    strict_domains: bool | None = Field(None, description="When True, disables cross-domain affinity bleed. None = use consumer default.")
    skip_cache: bool = Field(False, description="Bypass semantic cache and query cache (for fresh-data scenarios like setup wizard)")
    metadata_filter: dict | None = Field(None, description="ChromaDB where-clause for metadata filtering (e.g. {\"filename\": \"report.pdf\"})")
    # --- Context source gates (absolute on/off per source) ---
    context_sources: dict | None = Field(
        None,
        description="Source gates: {kb: bool, memory: bool, external: bool}. "
                    "None = all enabled. Disabled sources skip retrieval entirely.",
    )
    rag_mode: str = Field("manual", description="Retrieval mode: manual | smart | custom_smart")
    source_config: dict | None = Field(None, description="Source weights/toggles for custom_smart mode")


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
    mode: str = Field("audit", pattern="^(audit|trim|prune)$")
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


@router.post("/chat/compress")
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
        except Exception as exc:
            logger.warning("compress_history LLM failed, falling back to sliding window: %s", exc)
            compressed = sliding_window_prune(messages)

        compressed_tokens = _estimate_messages_tokens(compressed)
        return {
            "messages": compressed,
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
        }
    except Exception as e:
        logger.error("Compress history error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/query")
async def agent_query_endpoint(req: AgentQueryRequest, request: Request):
    async with _QUERY_SEMAPHORE:
        return await _agent_query_inner(req, request)


async def _agent_query_inner(req: AgentQueryRequest, request: Request):
    try:
        # ── Scope expansion ─────────────────────────────────────────────
        # Expand query_scope into individual flags (only sets defaults;
        # explicit per-field values always win).
        if req.query_scope == "document":
            if req.strict_domains is None:
                req.strict_domains = True
            if not req.skip_cache:
                req.skip_cache = True
            if req.metadata_filter is None and req.scope_ref:
                req.metadata_filter = {"filename": req.scope_ref}
        elif req.query_scope == "domain":
            if req.strict_domains is None:
                req.strict_domains = True

        from utils.query_cache import get_cached, set_cached

        has_context = bool(req.conversation_messages)
        domain_key = f"{','.join(sorted(req.domains)) if req.domains else 'all'}|rerank={req.use_reranking}"
        if not has_context and not req.skip_cache:
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

        if req.rag_mode in ("smart", "custom_smart"):
            from agents.retrieval_orchestrator import orchestrated_query
            result = await orchestrated_query(
                query=req.query,
                rag_mode=req.rag_mode,
                domains=req.domains,
                top_k=req.top_k,
                use_reranking=req.use_reranking,
                conversation_messages=req.conversation_messages,
                chroma_client=get_chroma(),
                redis_client=get_redis(),
                neo4j_driver=get_neo4j(),
                source_config=req.source_config,
                context_sources=req.context_sources,
                debug_timing=debug_timing,
                allowed_domains=allowed_domains,
                strict_domains=strict_domains,
                model=req.model,
            )
        else:
            # Manual mode: KB gate check — if context_sources disables KB, skip retrieval
            _cs = req.context_sources or {}

            # Launch external sources in parallel with KB (if enabled).
            # Runs concurrently — no latency penalty when KB has results.
            _ext_on = _cs.get("external", True)
            _external_task = None
            if _ext_on:
                try:
                    from utils.data_sources import registry
                    _external_task = asyncio.create_task(
                        registry.query_all(
                            req.query,
                            domain=req.domains[0] if req.domains else None,
                            timeout=5.0,
                        )
                    )
                except Exception:
                    pass  # Registry unavailable — skip external

            if _cs.get("kb", True) is False:
                result = {
                    "context": "", "sources": [], "confidence": 0.0,
                    "domains_searched": [], "total_results": 0,
                    "token_budget_used": 0, "graph_results": 0, "results": [],
                    "strategy": "conversation_only",
                    "source_status": {"kb": "disabled"},
                }
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
                    skip_cache=req.skip_cache,
                    metadata_filter=req.metadata_filter,
                )

            # Merge external results (parallel task completes by now)
            if _external_task is not None:
                try:
                    _ext_results = await _external_task
                except Exception:
                    _ext_results = []
                if _ext_results:
                    _DISCOUNT = 0.6
                    for _raw in _ext_results:
                        result.setdefault("results", []).append({
                            "content": _raw.get("content", ""),
                            "relevance": round(
                                _raw.get("confidence", 0.8) * _DISCOUNT, 3,
                            ),
                            "source_url": _raw.get("source_url", ""),
                            "source_name": _raw.get(
                                "source_name", _raw.get("title", ""),
                            ),
                            "source_type": "external",
                            "domain": "external",
                            "artifact_id": "",
                            "filename": _raw.get("source_name", ""),
                            "chunk_id": "",
                            "collection": "external",
                        })
                    result["total_results"] = len(result.get("results", []))
                    if result.get("results"):
                        result["confidence"] = round(
                            sum(r["relevance"] for r in result["results"])
                            / len(result["results"]),
                            4,
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

        if not has_context and not req.skip_cache:
            set_cached(req.query, domain_key, req.top_k, result)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Agent query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage")
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
    except Exception as e:
        logger.error(f"Triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage/batch")
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
            except Exception as e:
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
    except Exception as e:
        logger.error(f"Batch triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/hallucination")
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
    except Exception as e:
        logger.error(f"Hallucination check error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agent/hallucination/{conversation_id}")
async def hallucination_report_endpoint(conversation_id: str):
    try:
        from agents.hallucination import get_hallucination_report
        report = get_hallucination_report(get_redis(), conversation_id)
        if not report:
            raise HTTPException(status_code=404, detail="No hallucination report found")
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hallucination report error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ClaimFeedbackRequest(BaseModel):
    conversation_id: str
    claim_index: int
    correct: bool


@router.post("/agent/hallucination/feedback")
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
    except Exception as e:
        logger.error(f"Claim feedback error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/memory/extract")
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
    except Exception as e:
        logger.error(f"Memory extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/memory/archive")
async def memory_archive_endpoint(req: MemoryArchiveRequest):
    try:
        from agents.memory import archive_old_memories
        return await archive_old_memories(
            neo4j_driver=get_neo4j(),
            retention_days=req.retention_days,
        )
    except Exception as e:
        logger.error(f"Memory archive error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class MemoryRecallRequest(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.4


@router.post("/agent/memory/recall")
async def memory_recall_endpoint(req: MemoryRecallRequest):
    """Recall memories relevant to a query."""
    try:
        from agents.memory import recall_memories
        results = await recall_memories(
            query=req.query,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            top_k=req.top_k,
        )
        # Filter by min_score and return
        return [r for r in (results or []) if r.get("adjusted_score", r.get("score", 0)) >= req.min_score]
    except Exception as e:
        logger.error(f"Memory recall error: {e}")
        return []  # graceful degradation — empty recall, not 500


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
                    except (asyncio.CancelledError, Exception):
                        pass
                # Now the generator is idle — safe to close
                try:
                    await gen.aclose()
                except (RuntimeError, asyncio.CancelledError, GeneratorExit):
                    # RuntimeError: generator still running despite cancel-wait
                    # CancelledError: cancel scope still active during cleanup
                    # GeneratorExit: nested generator cleanup during our own exit
                    pass

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
        except Exception as e:
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


@router.post("/verification/save")
async def save_verification_report(req: SaveVerificationRequest):
    """Persist a verification report to Neo4j for long-term storage."""
    from app.db.neo4j.artifacts import save_verification_report as _save

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
    except Exception as e:
        logger.error("Failed to save verification report: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verification/{conversation_id}")
async def get_verification_report(conversation_id: str):
    """Retrieve a saved verification report by conversation ID."""
    from app.db.neo4j.artifacts import get_verification_report as _get

    try:
        report = _get(get_neo4j(), conversation_id)
        if report is None:
            raise HTTPException(status_code=404, detail="No verification report found")
        return report
    except HTTPException:
        raise
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
        logger.error(f"Curate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/curate/estimate")
async def curate_estimate_endpoint(req: CurateEstimateRequest):
    try:
        from agents.curator import estimate_synopsis_run
        return await estimate_synopsis_run(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            model=req.synopsis_model,
            domains=req.domains,
            max_artifacts=req.max_artifacts,
        )
    except Exception as e:
        logger.error(f"Curate estimate error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
