# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable SDK endpoints for external cerid-series consumers.

These thin facades delegate to the existing agent/health endpoints,
providing a versioned contract (``/sdk/v1/``) that survives internal
refactoring of the ``/agent/`` paths.

Consumers should send ``X-Client-ID`` for per-client rate limiting and
domain access control.  See ``config.settings.CONSUMER_REGISTRY`` for
the per-consumer configuration and ``docs/INTEGRATION_GUIDE.md`` for
adding new cerid-series consumers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

import config
from app.models.sdk import (
    SDKHallucinationResponse,
    SDKHealthResponse,
    SDKLLMCompleteRequest,
    SDKLLMCompleteResponse,
    SDKMemoryExtractAcceptedResponse,
    SDKMemoryExtractJobStatus,
    SDKMemoryExtractResponse,
    SDKQueryResponse,
)
from app.routers.agents import (
    AgentQueryRequest,
    HallucinationCheckRequest,
    MemoryExtractionRequest,
    agent_query_endpoint,
    hallucination_check_endpoint,
    memory_extract_endpoint,
)
from app.routers.health import degradation_status, health_check, list_collections
from app.routers.plugins import list_plugins
from app.routers.query import query_knowledge
from app.routers.sdk_version import SDK_VERSION
from app.services.ingestion import ingest_content, ingest_file
from config.features import FEATURE_FLAGS, FEATURE_TIER
from config.taxonomy import DOMAINS, TAXONOMY

router = APIRouter(prefix="/sdk/v1", tags=["SDK"])

_503 = {"description": "One or more backend services unavailable"}
_422 = {"description": "Invalid request parameters"}


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=SDKQueryResponse,
    summary="KB Query",
    description="Multi-domain knowledge base search with hybrid BM25+vector retrieval and optional LLM reranking. "
    "Results are scoped by the consumer's allowed_domains in CONSUMER_REGISTRY.",
    responses={422: _422, 503: _503},
)
async def sdk_query(req: AgentQueryRequest, request: Request):
    return await agent_query_endpoint(req, request)


@router.post(
    "/hallucination",
    response_model=SDKHallucinationResponse,
    summary="Hallucination Detection",
    description="Verify factual claims in a response against the KB. Returns per-claim status "
    "(verified/unverified/uncertain) with sources and confidence scores.",
    responses={422: _422, 503: _503},
)
async def sdk_hallucination(req: HallucinationCheckRequest):
    return await hallucination_check_endpoint(req)


@router.post(
    "/memory/extract",
    summary="Memory Extraction",
    description=(
        "Extract facts, decisions, and preferences from conversation text "
        "and store as KB artifacts. Deduplicates against existing memories.\n\n"
        "When ``MEMORY_QUEUE_MODE=async`` is set on the server (and a worker "
        "is running), the default behaviour is **fire-and-forget**: returns "
        "``202 Accepted`` immediately with a ``job_id`` and a ``status_url``. "
        "Poll ``GET /sdk/v1/memory/extract/jobs/{job_id}`` for the result.\n\n"
        "Pass ``?wait=true`` to force the synchronous path (waits for the "
        "full extract → consolidate → store pipeline before responding) — "
        "use for callers that need the result envelope inline."
    ),
    responses={
        200: {"model": SDKMemoryExtractResponse, "description": "Sync result (wait=true or queue disabled)"},
        202: {"model": SDKMemoryExtractAcceptedResponse, "description": "Accepted for async processing"},
        422: _422,
        503: _503,
    },
)
async def sdk_memory_extract(req: MemoryExtractionRequest, wait: bool = False):
    """Default-async endpoint with sync escape hatch.

    Routing:
      * ``wait=true`` OR ``MEMORY_QUEUE_MODE != async``  → sync path
        (existing behaviour: full extract→consolidate→store, returns
        ``SDKMemoryExtractResponse`` 200).
      * Otherwise  → enqueue + 202 ``SDKMemoryExtractAcceptedResponse``.

    The sync escape hatch keeps every existing caller binary-compatible
    until they choose to switch. New callers should default to async and
    poll the status endpoint — that's what the systemic class-of-problem
    answer asks for: post-fact annotation work doesn't belong on the
    request critical path.
    """
    from fastapi.responses import JSONResponse

    from app.queue import get_memory_queue, is_memory_async_mode

    if wait or not is_memory_async_mode():
        return await memory_extract_endpoint(req)

    queue = get_memory_queue()
    job = queue.enqueue(
        "app.queue.tasks.memory_extract_task",
        kwargs={
            "response_text": req.response_text,
            "conversation_id": req.conversation_id,
            "model": req.model,
        },
        # Keep results around long enough for trading-agent's reflection
        # loop (60s scan interval × ~10 cycles before a stale poll = 10 min
        # ceiling). Failed jobs persist for the same window so operators
        # can inspect the traceback via ``rq info``.
        result_ttl=600,
        failure_ttl=600,
    )
    accepted = SDKMemoryExtractAcceptedResponse(
        job_id=job.id,
        status="queued",
        status_url=f"/sdk/v1/memory/extract/jobs/{job.id}",
        conversation_id=req.conversation_id,
    )
    return JSONResponse(
        status_code=202,
        content=accepted.model_dump(),
        headers={"Location": accepted.status_url},
    )


