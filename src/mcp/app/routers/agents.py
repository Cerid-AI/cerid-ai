# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Agent endpoints — thin wrappers over agent modules."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

import config
from app.concurrency import KB_POOL
from app.deps import get_chroma, get_graph_store, get_neo4j, get_redis
from app.services.ingestion import ingest_content, validate_file_path
from app.services.private_mode import private_blocks, saves_blocked
from config.features import require_feature
from core.utils.swallowed import log_swallowed_error


# --- Response models (generated: single-return dict-literal routes) ---
class TriageBatchEndpointResponse(BaseModel):
    total: Any
    succeeded: Any
    failed: Any
    duplicates: Any
    results: Any


class ClaimFeedbackEndpointResponse(BaseModel):
    status: str


class SaveVerificationReportResponse(BaseModel):
    status: str
    report_id: Any



router = APIRouter()
logger = logging.getLogger("ai-companion")


def _verified_memory_fn(create_fn: Any) -> Any:
    """Return ``create_fn``, or ``None`` when private mode blocks promotion.

    Shared by ``/agent/hallucination`` and ``/agent/verify-stream`` — both
    dispatch verified-fact-to-memory promotion via an injected
    ``create_memory_fn`` (see ``core.agents.verified_memory.promote_verified_facts``).

    Bare ``None`` — not a no-op callable — is required: both call sites in
    ``core.agents.hallucination.streaming`` gate the entire
    ``promote_verified_facts`` dispatch on ``create_memory_fn is not None``,
    and ``promote_verified_facts`` itself re-checks the same identity before
    doing anything. A callable that merely returns ``None`` would still pass
    both guards and reach the unconditional Chroma-ingest step that follows
    the Neo4j write attempt, leaking the raw claim text into the
    "conversations" collection regardless of what the Neo4j write did.
    Passing ``None`` itself skips promotion — and both its writes — entirely.
    """
    return None if private_blocks(1) else create_fn


class AgentQueryRequest(BaseModel):
    query: str
    domains: list[str] | None = None
    top_k: int = Field(10, ge=1, le=100)
    use_reranking: bool = True
    conversation_messages: list[dict[str, str]] | None = None
    response_text: str | None = Field(None, description="LLM response text for Self-RAG validation")
    model: str | None = Field(None, description="Generating model (for Self-RAG metadata)")
    enable_self_rag: bool | None = Field(None, description="Override Self-RAG toggle (None = use server config)")
    cost_sensitivity: str | None = Field(
        None,
        description=(
            "Cost preference: 'low' | 'medium' | 'high'. When None it is resolved "
            "via the request -> consumer-registry -> persisted COST_SENSITIVITY "
            "chain (app.services.request_policy.resolve_cost_sensitivity, E1 CR-028). "
            "Retrieval ranking itself is not cost-routed; the resolved value steers "
            "LLM model selection on the chat/generation paths."
        ),
    )
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
    exclude_packs: bool = Field(False, description="When True, drop knowledge-pack chunks from retrieval (personal-first: answer from your own data only). Slice 7.3.")
    budget_seconds: float | None = Field(
        None, ge=1, le=120,
        description=(
            "Per-request retrieval wall-clock budget override (seconds). "
            "None = the server's interactive default (AGENT_QUERY_BUDGET_"
            "SECONDS, 20s). For offline/batch callers — eval harnesses, SDK "
            "batch jobs — that prefer completeness over latency."
        ),
    )
    # --- Context source gates (absolute on/off per source) ---
    context_sources: dict | None = Field(
        None,
        description="Source gates: {kb: bool, memory: bool, external: bool}. "
                    "None = all enabled. Disabled sources skip retrieval entirely.",
    )
    rag_mode: str = Field("manual", description="Retrieval mode: manual | smart | custom_smart")
    source_config: dict | None = Field(None, description="Source weights/toggles for custom_smart mode")


class AgentQueryResponse(BaseModel):
    """Response from ``POST /agent/query`` — the canonical retrieval envelope.

    ``extra="allow"`` so the agent pipeline can evolve its return shape (surface
    metadata, timings, CRAG/Self-RAG fields, cache markers)
    without breaking the typed contract — mirrors the SDK response models. Phase 1
    typed the producer that ``/sdk/v1/query`` delegates to (audit ACG-2 / RPB-4).
    """

    model_config = ConfigDict(extra="allow")

    context: str = Field(default="", description="Assembled context string")
    sources: list[dict[str, Any]] = Field(default_factory=list, description="Result chunks with relevance + metadata")
    confidence: float = Field(default=0.0, description="Average relevance of returned sources")
    results: list[dict[str, Any]] = Field(default_factory=list, description="All results with full metadata")
    domains_searched: list[str] = Field(default_factory=list, description="Domains actually searched")
    total_results: int = Field(default=0, description="Total results after dedup + filtering")


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


class HallucinationMode(str, Enum):
    """Verification depth for ``/agent/hallucination``.

    Class invariant: post-fact annotation handlers shouldn't make every
    caller pay full NLI cost. Each mode is a distinct point on the
    cost/quality curve, exposed explicitly so callers don't burn 60-100s
    waiting on cross-model verification when a 2s heuristic is enough.
    """

    FAST = "fast"          # Claim extraction only — no cross-model NLI
    THOROUGH = "thorough"  # Full extraction + cross-model NLI verification


class HallucinationCheckRequest(BaseModel):
    response_text: str
    # ``min_length=1`` enforces the requirement at Pydantic-validation time
    # rather than at handler runtime. Without it an empty value reached the
    # handler's "if not conversation_id" 422 branch — a guaranteed 422 that
    # cost a request slot and a round-trip. With it Pydantic rejects up-front
    # and the constraint is visible in the OpenAPI spec (the
    # ``sdk-openapi-drift`` gate keeps it stable across releases).
    conversation_id: str = Field(..., min_length=1, description="Required conversation identifier")
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    model: str | None = None
    user_query: str | None = None
    expert_mode: bool = False
    mode: HallucinationMode = Field(
        default=HallucinationMode.THOROUGH,
        description=(
            "Verification depth. ``fast`` extracts claims and returns them "
            "marked ``status='uncertain'`` with ``verification_skipped=True`` —"
            " ~ms latency, no NLI calls, useful for post-fact annotations. "
            "``thorough`` (default) runs the full cross-model NLI pipeline."
        ),
    )
    # Sprint C: auto-persist to Neo4j :VerificationReport on successful
    # return. Default True collapses the old two-endpoint dance
    # (/agent/hallucination then /verification/save) into one call.
    # External SDK consumers that manage their own persistence can
    # opt out with persist=False; the standalone /verification/save
    # remains available for that path.
    persist: bool = True


class MemoryExtractionRequest(BaseModel):
    response_text: str
    # See HallucinationCheckRequest.conversation_id — same systemic shape.
    conversation_id: str = Field(..., min_length=1, description="Required conversation identifier")
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