@router.get(
    "/memory/extract/jobs/{job_id}",
    response_model=SDKMemoryExtractJobStatus,
    summary="Memory Extract Job Status",
    description=(
        "Poll for the result of an async memory_extract job. Status "
        "transitions: ``queued → started → finished`` (success) or "
        "``queued → started → failed`` (worker error). The ``result`` "
        "field is populated only when ``status='finished'``; ``error`` "
        "is populated only when ``status='failed'``."
    ),
    responses={404: {"description": "Unknown job_id"}, 503: _503},
)
async def sdk_memory_extract_job_status(job_id: str) -> SDKMemoryExtractJobStatus:
    from app.queue import _rq_available, get_memory_queue

    if not _rq_available:
        raise HTTPException(
            status_code=503,
            detail="Async memory queue is not configured (rq not installed).",
        )

    from rq.exceptions import NoSuchJobError  # type: ignore[import-not-found]
    from rq.job import Job  # type: ignore[import-not-found]

    queue = get_memory_queue()
    try:
        job = Job.fetch(job_id, connection=queue.connection)
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")

    status = job.get_status()  # rq's canonical status string
    payload: dict = {
        "job_id": job_id,
        "status": status or "unknown",
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
        "result": None,
        "error": None,
    }
    if status == "finished" and job.result is not None:
        # The worker returns the same dict shape ``SDKMemoryExtractResponse``
        # describes — Pydantic re-validates here so any drift is loud.
        payload["result"] = SDKMemoryExtractResponse(**job.result).model_dump()
    elif status == "failed":
        payload["error"] = (job.exc_info or "").strip().split("\n")[-1] or "worker failure"

    return SDKMemoryExtractJobStatus(**payload)