@router.post("/chat/compress", response_model=dict[str, Any])
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
            log_swallowed_error('app.routers.agents', exc)
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/query", response_model=AgentQueryResponse)
async def agent_query_endpoint(req: AgentQueryRequest, request: Request):
    # Private Mode L2 ("skip KB") — server-side enforcement. A direct API
    # caller must get the same query-only behavior the web client applies
    # locally: no KB retrieval at all, not even a cache lookup.
    if private_blocks(2):
        # E1 CR-032/062: empty envelope only — no write-only kb_bypassed stamp.
        return {
            "context": "",
            "sources": [],
            "results": [],
            "domains_searched": [],
            "total_results": 0,
            "confidence": 0.0,
        }
    # Heavy RAG path is gated by KB_POOL so /health, /observability, and
    # other lightweight routes served by HEALTH_POOL are never starved by
    # concurrent KB queries (audit RC-C, smoke Test G).
    async with KB_POOL.acquire():
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

        # Consumer domain isolation: resolve the consumer BEFORE the cache check
        # so the result cache is keyed by the consumer's effective domain scope
        # (E1 CR-001). Otherwise a strict consumer is served the unrestricted
        # 'gui' result on a cache HIT — the isolation applied downstream at
        # query_agent.py:2357 is bypassed entirely on a warm cache.
        from config.settings import CONSUMER_REGISTRY
        from utils.query_cache import get_cached, set_cached
        client_id = request.headers.get("x-client-id", "gui")
        consumer = CONSUMER_REGISTRY.get(client_id, CONSUMER_REGISTRY.get("_default", {}))
        allowed_domains = consumer.get("allowed_domains")
        # Per-request strict_domains can only tighten (True), never loosen the consumer default
        consumer_strict = consumer.get("strict_domains", False)
        strict_domains = req.strict_domains if req.strict_domains else consumer_strict
        consumer_scope = ",".join(sorted(allowed_domains)) if allowed_domains else "__all__"

        has_context = bool(req.conversation_messages)
        domain_key = f"{','.join(sorted(req.domains)) if req.domains else 'all'}|rerank={req.use_reranking}"
        # E1 R16 / CR-001: C1 exact-match key must isolate Memory ON/OFF,
        # context_sources, rag_mode, and scoped retrieval. Mirror C2: skip C1
        # entirely when metadata_filter / exclude_packs narrow the result set
        # (a general-key hit would cross that wall).
        _cs = req.context_sources or {}
        _mem = "1" if _cs.get("memory", True) is not False else "0"
        _kb = "1" if _cs.get("kb", True) is not False else "0"
        _ext = "1" if _cs.get("external", True) is not False else "0"
        _c1_scoped = bool(req.metadata_filter) or bool(req.exclude_packs)
        if req.metadata_filter:
            import json as _json
            _mf = _json.dumps(req.metadata_filter, sort_keys=True, default=str)
        else:
            _mf = ""
        c1_hint = (
            f"{consumer_scope}|mem={_mem}|kb={_kb}|ext={_ext}"
            f"|rag={req.rag_mode or 'manual'}|mf={_mf}"
        )
        if not has_context and not req.skip_cache and not _c1_scoped:
            cached = get_cached(req.query, domain_key, req.top_k, context_hint=c1_hint)
            if cached:
                # ``get_cached`` stamps ``cached: True`` + ``cache_age_ms`` on
                # the payload; the metrics middleware reads the body and sets
                # ``X-Cache: HIT`` so dashboards/smoke harnesses can distinguish
                # warm from cold without timing the call (audit RC-G).
                return cached

        # Workstream E Phase 0: per-request header overrides the default;
        # absent header → ENABLE_STEP_TIMING env (default true) so production
        # traces always include per-stage elapsed times for the cost
        # telemetry contract.
        _dbg_header = request.headers.get("X-Debug-Timing", "").lower()
        debug_timing = (
            _dbg_header == "true" if _dbg_header in ("true", "false")
            else config.ENABLE_STEP_TIMING
        )

        if req.rag_mode in ("smart", "custom_smart"):
            from app.agents.retrieval_orchestrator import orchestrated_query
            from core.agents.self_rag import maybe_self_rag
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
                graph_store=get_graph_store(),
                source_config=req.source_config,
                context_sources=req.context_sources,
                debug_timing=debug_timing,
                allowed_domains=allowed_domains,
                strict_domains=strict_domains,
                model=req.model,
                exclude_packs=req.exclude_packs,
                # E1 CR-009: the smart branch previously dropped these three
                # (the manual branch forwarded them), so a document-scoped or
                # skip_cache/budget-overridden request silently searched the
                # whole KB with the semantic cache live. orchestrated_query's
                # **kwargs propagate them into agent_query.
                skip_cache=req.skip_cache,
                metadata_filter=req.metadata_filter,
                budget_seconds=req.budget_seconds,
            )
            # Smart mode shares Self-RAG with the manual path (agent_query_full).
            # E1 CR-032: low_confidence stamp removed (write-only; no production readers).
            result = await maybe_self_rag(
                result,
                req.response_text,
                req.enable_self_rag,
                chroma_client=get_chroma(),
                neo4j_driver=get_neo4j(),
                redis_client=get_redis(),
                model=req.model,
            )
        else:
            # Manual mode → the canonical full agentic-retrieval path. The KB
            # gate, core retrieval, CRAG external augmentation, low-confidence
            # stamp, and Self-RAG all live in core.agent_query_full now (Phase 1);
            # the wrapper just supplies the store handles + header-derived
            # isolation params, so MCP/A2A/custom-agent callers reach the SAME path.
            from core.agents.query_agent import agent_query_full
            result = await agent_query_full(
                query=req.query,
                domains=req.domains,
                top_k=req.top_k,
                use_reranking=req.use_reranking,
                conversation_messages=req.conversation_messages,
                chroma_client=get_chroma(),
                redis_client=get_redis(),
                neo4j_driver=get_neo4j(),
                graph_store=get_graph_store(),
                debug_timing=debug_timing,
                allowed_domains=allowed_domains,
                strict_domains=strict_domains,
                model=req.model,
                skip_cache=req.skip_cache,
                metadata_filter=req.metadata_filter,
                exclude_packs=req.exclude_packs,
                kb_enabled=_cs.get("kb", True) is not False,
                external_augmentation=_cs.get("external", True),
                response_text=req.response_text,
                enable_self_rag=req.enable_self_rag,
                budget_seconds=req.budget_seconds,
                # E1 CR-016: honor the context_sources.memory opt-out on the
                # manual path (the FE 'always' RAG mode lands here). Previously
                # only kb/external were read, so a user who toggled Memory off
                # still had recalled personal memories enter their answer.
                memory_enabled=_cs.get("memory", True) is not False,
            )

        # Envelope invariant (preservation I2): every /agent/query response
        # carries source_breakdown. The orchestrated path builds the full
        # kb/memory/external split; the manual path only picked one up when
        # low confidence detoured through CRAG enrichment — high-confidence
        # answers on a rich corpus returned a slim envelope (surfaced by the
        # 2026-07-12 master-instance preservation run; CI's near-empty corpus
        # always took the enriched path, hiding it). Manual = kb-only lanes.
        if isinstance(result, dict):
            if "source_breakdown" not in result:
                result["source_breakdown"] = {
                    "kb": result.get("sources", []),
                    "memory": [],
                    "external": [],
                }
            # "strategy" is likewise only stamped on the special paths
            # (conversation_only / disabled / error); the standard success
            # path describes itself by the surface that served it.
            result.setdefault("strategy", result.get("surface_route") or "retrieval")

        # Phase 4.3 — retrieval-quality proxy into the time-series collector
        # /observability/quality reads. Proxy = mean relevance of the returned
        # results. Best-effort; never blocks the response. REST-only observability
        # (utils.metrics is app-bound), so it stays in the wrapper, not in core.
        try:
            _res = result.get("results") if isinstance(result, dict) else None
            if _res:
                _rels = [float(r.get("relevance", 0.0)) for r in _res]
                if _rels:
                    from utils.metrics import get_metrics_collector
                    get_metrics_collector().record_metric(
                        "retrieval_ndcg", round(sum(_rels) / len(_rels), 4),
                        tags={"rag_mode": req.rag_mode or "manual"},
                    )
        except Exception as _exc:  # noqa: BLE001 — metrics recording must never block the query response
            log_swallowed_error("app.routers.agents.retrieval_ndcg", _exc)

        # Never cache a budget-degraded envelope: it holds fallback-only
        # results (kb/memory timed out), and a cache hit would keep serving
        # them after the load transient passes. Also never cache an empty-source
        # result — a transient miss would otherwise poison every matching query
        # for the full TTL (AF-101; mirrors the semantic cache's guard at
        # core/retrieval/semantic_cache.py:293).
        degraded = isinstance(result, dict) and (
            bool(result.get("budget_exceeded")) or not result.get("sources")
        )
        if not has_context and not req.skip_cache and not degraded and not _c1_scoped:
            set_cached(req.query, domain_key, req.top_k, result, context_hint=c1_hint)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Agent query error: {e}")
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/triage")  # response-model-allowed: dynamic response (shape varies)
async def triage_file_endpoint(req: TriageFileRequest):
    try:
        validate_file_path(req.file_path)
        from app.agents.triage import triage_file
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
            # Triage already ran the classifier (domain + sub_category + tags
            # are in metadata) — skip the Phase 5.1 enrichment re-classify.
            enrich=False,
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/triage/batch", response_model=TriageBatchEndpointResponse)
async def triage_batch_endpoint(req: TriageBatchRequest):
    try:
        from app.agents.triage import triage_batch
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
                log_swallowed_error('app.routers.agents', e)
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/hallucination")  # response-model-allowed: dynamic response (shape varies)
@require_feature("truth_audit")
async def hallucination_check_endpoint(req: HallucinationCheckRequest):
    try:
        # Fast mode bypasses the cross-model NLI pipeline entirely — the
        # handler runs claim extraction (the unique value this endpoint
        # provides over a regex check) and returns claims marked
        # ``status="uncertain"`` with ``verification_skipped=True``. The
        # response shape stays identical to the thorough path so consumers
        # don't have to switch models. Useful for post-fact annotations
        # (e.g. trading-agent's ``[KB HALLUCINATION WARNING]`` prefix) that
        # need the extracted claims but don't want to wait 60-100s on NLI.
        if req.mode == HallucinationMode.FAST:
            from core.agents.hallucination.extraction import extract_claims
            from core.utils.time import utcnow_iso
            claims_list, method = await extract_claims(
                req.response_text, user_query=req.user_query,
            )
            uncertain_claims = [
                {
                    "text": c if isinstance(c, str) else (c.get("text") or c.get("claim") or ""),
                    "status": "uncertain",
                    "confidence": 0.0,
                    "verification_skipped": True,
                }
                for c in claims_list
            ]
            return {
                "conversation_id": req.conversation_id,
                "timestamp": utcnow_iso(),
                "skipped": False,
                "reason": None,
                "extraction_method": method,
                "claims": uncertain_claims,
                "summary": {
                    "total": len(uncertain_claims),
                    "verified": 0,
                    "unverified": 0,
                    "uncertain": len(uncertain_claims),
                },
                "mode": "fast",
                "nli_skipped": True,
                "persisted": False,
            }

        from app.db.neo4j.memory import create_memory_node
        from core.agents.hallucination import check_hallucinations

        # Private Mode L1+ ("skip saves") suppresses the verification report's
        # durable twin stores — the Redis hall:{cid} snapshot (gated inside
        # check_hallucinations via persist_report) and the Neo4j auto-persist
        # below — matching the memory-promotion gate (CR-018/086).
        persist_report = not saves_blocked()
        result = await check_hallucinations(
            response_text=req.response_text,
            conversation_id=req.conversation_id,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
            threshold=req.threshold,
            model=req.model,
            user_query=req.user_query,
            expert_mode=req.expert_mode,
            create_memory_fn=_verified_memory_fn(create_memory_node),
            persist_report=persist_report,
        )
        result["mode"] = "thorough"

        # Sprint C auto-persist: collapse the old FE two-call dance
        # (/agent/hallucination -> /verification/save). The standalone
        # /verification/save endpoint stays available for consumers
        # with explicit persistence workflows.
        result["persisted"] = False
        if req.persist and persist_report and not result.get("skipped"):
            claims_payload = result.get("claims") or []
            if claims_payload:
                try:
                    from app.db.neo4j.artifacts import (
                        save_verification_report as _save,
                    )
                    summary = result.get("summary") or {}
                    _save(
                        get_neo4j(),
                        conversation_id=req.conversation_id,
                        claims=claims_payload,
                        overall_score=float(
                            summary.get("overall_confidence")
                            or result.get("overall_score")
                            or 0.0
                        ),
                        verified=int(summary.get("verified", 0)),
                        unverified=int(summary.get("unverified", 0)),
                        uncertain=int(summary.get("uncertain", 0)),
                        total=int(summary.get("total", len(claims_payload))),
                    )
                    result["persisted"] = True
                except Exception:
                    # Persistence failure must not break verification —
                    # the claims are already in the response. Surface
                    # via log_swallowed_error so dashboards catch
                    # systematic save failures.
                    log_swallowed_error(
                        "agent.hallucination.auto_persist",
                        Exception("save_verification_report failed"),
                        context={"conversation_id": req.conversation_id},
                        redis_client=get_redis(),
                    )
                    logger.exception("auto_persist_failed", extra={
                        "conversation_id": req.conversation_id,
                    })

        return result
    except Exception as e:
        logger.error(f"Hallucination check error: {e}")
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.get("/agent/hallucination/{conversation_id}")  # response-model-allowed: dynamic response (shape varies)
@require_feature("truth_audit")
async def hallucination_report_endpoint(conversation_id: str):
    try:
        from core.agents.hallucination import get_hallucination_report
        report = get_hallucination_report(get_redis(), conversation_id)
        if not report:
            raise HTTPException(status_code=404, detail="No hallucination report found")
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hallucination report error: {e}")
        raise HTTPException(status_code=500, detail="Internal error processing request")


class ClaimFeedbackRequest(BaseModel):
    conversation_id: str
    claim_index: int
    correct: bool


@router.post("/agent/hallucination/feedback", response_model=ClaimFeedbackEndpointResponse)
@require_feature("truth_audit")
async def claim_feedback_endpoint(req: ClaimFeedbackRequest):
    """Record user feedback on a verification claim."""
    try:
        from core.agents.hallucination import (
            REDIS_HALLUCINATION_PREFIX,
            REDIS_HALLUCINATION_TTL,
            get_hallucination_report,
        )
        from core.utils.cache import log_claim_feedback

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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/memory/extract")  # response-model-allowed: dynamic response (shape varies)
async def memory_extract_endpoint(req: MemoryExtractionRequest):
    if private_blocks(1):
        return {"stored": False, "skipped": "private_mode"}
    started = time.perf_counter()
    try:
        from app.agents.memory import extract_and_store_memories
        result = await extract_and_store_memories(
            response_text=req.response_text,
            conversation_id=req.conversation_id,
            model=req.model,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "memory_extract_ok",
            extra={
                "stage": "memory_extract",
                "model": req.model,
                "response_len": len(req.response_text or ""),
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )
        return result
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error(
            "memory_extract_failed",
            extra={
                "stage": "memory_extract",
                "model": req.model,
                "elapsed_ms": round(elapsed_ms, 1),
                "exc_type": type(e).__name__,
            },
        )
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/memory/archive")  # response-model-allowed: dynamic response (shape varies)
async def memory_archive_endpoint(req: MemoryArchiveRequest):
    try:
        from app.agents.memory import archive_old_memories
        return await archive_old_memories(
            neo4j_driver=get_neo4j(),
            retention_days=req.retention_days,
        )
    except Exception as e:
        logger.error(f"Memory archive error: {e}")
        raise HTTPException(status_code=500, detail="Internal error processing request")


class MemoryRecallRequest(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.4


class MemoryRecallResponse(BaseModel):
    """Response from ``POST /agent/memory/recall``.

    Wraps the result list in an envelope so consumers can rely on a single
    shape (object) for both empty and non-empty cases. The previous bare-
    list return broke naive ``body.get("result", body)`` parsers — an
    AttributeError counted as an error on every empty success and inflated
    consumers' error rates by tens of thousands of false-positive failures
    (see cerid-trading-agent ``tasks/cerid-ai-interface-issues.md`` § D).

    Class invariant: every endpoint consumed by an external SDK returns an
    object, never a top-level array. Top-level arrays are valid JSON but
    indistinguishable from "actual error" in clients that ``.get()`` on the
    body. The systemic rule is enforced via ``response_model=`` on the
    handler — Pydantic re-serializes the dict shape and FastAPI emits the
    constraint into the OpenAPI spec.
    """

    memories: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Recalled memories scored by relevance + salience decay",
    )
    total: int = Field(default=0, ge=0, description="Number of memories returned (after min_score filter)")


@router.post("/agent/memory/recall", response_model=MemoryRecallResponse)
async def memory_recall_endpoint(req: MemoryRecallRequest):
    """Recall memories relevant to a query."""
    try:
        from app.services.request_policy import build_request_context
        from core.agents.guarded_retrieval import guarded_recall_memories
        results = await guarded_recall_memories(
            request_context=build_request_context(),
            query=req.query,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            top_k=req.top_k,
        )
        filtered = [
            r for r in (results or [])
            if r.get("adjusted_score", r.get("score", 0)) >= req.min_score
        ]
        return MemoryRecallResponse(memories=filtered, total=len(filtered))
    except Exception as e:
        log_swallowed_error('app.routers.agents', e)
        logger.error(f"Memory recall error: {e}")
        # Graceful degradation — empty recall, not 500. Still object-shaped.
        return MemoryRecallResponse(memories=[], total=0)


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
    single_claim_index: int | None = Field(
        None,
        ge=0,
        description=(
            "Set by a per-claim retry: response_text is ONE claim from an existing "
            "N-claim report and its fresh verdict must be MERGED into the durable "
            "hall:{cid} report at this index, not replace it (E1 CR-019)."
        ),
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
@require_feature("truth_audit")
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
            from app.db.neo4j.artifacts import save_verification_report as _save_report
            from app.db.neo4j.memory import create_memory_node as _create_mem_fn
            from core.agents.hallucination import verify_response_streaming

            # Private Mode L1+ ("skip saves") suppresses the report's durable
            # twin stores (Redis hall:{cid} + the Neo4j save_report_fn); both are
            # gated inside verify_response_streaming via persist_report, matching
            # the memory-promotion gate (CR-018).
            persist_report = not saves_blocked()

            logger.info(
                "Verify stream started for conversation=%s (model=%s, query_len=%d)",
                req.conversation_id,
                req.model or "default",
                len(req.user_query or ""),
            )

            # Sprint C auto-persist: bind the Neo4j save helper into a
            # kwargs-only closure so core/streaming.py (which cannot import
            # from app/ by layering rules) can invoke it once the retry
            # sweep + consistency checks settle. get_neo4j() is re-fetched
            # inside the closure to support driver rotation.
            def _save_report_fn(
                *,
                conversation_id: str,
                claims: list[dict],
                overall_score: float,
                verified: int,
                unverified: int,
                uncertain: int,
                total: int,
            ) -> None:
                _save_report(
                    get_neo4j(),
                    conversation_id=conversation_id,
                    claims=claims,
                    overall_score=overall_score,
                    verified=verified,
                    unverified=unverified,
                    uncertain=uncertain,
                    total=total,
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
                create_memory_fn=_verified_memory_fn(_create_mem_fn),
                save_report_fn=_save_report_fn,
                persist_report=persist_report,
                merge_claim_index=req.single_claim_index,
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
                    except (asyncio.CancelledError, Exception) as exc:
                        log_swallowed_error('app.routers.agents', exc)
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
            log_swallowed_error('app.routers.agents', e)
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


@router.post("/verification/save", response_model=SaveVerificationReportResponse)
@require_feature("truth_audit")
async def save_verification_report(req: SaveVerificationRequest):
    """Persist a verification report to Neo4j for long-term storage."""
    # Private Mode L1+ ("skip saves") forbids durably persisting the report's
    # verbatim claims + source snippets; return the success-shaped skip the
    # write-path gating contract uses (CR-086).
    if saves_blocked():
        return {"status": "skipped", "report_id": None}

    from app.db.neo4j.artifacts import save_verification_report as _save

    try:
        # The writer does up to 2N sequential SYNC Neo4j round-trips; offload it
        # so a verification save doesn't block the event loop on every report (CR-022).
        report_id = await asyncio.to_thread(
            _save,
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.get("/verification/{conversation_id}")  # response-model-allowed: dynamic response (shape varies)
@require_feature("truth_audit")
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/rectify")  # response-model-allowed: dynamic response (shape varies)
async def rectify_endpoint(req: RectifyRequest):
    try:
        from core.agents.rectify import rectify
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/audit")  # response-model-allowed: dynamic response (shape varies)
@require_feature("truth_audit")
async def audit_endpoint(req: AuditRequest):
    try:
        from core.agents.audit import audit
        return await audit(
            redis_client=get_redis(),
            reports=req.reports,
            hours=req.hours,
        )
    except Exception as e:
        logger.error(f"Audit error: {e}")
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/maintain")  # response-model-allowed: dynamic response (shape varies)
async def maintain_endpoint(req: MaintenanceRequest):
    try:
        from core.agents.maintenance import maintain
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/curate")  # response-model-allowed: dynamic response (shape varies)
async def curate_endpoint(req: CurateRequest):
    try:
        from app.agents.curator import curate
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
        raise HTTPException(status_code=500, detail="Internal error processing request")


@router.post("/agent/curate/estimate")  # response-model-allowed: dynamic response (shape varies)
async def curate_estimate_endpoint(req: CurateEstimateRequest):
    try:
        from app.agents.curator import estimate_synopsis_run
        return await estimate_synopsis_run(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            model=req.synopsis_model,
            domains=req.domains,
            max_artifacts=req.max_artifacts,
        )
    except Exception as e:
        logger.error(f"Curate estimate error: {e}")
        raise HTTPException(status_code=500, detail="Internal error processing request")