@router.post(
    "/llm/complete",
    response_model=SDKLLMCompleteResponse,
    summary="Smart-routed LLM completion",
    description=(
        "Tier-aware LLM completion. Consumers describe the task "
        "(`task_type`, `cost_sensitivity`); the smart_router selects the "
        "best model from FREE / CHEAP / CAPABLE / RESEARCH / EXPERT tiers, "
        "preferring Ollama when available. Returns content plus the model "
        "actually used and an estimated cost-per-1K-tokens for budget tracking."
    ),
    responses={422: _422, 503: _503},
)
async def sdk_llm_complete(req: SDKLLMCompleteRequest) -> SDKLLMCompleteResponse:
    from core.routing.smart_router import (  # noqa: F401  (EXPERT_MODELS import for cost lookup)
        EXPERT_MODELS,
        BudgetUnsatisfiableError,
    )
    from core.utils.llm_client import route_and_call

    try:
        content, decision = await route_and_call(
            messages=req.messages,
            query=req.query or (req.messages[-1].get("content", "")[:200] if req.messages else ""),
            task_type=req.task_type,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            response_format=req.response_format,
            cost_sensitivity=req.cost_sensitivity,
            slo_budget_ms=req.slo_budget_ms,
        )
    except BudgetUnsatisfiableError as exc:
        # Fail fast so the caller can route to direct providers — silently
        # downgrading the tier would hide a quality drop. Retry-After is in
        # seconds (rounded up from ms) per RFC 7231 § 7.1.3.
        retry_after_s = max(1, (exc.retry_after_ms + 999) // 1000)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "slo_budget_exceeded",
                "message": str(exc),
                "retry_after_ms": exc.retry_after_ms,
                "floor_p95_ms": exc.floor_p95_ms,
                "eligible_tier": exc.eligible_tier,
            },
            headers={"Retry-After": str(retry_after_s)},
        )
    return SDKLLMCompleteResponse(
        content=content,
        model=decision.model,
        provider=decision.provider,
        reason=decision.reason,
        estimated_cost_per_1k=decision.estimated_cost_per_1k,
        tier_p95_ms=decision.tier_p95_ms,
    )


@router.get(
    "/health",
    response_model=SDKHealthResponse,
    summary="Health Check",
    description="Service connectivity and consumer-relevant feature flags. "
    "Returns 'healthy' when all services are connected, 'degraded' otherwise.",
)
def sdk_health():
    from config.features import FEATURE_TOGGLES

    base = health_check()
    base["version"] = SDK_VERSION
    base["features"] = {
        k: v for k, v in FEATURE_TOGGLES.items()
        if k in (
            "enable_hallucination_check",
            "enable_feedback_loop",
            "enable_self_rag",
            "enable_memory_extraction",
        )
    }
    # Expose internal LLM provider info for SDK consumers
    import os
    base["internal_llm"] = {
        "provider": config.INTERNAL_LLM_PROVIDER,
        "model": config.INTERNAL_LLM_MODEL or config.OLLAMA_DEFAULT_MODEL,
        "ollama_enabled": os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1"),
    }
    return base


# ---------------------------------------------------------------------------
# Ingest endpoints
# ---------------------------------------------------------------------------


@router.post("/ingest", summary="Ingest Text", responses={422: _422, 503: _503})
def sdk_ingest(req: dict):
    result = ingest_content(
        req.get("content", ""),
        domain=req.get("domain", "general"),
        metadata={"tags": req.get("tags", "")},
    )
    return result


@router.post("/ingest/file", summary="Ingest File", responses={422: _422, 503: _503})
async def sdk_ingest_file(req: dict):
    result = await ingest_file(
        req.get("file_path", ""),
        domain=req.get("domain", ""),
        tags=req.get("tags", ""),
    )
    return result


# ---------------------------------------------------------------------------
# Collections / Taxonomy / Search
# ---------------------------------------------------------------------------


@router.get("/collections", summary="List Collections")
def sdk_collections():
    return list_collections()


@router.get("/taxonomy", summary="Domain Taxonomy")
def sdk_taxonomy():
    return {"domains": list(DOMAINS), "taxonomy": dict(TAXONOMY)}


@router.get("/health/detailed", summary="Detailed Health")
def sdk_health_detailed():
    return degradation_status()


@router.get("/settings", summary="SDK Settings")
def sdk_settings():
    return {"version": SDK_VERSION, "tier": FEATURE_TIER, "features": dict(FEATURE_FLAGS)}


@router.post("/search", summary="KB Search", responses={422: _422, 503: _503})
def sdk_search(req: dict):
    result = query_knowledge(
        req.get("query", ""),
        domain=req.get("domain", "general"),
        top_k=req.get("top_k", 3),
    )
    sources = result.get("sources", [])
    return {"results": sources, "total_results": len(sources), "confidence": result.get("confidence", 0.0)}


@router.get("/plugins", summary="List Plugins")
def sdk_plugins():
    result = list_plugins()
    return {"plugins": [p.model_dump() for p in result.plugins], "total": result.total}
